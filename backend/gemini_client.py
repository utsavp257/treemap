"""Thin wrapper around google-genai with structured-output helpers.

All Gemini calls go through this module. It centralizes:
- API client construction
- JSON-schema-validated structured output
- Honest error classification (no more 'QUOTA EXHAUSTED on every error')
"""

from __future__ import annotations

import base64
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Type, TypeVar

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from pydantic import BaseModel, ValidationError

from .config import CONFIG
from .errors import (
    GeminiInvalidResponse,
    GeminiQuotaExhausted,
    GeminiTransportError,
)

logger = logging.getLogger(__name__)

_DEBUG_DIR = Path(tempfile.gettempdir()) / "rootcause-debug"


def _dump_for_inspection(raw: str, finish_reason: str, model: str) -> Path:
    """Write the full raw response to disk so we can inspect parse failures."""
    _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = _DEBUG_DIR / f"gemini_{int(time.time() * 1000)}_{model.replace('/', '_')}.txt"
    path.write_text(
        f"# finish_reason: {finish_reason}\n"
        f"# model: {model}\n"
        f"# bytes: {len(raw)}\n"
        f"# ---\n{raw}\n"
    )
    return path


T = TypeVar("T", bound=BaseModel)


client = genai.Client(api_key=CONFIG.gemini_api_key)
_client = client  # backwards-compatible alias


def _image_part(image_base64: str, mime_type: str = "image/jpeg") -> types.Part:
    return types.Part.from_bytes(
        data=base64.b64decode(image_base64),
        mime_type=mime_type,
    )


def _classify(exc: Exception) -> Exception:
    """Map SDK exceptions to our typed error hierarchy."""
    msg = str(exc).lower()
    if isinstance(exc, genai_errors.APIError):
        if exc.code == 429 or "quota" in msg or "rate" in msg:
            return GeminiQuotaExhausted(str(exc))
        return GeminiTransportError(str(exc))
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return GeminiTransportError(str(exc))
    return GeminiInvalidResponse(str(exc))


def generate_structured(
    *,
    model: str,
    prompt: str,
    response_schema: Type[T] | list,
    image_base64: str | None = None,
    images: list[str] | None = None,
    image_mime_type: str = "image/jpeg",
    temperature: float = 0.1,
    max_output_tokens: int = 2048,
    thinking_budget: int | None = None,
) -> T | list:
    """Call Gemini with a structured-output contract.

    Pass either `image_base64` for a single image, or `images` for a
    multi-image call (e.g. target + reference panel). The base64 strings
    must NOT carry a `data:` prefix.

    `thinking_budget`:
      - None  → SDK default (thinking enabled on 2.5+/3.x models)
      - 0     → thinking DISABLED — model goes straight to output. Use for
                triage / quick classification calls where the structured
                output is the entire task; thinking just eats the
                max_output_tokens budget and causes truncation.
      - >0    → explicit cap on thinking tokens

    Returns a parsed Pydantic instance (or list of instances) matching
    `response_schema`. Raises a typed GeminiError on any failure.
    """
    parts: list[types.Part | str] = []
    if image_base64 and images:
        raise ValueError("Pass either `image_base64` or `images`, not both.")
    for b64 in (images or ([image_base64] if image_base64 else [])):
        parts.append(_image_part(b64, image_mime_type))
    parts.append(prompt)

    config_kwargs: dict[str, Any] = dict(
        response_mime_type="application/json",
        response_schema=response_schema,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_budget=thinking_budget
        )
    config = types.GenerateContentConfig(**config_kwargs)

    try:
        response = _client.models.generate_content(
            model=model,
            contents=parts,
            config=config,
        )
    except Exception as e:
        raise _classify(e) from e

    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed

    # SDK didn't auto-parse — fall back to raw text + manual decode.
    raw = (response.text or "").strip()
    if not raw:
        raise GeminiInvalidResponse("Gemini returned an empty response.")

    # Strip common markdown fences Pro sometimes emits even with json mime type.
    if raw.startswith("```"):
        raw = raw.lstrip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.rstrip("`").strip()

    # If the model wrapped the JSON in prose, slice to the first/last bracket.
    def _slice_to_json(s: str) -> str:
        first_obj = s.find("{")
        first_arr = s.find("[")
        starts = [i for i in (first_obj, first_arr) if i != -1]
        if not starts:
            return s
        start = min(starts)
        end = max(s.rfind("}"), s.rfind("]"))
        if end <= start:
            return s
        return s[start : end + 1]

    candidate = _slice_to_json(raw)

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        finish = ""
        try:
            finish = response.candidates[0].finish_reason.name  # type: ignore[union-attr]
        except Exception:
            pass

        dump_path = _dump_for_inspection(raw, finish, model)
        logger.warning(
            "Gemini parse failed (finish=%s, %d bytes). Full response dumped to: %s",
            finish, len(raw), dump_path,
        )

        hint = ""
        if finish == "MAX_TOKENS":
            hint = " (output truncated by max_output_tokens — increase it)"
        elif finish == "SAFETY":
            hint = " (response blocked by safety filter)"
        elif finish == "RECITATION":
            hint = " (response blocked by recitation filter)"

        raise GeminiInvalidResponse(
            f"Gemini returned non-JSON{hint}. Full dump: {dump_path}. Preview: {candidate[:200]}"
        ) from e

    try:
        if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
            return response_schema.model_validate(data)
        # list[Model] schema
        if (
            isinstance(response_schema, type(list[int]))  # generic alias
            or (hasattr(response_schema, "__origin__") and response_schema.__origin__ is list)
        ):
            inner = response_schema.__args__[0]
            return [inner.model_validate(item) for item in data]
    except ValidationError as e:
        raise GeminiInvalidResponse(f"Response failed schema validation: {e}") from e

    raise GeminiInvalidResponse(
        f"Unsupported response_schema shape: {response_schema!r}"
    )
