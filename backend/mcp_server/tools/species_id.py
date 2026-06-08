"""Species identification.

Pipeline:
1. Pl@ntNet `/identify/all` returns ranked candidates. We accept the top
   candidate if its score is decisively higher than the runner-up.
2. If candidates are close (< gap_threshold), Gemini Flash tie-breaks
   by examining the same image against the candidate names.
3. If Pl@ntNet is unavailable / returns nothing, we fall back to a
   single Gemini Flash call ("identify this tree species").
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from ...config import CONFIG
from ...gemini_client import generate_structured

logger = logging.getLogger(__name__)


PLANTNET_URL = "https://my-api.plantnet.org/v2/identify/all"
TIEBREAK_GAP = 0.10  # if top two candidates are within 10%, ask Gemini


# ── Schemas for the Gemini fallback / tiebreaker ────────────────────────
class SpeciesGuess(BaseModel):
    scientific_name: str = Field(..., description="Best-guess scientific name, genus + species.")
    common_name: str = Field(..., description="Best-guess common name, empty if unknown.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(..., description="One sentence pointing to visible morphology cues.")


class TiebreakResponse(BaseModel):
    chosen: str = Field(..., description="Scientific name of the chosen candidate.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str


# ── Pl@ntNet call ───────────────────────────────────────────────────────
async def _plantnet_identify(image_bytes: bytes) -> Optional[list[dict[str, Any]]]:
    if not CONFIG.plantnet_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                PLANTNET_URL,
                params={"api-key": CONFIG.plantnet_api_key, "no-reject": "true"},
                files={"images": ("crown.jpg", image_bytes, "image/jpeg")},
                data={"organs": "leaf"},
            )
        if resp.status_code != 200:
            logger.warning("Pl@ntNet returned %s: %s", resp.status_code, resp.text[:200])
            return None
        results = resp.json().get("results", [])
        return [
            {
                "scientific_name": r["species"].get("scientificNameWithoutAuthor", ""),
                "common_names": r["species"].get("commonNames", []),
                "score": r.get("score", 0.0),
            }
            for r in results[:5]
        ]
    except Exception as e:
        logger.warning("Pl@ntNet call failed: %s", e)
        return None


# ── Gemini fallback (no Pl@ntNet candidates available) ──────────────────
_FALLBACK_PROMPT = (
    "Identify the tree species visible in this image. Use only morphological "
    "cues that are actually visible (leaf shape, bark texture, crown habit, "
    "needle arrangement, etc). If you cannot identify confidently, say so "
    "by setting confidence below 0.4 and using a genus-only or 'tree' label."
)


async def _gemini_fallback(image_base64: str) -> dict[str, Any]:
    guess: SpeciesGuess = generate_structured(  # type: ignore[assignment]
        model=CONFIG.flash_model,
        prompt=_FALLBACK_PROMPT,
        response_schema=SpeciesGuess,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return {
        "species": guess.scientific_name or "tree",
        "common_name": guess.common_name,
        "confidence": guess.confidence,
        "source": "gemini",
        "rationale": guess.rationale,
        "alternatives": [],
    }


# ── Gemini tiebreaker (Pl@ntNet candidates are close) ───────────────────
def _tiebreak_prompt(candidates: list[dict[str, Any]]) -> str:
    lines = [
        f"{i + 1}. {c['scientific_name']} (Pl@ntNet score {c['score']:.2f})"
        for i, c in enumerate(candidates)
    ]
    return (
        "Pl@ntNet returned the following candidate species for this tree, "
        "with scores too close to choose confidently:\n"
        + "\n".join(lines)
        + "\n\nExamine the image and pick the single best match. Your 'chosen' "
          "field must be the scientific name from one of the candidates above."
    )


async def _gemini_tiebreak(
    image_base64: str, candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    result: TiebreakResponse = generate_structured(  # type: ignore[assignment]
        model=CONFIG.flash_model,
        prompt=_tiebreak_prompt(candidates),
        response_schema=TiebreakResponse,
        image_base64=image_base64,
        temperature=0.05,
        max_output_tokens=400,
    )
    chosen = next(
        (c for c in candidates if c["scientific_name"] == result.chosen),
        candidates[0],
    )
    return {
        "species": chosen["scientific_name"],
        "common_name": (chosen.get("common_names") or [""])[0],
        "confidence": result.confidence,
        "source": "plantnet+gemini",
        "rationale": result.rationale,
        "alternatives": [
            {"species": c["scientific_name"], "score": c["score"]}
            for c in candidates if c["scientific_name"] != chosen["scientific_name"]
        ],
    }


# ── Public entry point ──────────────────────────────────────────────────
async def identify_species(image_base64: str) -> dict[str, Any]:
    """Return the best species ID for the given image.

    Returns:
        {
            "species": str,           # scientific name, or "tree" if unknown
            "common_name": str,
            "confidence": float,
            "source": "plantnet" | "plantnet+gemini" | "gemini",
            "rationale": str,
            "alternatives": [{species, score}, ...]
        }
    """
    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as e:
        logger.warning("identify_species got undecodable image: %s", e)
        return {
            "species": "tree", "common_name": "", "confidence": 0.0,
            "source": "error", "rationale": "image decode failed",
            "alternatives": [],
        }

    candidates = await _plantnet_identify(image_bytes)

    if not candidates:
        return await _gemini_fallback(image_base64)

    top = candidates[0]
    runner_up_score = candidates[1]["score"] if len(candidates) > 1 else 0.0

    if top["score"] - runner_up_score >= TIEBREAK_GAP:
        return {
            "species": top["scientific_name"],
            "common_name": (top.get("common_names") or [""])[0],
            "confidence": top["score"],
            "source": "plantnet",
            "rationale": "Pl@ntNet top candidate with decisive score gap.",
            "alternatives": [
                {"species": c["scientific_name"], "score": c["score"]}
                for c in candidates[1:]
            ],
        }

    return await _gemini_tiebreak(image_base64, candidates)
