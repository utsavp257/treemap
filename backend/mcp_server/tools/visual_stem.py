"""Atomic visual QA tools — stem (trunk/bark) perspective.

Stem-level symptoms are the early-warning signals crown imagery misses:
pitch tubes, galleries, cankers, mycelial fans, resin flow, frass. Each
tool answers one focused question about a stem close-up.

Inputs:
    image_base64 — close-up of the tree TRUNK (bark visible at hand-arm
                   distance, ideally the lower 0-3m of the trunk).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ...config import CONFIG
from ...gemini_client import generate_structured


# ── Schemas ──────────────────────────────────────────────────────────────
class BarkTextureAssessment(BaseModel):
    observed_texture: str = Field(..., description="Short descriptor: 'smooth grey', 'deeply furrowed', 'peeling plates', etc.")
    matches_baseline: bool = Field(..., description="True if the bark looks consistent with the species baseline (if known).")
    anomalies: list[str] = Field(default_factory=list, description="Visible deviations: peeling, cracks, lesions, discoloration, swelling.")
    notes: str


class PitchTubeDetection(BaseModel):
    present: bool
    count_estimate: int = Field(..., ge=0)
    severity: Literal["none", "few", "many"]
    notes: str


class GalleryDetection(BaseModel):
    present: bool
    pattern: Literal["none", "linear", "branching", "scattered", "engraved"]
    notes: str


class CankerDetection(BaseModel):
    present: bool
    count_estimate: int = Field(..., ge=0)
    type_guess: Literal["unknown", "perennial", "annual", "diffuse"]
    notes: str


class MycelialFanDetection(BaseModel):
    present: bool
    color: str = Field(..., description="Short label: 'white', 'cream', 'dark', or '' if not present.")
    location: Literal["unknown", "below_bark", "at_base", "on_trunk", "none"]
    notes: str


class ResinFlowDetection(BaseModel):
    present: bool
    severity: Literal["none", "trace", "moderate", "heavy"]
    color: str = Field(..., description="'clear', 'amber', 'dark', or '' if not present.")
    notes: str


class FrassDetection(BaseModel):
    present: bool
    color: str = Field(..., description="'pale', 'reddish', 'dark', or '' if not present.")
    location: Literal["unknown", "trunk", "base", "branches", "none"]
    notes: str


# ── Prompt helper ────────────────────────────────────────────────────────
def _bark_baseline_clause(baseline: Optional[dict[str, Any]]) -> str:
    if not baseline:
        return ""
    descriptor = baseline.get("bark_texture")
    if not descriptor:
        return ""
    name = baseline.get("scientific_name") or "this species"
    return (
        f"\n\nHealthy {name} bark is typically: {descriptor}. "
        "Set matches_baseline=true if the visible bark is consistent with this."
    )


# ── Tool functions ───────────────────────────────────────────────────────
async def assess_bark_texture(
    image_base64: str,
    baseline: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    prompt = (
        "Describe the bark texture visible in this trunk close-up. Report a "
        "short observed_texture descriptor, whether it matches the species "
        "baseline (if provided), and any anomalies — peeling, cracking, "
        "splitting, swelling, lesions, blackened patches, or unusual "
        "discoloration. Only list anomalies that are clearly visible."
        + _bark_baseline_clause(baseline)
    )
    result: BarkTextureAssessment = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=BarkTextureAssessment,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_pitch_tubes(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this trunk close-up for pitch tubes — small popcorn-sized "
        "blobs of hardened resin extruded from beetle entry holes, usually "
        "white, cream, or pink, often surrounding a dark bore hole. Pitch "
        "tubes are a hallmark of bark beetle attack (Ips, Dendroctonus, etc.). "
        "Report presence, count_estimate, and severity. Pinkish discoloration "
        "from bark alone is NOT a pitch tube — only count discrete extruded "
        "resin masses."
    )
    result: PitchTubeDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=PitchTubeDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_galleries(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this trunk close-up for visible insect galleries — the "
        "engraved tunnel patterns left in cambium or outer wood by bark "
        "beetles, ambrosia beetles, or wood-boring larvae. Galleries are "
        "usually exposed only when bark has flaked away. Report presence "
        "and pattern (linear like Ips, branching like Scolytus, scattered, "
        "or engraved with characteristic radial pattern). Set pattern='none' "
        "if no galleries are visible."
    )
    result: GalleryDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=GalleryDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_cankers(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this trunk close-up for cankers — localized, sunken, often "
        "discolored areas of dead bark/cambium caused by fungal or bacterial "
        "infection. Cankers may be ringed by callus tissue (perennial), "
        "appear as a single dark patch (annual), or spread diffusely without "
        "clear borders. Report presence, rough count, and type guess. Avoid "
        "calling natural bark furrows or healed pruning scars cankers."
    )
    result: CankerDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=CankerDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_mycelial_fans(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this trunk close-up for mycelial fans — flat, white-to-cream "
        "sheets of fungal tissue, characteristic of Armillaria (honey fungus) "
        "and a few other root/butt rot fungi. They are usually visible only "
        "beneath flaked or peeled bark, often near the trunk base. Report "
        "presence, color, and location. If absent, set location='none' and "
        "color=''."
    )
    result: MycelialFanDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=MycelialFanDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_resin_flow(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this trunk close-up for resin flow — clear-to-amber-to-dark "
        "sap exuded from the trunk. Normal background resin is minimal; "
        "heavy streaming flow indicates wounding, bark beetle attack, or "
        "vascular stress. Distinguish from pitch tubes (discrete blobs) "
        "and from sticky honeydew (insect excretion). Report presence, "
        "severity, and color. If absent, set color=''."
    )
    result: ResinFlowDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=ResinFlowDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()


async def detect_frass(image_base64: str) -> dict[str, Any]:
    prompt = (
        "Examine this trunk close-up for frass — sawdust-like insect "
        "excrement or boring dust accumulated near entry holes, in bark "
        "crevices, or at the tree base. Frass is a hallmark of active "
        "wood-boring insect activity. Report presence, color (pale = "
        "fresh wood; reddish/dark = older or species-specific), and "
        "location. If absent, set color='' and location='none'."
    )
    result: FrassDetection = await asyncio.to_thread(
        generate_structured,
        model=CONFIG.flash_model,
        prompt=prompt,
        response_schema=FrassDetection,
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=400,
    )
    return result.model_dump()
