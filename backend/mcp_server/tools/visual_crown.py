"""Atomic visual QA tools — crown perspective.

Each function is a focused Gemini 2.5 Flash call with a tight prompt and
a strict Pydantic response schema. The agent assembles these answers
into the final evidence trace; no individual tool draws a diagnosis.

Inputs:
    image_base64 — close-up of the tree CROWN (top-down or oblique).
    baseline    — optional dict from `lookup_species_baseline`. If
                  supplied, the prompt embeds the healthy reference
                  range so the model grounds its answer.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ...config import CONFIG
from ...gemini_client import generate_structured


# ── Schemas ──────────────────────────────────────────────────────────────
class LeafColorAssessment(BaseModel):
    dominant_hue: str = Field(..., description='Short label: "deep green", "yellow-green", "brown-yellow", etc.')
    healthy_match: bool = Field(..., description="True if the observed hue is within the healthy range for the species (if known).")
    chlorosis_score: float = Field(..., ge=0.0, le=1.0, description="0 = none, 1 = severe yellowing.")
    necrosis_score: float = Field(..., ge=0.0, le=1.0, description="0 = none, 1 = extensive brown/dead tissue.")
    notes: str


class CanopyDensityAssessment(BaseModel):
    estimated_density_pct: int = Field(..., ge=0, le=100)
    healthy_match: bool = Field(..., description="True if density is within the species' healthy range (if known).")
    visible_gap_count: int = Field(..., ge=0)
    dieback_present: bool
    notes: str


class CanopyGapDetection(BaseModel):
    gap_count: int = Field(..., ge=0)
    gap_severity: Literal["none", "minor", "moderate", "severe"]
    pattern: Literal["none", "scattered", "clustered", "edge", "central"]
    notes: str


class DiebackDetection(BaseModel):
    dieback_present: bool
    severity: Literal["none", "mild", "moderate", "severe"]
    pattern: Literal["none", "crown_top", "outer_edges", "branches", "whole_crown"]
    notes: str


# ── Prompt builders ──────────────────────────────────────────────────────
def _baseline_clause_leaf_color(baseline: Optional[dict[str, Any]]) -> str:
    if not baseline:
        return ""
    rng = baseline.get("leaf_hue_range_hsv_deg")
    if not rng:
        return ""
    name = baseline.get("scientific_name") or "this species"
    return (
        f"\n\nHealthy {name} foliage typically falls in HSV hue {rng[0]}-{rng[1]}° "
        "(green-yellow-green range). Use this as your reference; deviation toward "
        "lower hues (yellow/brown) indicates chlorosis or necrosis."
    )


def _baseline_clause_canopy_density(baseline: Optional[dict[str, Any]]) -> str:
    if not baseline:
        return ""
    rng = baseline.get("canopy_density_pct_healthy")
    if not rng:
        return ""
    name = baseline.get("scientific_name") or "this species"
    return (
        f"\n\nHealthy {name} crowns typically show {rng[0]}-{rng[1]}% canopy density "
        "from a top-down view. Set healthy_match=true if estimated_density_pct "
        "lies within this range."
    )


# ── Tool functions ───────────────────────────────────────────────────────
async def assess_leaf_color(
    image_base64: str,
    baseline: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    prompt = (
        "Assess the foliage color visible in this tree crown image. "
        "Focus only on what is visible — do not infer disease."
        + _baseline_clause_leaf_color(baseline)
    )
    result: LeafColorAssessment = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=LeafColorAssessment,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def assess_canopy_density(
    image_base64: str,
    baseline: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    prompt = (
        "Estimate the canopy density of the tree crown visible in this image. "
        "Density = the percentage of the crown footprint visually filled by "
        "foliage when viewed from above. Count visible gaps in the canopy and "
        "report whether dieback (dead branches, missing tips) is present."
        + _baseline_clause_canopy_density(baseline)
    )
    result: CanopyDensityAssessment = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=CanopyDensityAssessment,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_canopy_gaps(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this tree crown image and characterise the visible gaps in "
        "the canopy. A gap is an opening through the crown where the ground, "
        "branches, or sky is clearly visible. Report the rough count, the "
        "severity (none/minor/moderate/severe), and the spatial pattern "
        "(scattered, clustered, concentrated at the edge, or concentrated "
        "centrally). If there are no gaps, set count=0, severity='none', "
        "pattern='none'."
    )
    result: CanopyGapDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=CanopyGapDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_dieback_pattern(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this tree crown image for dieback — branches that have lost "
        "foliage or are visibly dead/brown. Report whether dieback is present, "
        "the severity (none/mild/moderate/severe), and where it is concentrated:"
        " 'crown_top' (top-down dieback, common in advanced root disease), "
        "'outer_edges' (flagging, common in stress and bark beetles), "
        "'branches' (scattered branch death), 'whole_crown' (broad collapse), "
        "or 'none'."
    )
    result: DiebackDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=DiebackDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()
