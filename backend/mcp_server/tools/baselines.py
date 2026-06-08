"""Bundled-data tools: species baselines + soil suitability.

Two-tier lookup:
  1. Bundled JSON (instant, no API call) — covers commonly-encountered
     temperate species.
  2. Gemini fallback (one Flash call, ~1-2s, cached in-process) — covers
     anything not in the JSON. Returned baselines are tagged with their
     source so the agent can weight them.

This is what makes the app scalable to any forest. The JSON is a fast
path, not a coverage requirement.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ...config import CONFIG
from ...gemini_client import generate_structured

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# In-process caches. Survive across calls within one MCP server lifetime.
# Keyed by the *raw* species string the agent passed (lowercased) so
# common-name and scientific-name lookups don't collide.
_GEMINI_BASELINE_CACHE: dict[str, dict[str, Any]] = {}
_GEMINI_SOIL_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


# ── Loaders ──────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _baselines() -> dict[str, Any]:
    with open(_DATA_DIR / "species_baselines.json", "r") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _soil() -> dict[str, Any]:
    with open(_DATA_DIR / "soil_suitability.json", "r") as f:
        return json.load(f)


# ── Schemas for Gemini-generated baselines ───────────────────────────────
SpeciesGroup = Literal["conifer", "broadleaf_deciduous", "broadleaf_evergreen", "palm"]
SoilTexture = Literal[
    "sandy", "sandy_loam", "loam", "silt_loam",
    "clay_loam", "clay", "chalky", "peaty",
]


class GeneratedBaseline(BaseModel):
    common_names: list[str] = Field(..., description="Widely-used common names.")
    group: SpeciesGroup
    mature_crown_radius_m: list[float] = Field(
        ..., min_length=2, max_length=2,
        description="[min, max] crown radius in meters for a healthy mature specimen.",
    )
    canopy_density_pct_healthy: list[int] = Field(
        ..., min_length=2, max_length=2,
        description="[min, max] canopy density percentage from a top-down view.",
    )
    leaf_hue_range_hsv_deg: list[int] = Field(
        ..., min_length=2, max_length=2,
        description="[min, max] HSV hue in degrees. Healthy green foliage is roughly 70-115.",
    )
    bark_texture: str = Field(..., description="One-line descriptor of mature bark.")
    common_diseases: list[str] = Field(
        default_factory=list,
        description="Well-known diseases and pests for this species.",
    )
    preferred_soil_textures: list[SoilTexture] = Field(default_factory=list)
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Your confidence that this species exists as described. Be honest.",
    )


class GeneratedSoilScore(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    rating: Literal["ideal", "acceptable", "stressed", "poor"]
    rationale: str
    confidence: float = Field(..., ge=0.0, le=1.0)


# ── Name normalization ──────────────────────────────────────────────────
def _normalize(species: str) -> Optional[str]:
    """Find an exact bundled match. Returns canonical scientific name or None."""
    if not species:
        return None
    table = _baselines()["species"]
    if species in table:
        return species

    query = species.lower().strip()
    for canonical, info in table.items():
        if canonical.lower() == query:
            return canonical
        for common in info.get("common_names", []):
            if common.lower() == query:
                return canonical

    # Genus-level fallback (e.g. "Quercus sp." → first Quercus entry)
    genus = query.split()[0] if query else ""
    if genus and len(genus) > 2:
        for canonical in table:
            if canonical.lower().startswith(genus):
                return canonical

    return None


# ── Gemini fallback: baseline ────────────────────────────────────────────
_BASELINE_PROMPT = """You are a forestry reference compiler. Produce a trait baseline for a healthy mature specimen of this tree species, in a strict JSON schema.

Species name: "{species}"

Rules:
- If you recognize the species, give realistic mid-range values from published forestry sources.
- If the name is ambiguous, pick the most common species matching it and note the assumption in common_names.
- If you don't recognize it at all, return a conservative generic broadleaf_deciduous baseline with confidence < 0.3.
- leaf_hue_range_hsv_deg is HSV hue in degrees: ~70-115 for typical green foliage, lower (60-90) for some olive/silvery species, higher (80-130) for blue-green conifers.
- preferred_soil_textures must be drawn from: sandy, sandy_loam, loam, silt_loam, clay_loam, clay, chalky, peaty.
- common_diseases should list real, named pathogens or pests for this species, not generic terms like "fungal infection".
"""


async def _gemini_generate_baseline(species: str) -> dict[str, Any]:
    cache_key = species.lower().strip()
    if cache_key in _GEMINI_BASELINE_CACHE:
        return _GEMINI_BASELINE_CACHE[cache_key]

    try:
        result: GeneratedBaseline = await asyncio.to_thread(
            generate_structured,
            model=CONFIG.flash_model,
            prompt=_BASELINE_PROMPT.format(species=species),
            response_schema=GeneratedBaseline,
            temperature=0.1,
            max_output_tokens=1024,
        )
    except Exception as e:
        logger.warning("Baseline Gemini fallback failed for '%s': %s", species, e)
        # Last-ditch: return the bundled generic 'tree' anchor.
        generic = _baselines()["species"]["tree"].copy()
        generic.update({
            "scientific_name": species,
            "matched": False,
            "source": "fallback_generic",
            "confidence": 0.1,
        })
        return generic

    payload = result.model_dump()
    payload["scientific_name"] = species
    payload["matched"] = True
    payload["source"] = "gemini"
    _GEMINI_BASELINE_CACHE[cache_key] = payload
    logger.info(
        "Gemini-generated baseline cached for '%s' (group=%s, conf=%.2f)",
        species, result.group, result.confidence,
    )
    return payload


# ── Public: lookup_species_baseline ──────────────────────────────────────
async def lookup_species_baseline(species: str) -> dict[str, Any]:
    """Return trait baseline. Bundled fast path → Gemini fallback for
    anything we don't have curated.
    """
    canonical = _normalize(species)
    if canonical is not None:
        entry = _baselines()["species"][canonical].copy()
        entry["scientific_name"] = canonical
        entry["matched"] = True
        entry["source"] = "bundled"
        return entry

    # Not in JSON — generate via Gemini (cached).
    return await _gemini_generate_baseline(species)


# ── Gemini fallback: soil suitability ────────────────────────────────────
_SOIL_PROMPT = """You are a forestry expert. Score how well this species grows on this soil texture.

Species: "{species}"
Soil texture: "{soil_type}"

Return a JSON object with:
- score: 0.0-1.0 (1.0 = ideal, 0.0 = unviable)
- rating: one of "ideal" (>=0.8), "acceptable" (0.6-0.8), "stressed" (0.4-0.6), "poor" (<0.4)
- rationale: one sentence on why
- confidence: 0.0-1.0 — be honest about uncertainty if you don't know the species well.
"""


async def _gemini_soil_score(species: str, soil_type: str) -> dict[str, Any]:
    cache_key = (species.lower().strip(), soil_type.lower().strip())
    if cache_key in _GEMINI_SOIL_CACHE:
        return _GEMINI_SOIL_CACHE[cache_key]

    try:
        result: GeneratedSoilScore = await asyncio.to_thread(
            generate_structured,
            model=CONFIG.flash_model,
            prompt=_SOIL_PROMPT.format(species=species, soil_type=soil_type),
            response_schema=GeneratedSoilScore,
            temperature=0.1,
            max_output_tokens=512,
        )
    except Exception as e:
        logger.warning("Soil Gemini fallback failed for (%s, %s): %s", species, soil_type, e)
        return {
            "species": species, "soil_type": soil_type,
            "score": None, "rating": "unknown",
            "notes": f"Lookup failed: {e}",
            "source": "fallback_error",
        }

    payload = {
        "species": species,
        "soil_type": soil_type,
        "score": result.score,
        "rating": result.rating,
        "notes": result.rationale,
        "source": "gemini",
        "confidence": result.confidence,
    }
    _GEMINI_SOIL_CACHE[cache_key] = payload
    return payload


# ── Public: get_soil_suitability ─────────────────────────────────────────
async def get_soil_suitability(species: str, soil_type: str) -> dict[str, Any]:
    canonical = _normalize(species)
    soil_key = soil_type.lower().replace("-", "_").replace(" ", "_")

    if canonical is not None:
        matrix = _soil()["matrix"]
        row = matrix.get(canonical) or matrix["tree"]
        score = row.get(soil_key)
        if score is not None:
            if score >= 0.8:
                rating = "ideal"
            elif score >= 0.6:
                rating = "acceptable"
            elif score >= 0.4:
                rating = "stressed"
            else:
                rating = "poor"
            return {
                "species": canonical,
                "soil_type": soil_key,
                "score": score,
                "rating": rating,
                "notes": f"{rating.capitalize()} pairing — {canonical} on {soil_key}.",
                "source": "bundled",
            }

    # Either species or soil_type not in matrix — Gemini it.
    return await _gemini_soil_score(species, soil_type)
