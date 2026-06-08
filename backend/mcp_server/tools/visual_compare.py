"""Cross-cutting tool: compare a target image against reference images.

The agent calls this with a target tree crop and the URLs returned by
get_healthy_reference_images / get_disease_reference_images. The tool
fetches the reference images, packs everything into one multi-image
Gemini call, and returns ranked deltas.

This is the one tool that makes Gemini decisions reference-grounded:
"this canopy is 40% thinner than the healthy template" beats
"this canopy looks thin".
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Literal, Optional

import httpx
from pydantic import BaseModel, Field

from ...config import CONFIG
from ...gemini_client import generate_structured
from . import references as _references

logger = logging.getLogger(__name__)


# ── Schema ───────────────────────────────────────────────────────────────
class ReferenceDifference(BaseModel):
    aspect: str = Field(
        ...,
        description='Short label for the aspect being compared: "canopy density", "leaf color", "bark texture", etc.',
    )
    target_observation: str
    reference_observation: str
    significance: Literal["minor", "moderate", "major"]


class ReferenceComparison(BaseModel):
    overall_similarity: float = Field(..., ge=0.0, le=1.0)
    matches_reference_type: bool = Field(
        ...,
        description="True if the target appears to be the same species/condition as the references.",
    )
    top_differences: list[ReferenceDifference]
    summary: str


# ── Helpers ──────────────────────────────────────────────────────────────
async def _fetch_image_b64(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            logger.warning("Reference fetch %s -> %s", url, resp.status_code)
            return None
        return base64.b64encode(resp.content).decode("ascii")
    except Exception as e:
        logger.warning("Reference fetch %s failed: %s", url, e)
        return None


async def _fetch_all(urls: list[str], max_refs: int) -> list[str]:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_fetch_image_b64(client, u) for u in urls[:max_refs]])
    return [b64 for b64 in results if b64]


# ── Tool ─────────────────────────────────────────────────────────────────
async def compare_to_reference(
    target_image_base64: str,
    reference_image_urls: Optional[list[str]] = None,
    species: Optional[str] = None,
    disease: Optional[str] = None,
    focus: str = "overall condition",
    reference_type: Literal["healthy", "disease"] = "healthy",
    max_references: int = 4,
) -> dict[str, Any]:
    """Compare the target image against reference images.

    Reference acquisition is now one step instead of two — pass either:
      - reference_image_urls: pre-fetched URLs (legacy path), OR
      - species (reference_type='healthy') → fetch healthy refs internally, OR
      - disease + optional species (reference_type='disease') → fetch
        disease refs internally.

    Args:
        target_image_base64: Observed tree crop being evaluated.
        reference_image_urls: Optional pre-fetched URL list. When absent,
            the tool fetches its own refs based on species/disease.
        species: Tree species for the healthy-reference fetch.
        disease: Disease/pathology name for the disease-reference fetch.
        focus: Aspect to emphasise ("canopy density", "bark color", etc.).
        reference_type: "healthy" anchors against healthy templates;
            "disease" anchors against positive disease examples.
        max_references: Hard cap on refs Gemini sees.
    """
    # ── Acquire reference URLs if the caller didn't supply them ──────
    if not reference_image_urls:
        if reference_type == "healthy" and species:
            refs_payload = await _references.get_healthy_reference_images(
                species=species, limit=max_references
            )
            reference_image_urls = refs_payload.get("image_urls", []) or []
        elif reference_type == "disease" and disease:
            refs_payload = await _references.get_disease_reference_images(
                disease=disease, species=species, limit=max_references
            )
            reference_image_urls = refs_payload.get("image_urls", []) or []

    if not reference_image_urls:
        return {
            "overall_similarity": 0.0,
            "matches_reference_type": False,
            "top_differences": [],
            "summary": (
                "No reference images available — pass species (for "
                "healthy refs) or disease (for disease refs), or "
                "supply reference_image_urls directly."
            ),
        }

    ref_b64 = await _fetch_all(reference_image_urls, max_references)
    if not ref_b64:
        return {
            "overall_similarity": 0.0,
            "matches_reference_type": False,
            "top_differences": [],
            "summary": "All reference fetches failed; comparison skipped.",
        }

    if reference_type == "healthy":
        anchor_clause = (
            "The first image is the OBSERVED tree. The remaining images are "
            "HEALTHY reference photos of the same species. Compare the "
            "observed tree against the healthy reference set."
        )
        match_semantics = (
            "Set matches_reference_type=true if the observed tree appears "
            "broadly consistent with the healthy reference set."
        )
    else:
        anchor_clause = (
            "The first image is the OBSERVED tree. The remaining images are "
            "reference photos of a SPECIFIC disease/pathology. Compare and "
            "determine whether the observed tree shows the same condition."
        )
        match_semantics = (
            "Set matches_reference_type=true if the observed tree visibly "
            "exhibits the same pathology shown in the reference set."
        )

    prompt = (
        f"{anchor_clause} Emphasise the aspect: {focus}.\n\n"
        f"{match_semantics}\n\n"
        "overall_similarity is a 0-1 score: 1.0 = visually indistinguishable "
        "from the reference panel for the requested focus, 0.0 = completely "
        "unrelated. Populate top_differences with 1-4 entries describing the "
        "most significant deltas between observed and reference. "
        "Each difference must cite an observation of the target image AND a "
        "contrasting observation from the references."
    )

    images = [target_image_base64] + ref_b64
    result: ReferenceComparison = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=ReferenceComparison,
        images=images,
        temperature=0.15,
        max_output_tokens=900,
    )
    return result.model_dump()
