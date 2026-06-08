"""MCP server — reference data + (Phase 3) atomic visual QA tools.

Started as a subprocess by the FastAPI backend on boot (see
backend/mcp_launcher.py) and communicates over stdio.

Phase 2 registers the reference-data tools only:
- identify_species          → Pl@ntNet + Gemini tiebreaker
- lookup_species_baseline   → bundled trait JSON
- get_soil_suitability      → bundled species×soil matrix
- get_healthy_reference_images   → iNaturalist
- get_disease_reference_images   → iNaturalist

Phase 3 will register the atomic visual QA tools alongside these,
each implemented as a focused Gemini 2.5 Flash call.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .tools import (
    baselines,
    references,
    species_id,
    visual_compare,
    visual_crown,
    visual_stem,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s mcp.%(name)s — %(message)s",
)
logger = logging.getLogger("server")


mcp = FastMCP("rootcause-forest-tools")


# ── Reference data tools ────────────────────────────────────────────────
@mcp.tool()
async def identify_species(image_base64: str) -> dict[str, Any]:
    """Identify the tree species visible in an image.

    Args:
        image_base64: JPEG/PNG image encoded as base64 (no data: prefix).

    Returns:
        Dict with keys: species (scientific name), common_name, confidence
        (0-1), source ("plantnet" | "plantnet+gemini" | "gemini"),
        rationale, alternatives (list of less-likely candidates).
    """
    return await species_id.identify_species(image_base64)


@mcp.tool()
async def lookup_species_baseline(species: str) -> dict[str, Any]:
    """Return the trait baseline for a species (mature crown radius range,
    healthy canopy density range, leaf hue range, bark texture descriptor,
    common diseases, preferred soil textures).

    Args:
        species: Scientific or common name. Hits the bundled JSON for
            common species (instant); falls back to a Gemini-generated
            baseline for anything else (cached after first call).
    """
    return await baselines.lookup_species_baseline(species)


@mcp.tool()
async def get_soil_suitability(species: str, soil_type: str) -> dict[str, Any]:
    """Score a species against a soil texture class.

    Args:
        species: Scientific or common name.
        soil_type: USDA texture class (sandy, sandy_loam, loam, silt_loam,
            clay_loam, clay, chalky, peaty).

    Returns:
        Dict with score (0-1), rating (ideal/acceptable/stressed/poor), notes.
        Bundled fast path for common species + soil_types; Gemini fallback
        for anything else (cached).
    """
    return await baselines.get_soil_suitability(species, soil_type)


@mcp.tool()
async def get_healthy_reference_images(
    species: str,
    view: Literal["crown", "leaf", "stem", "any"] = "any",
    season: Literal["spring", "summer", "autumn", "winter", "any"] = "any",
    limit: int = 6,
) -> dict[str, Any]:
    """Fetch healthy reference photos from iNaturalist for visual anchoring.

    The agent uses these as healthy templates when scoring an observed
    tree's deviation from baseline.
    """
    return await references.get_healthy_reference_images(
        species=species, view=view, season=season, limit=limit
    )


@mcp.tool()
async def get_disease_reference_images(
    disease: str,
    species: str | None = None,
    limit: int = 4,
) -> dict[str, Any]:
    """Fetch reference photos showing a specific disease/pathology.

    Args:
        disease: Common or scientific name of the disease/pest
            (e.g. "Armillaria root rot", "bark beetle", "Phytophthora").
        species: Optional host species filter.
    """
    return await references.get_disease_reference_images(
        disease=disease, species=species, limit=limit
    )


# ── Atomic visual QA — crown side ────────────────────────────────────────
@mcp.tool()
async def assess_leaf_color(
    image_base64: str,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score the foliage color of a crown crop.

    Returns dominant_hue, healthy_match, chlorosis_score (0-1),
    necrosis_score (0-1), notes.

    Pass the dict from lookup_species_baseline as `baseline` to ground
    the answer in species-specific healthy ranges.
    """
    return await visual_crown.assess_leaf_color(image_base64, baseline)


@mcp.tool()
async def assess_canopy_density(
    image_base64: str,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate canopy density (%) and flag dieback for a crown crop."""
    return await visual_crown.assess_canopy_density(image_base64, baseline)


@mcp.tool()
async def detect_canopy_gaps(image_base64: str) -> dict[str, Any]:
    """Count and characterise visible canopy gaps."""
    return await visual_crown.detect_canopy_gaps(image_base64)


@mcp.tool()
async def detect_dieback_pattern(image_base64: str) -> dict[str, Any]:
    """Identify the spatial pattern of crown dieback (top-down, edges, branches, etc.)."""
    return await visual_crown.detect_dieback_pattern(image_base64)


# ── Atomic visual QA — stem side ─────────────────────────────────────────
@mcp.tool()
async def assess_bark_texture(
    image_base64: str,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe bark texture and flag deviations from the species baseline."""
    return await visual_stem.assess_bark_texture(image_base64, baseline)


@mcp.tool()
async def detect_pitch_tubes(image_base64: str) -> dict[str, Any]:
    """Detect resin-extrusion pitch tubes (bark beetle hallmark)."""
    return await visual_stem.detect_pitch_tubes(image_base64)


@mcp.tool()
async def detect_galleries(image_base64: str) -> dict[str, Any]:
    """Detect bark beetle / borer galleries exposed under flaked bark."""
    return await visual_stem.detect_galleries(image_base64)


@mcp.tool()
async def detect_cankers(image_base64: str) -> dict[str, Any]:
    """Detect cankers on the trunk (fungal/bacterial bark lesions)."""
    return await visual_stem.detect_cankers(image_base64)


@mcp.tool()
async def detect_mycelial_fans(image_base64: str) -> dict[str, Any]:
    """Detect mycelial fans (Armillaria root rot hallmark)."""
    return await visual_stem.detect_mycelial_fans(image_base64)


@mcp.tool()
async def detect_resin_flow(image_base64: str) -> dict[str, Any]:
    """Detect abnormal resin streaming on the trunk."""
    return await visual_stem.detect_resin_flow(image_base64)


@mcp.tool()
async def detect_frass(image_base64: str) -> dict[str, Any]:
    """Detect insect frass (boring dust / sawdust) on or near the trunk."""
    return await visual_stem.detect_frass(image_base64)


# ── Mega-tools (batched parallel atomic checks) ──────────────────────────
#
# These run multiple atomic visual checks server-side via asyncio.gather
# and return a merged dict. From the orchestrator's perspective they
# count as ONE function call + ONE function response — saving 3-6 turns
# of accumulating context per tree vs invoking the atomic tools
# individually. Same per-atomic Gemini cost; far less orchestrator cost.
#
# Partial-failure tolerant: a sub-check that throws gets its slot
# replaced with {"error": "..."} so the rest still feed downstream.


import asyncio as _asyncio


def _ok_or_error(value: Any) -> dict[str, Any]:
    """Coerce an asyncio.gather slot result into a serializable dict."""
    if isinstance(value, Exception):
        return {"error": f"{type(value).__name__}: {value}"}
    if isinstance(value, dict):
        return value
    return {"value": value}


@mcp.tool()
async def examine_crown(
    image_base64: str,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ALL crown-side atomic checks in parallel and return a merged report.

    Equivalent to calling assess_leaf_color + assess_canopy_density +
    detect_canopy_gaps + detect_dieback_pattern in one shot. This is the
    PREFERRED tool for top-down/oblique crown images — it's far cheaper
    on the orchestrator side than four separate calls.

    Args:
        image_base64: the tree crown crop (auto-injected by orchestrator).
        baseline: optional species baseline from lookup_species_baseline,
            forwarded to the sub-checks that use it (leaf_color, density).

    Returns a dict with four keys (each either the sub-tool's result, or
    an {"error": "..."} stub if that specific check failed):
      - leaf_color
      - canopy_density
      - canopy_gaps
      - dieback
    """
    leaf, density, gaps, dieback = await _asyncio.gather(
        visual_crown.assess_leaf_color(image_base64, baseline),
        visual_crown.assess_canopy_density(image_base64, baseline),
        visual_crown.detect_canopy_gaps(image_base64),
        visual_crown.detect_dieback_pattern(image_base64),
        return_exceptions=True,
    )
    return {
        "leaf_color": _ok_or_error(leaf),
        "canopy_density": _ok_or_error(density),
        "canopy_gaps": _ok_or_error(gaps),
        "dieback": _ok_or_error(dieback),
    }


@mcp.tool()
async def examine_stem(
    image_base64: str,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ALL stem-side atomic checks in parallel and return a merged report.

    Equivalent to calling assess_bark_texture + detect_pitch_tubes +
    detect_galleries + detect_cankers + detect_mycelial_fans +
    detect_resin_flow + detect_frass in one shot. PREFERRED for
    low-altitude trunk/bark close-ups.

    Args:
        image_base64: trunk close-up (auto-injected).
        baseline: optional species baseline; forwarded to bark_texture.

    Returns a dict with seven keys (each either the sub-tool's result
    or {"error": "..."} on failure):
      - bark_texture
      - pitch_tubes
      - galleries
      - cankers
      - mycelial_fans
      - resin_flow
      - frass
    """
    bark, pitch, galleries, cankers, mycelial, resin, frass = await _asyncio.gather(
        visual_stem.assess_bark_texture(image_base64, baseline),
        visual_stem.detect_pitch_tubes(image_base64),
        visual_stem.detect_galleries(image_base64),
        visual_stem.detect_cankers(image_base64),
        visual_stem.detect_mycelial_fans(image_base64),
        visual_stem.detect_resin_flow(image_base64),
        visual_stem.detect_frass(image_base64),
        return_exceptions=True,
    )
    return {
        "bark_texture": _ok_or_error(bark),
        "pitch_tubes": _ok_or_error(pitch),
        "galleries": _ok_or_error(galleries),
        "cankers": _ok_or_error(cankers),
        "mycelial_fans": _ok_or_error(mycelial),
        "resin_flow": _ok_or_error(resin),
        "frass": _ok_or_error(frass),
    }


# ── Cross-cutting visual comparison ──────────────────────────────────────
@mcp.tool()
async def compare_to_reference(
    target_image_base64: str,
    reference_image_urls: list[str] | None = None,
    species: str | None = None,
    disease: str | None = None,
    focus: str = "overall condition",
    reference_type: str = "healthy",
    max_references: int = 4,
) -> dict[str, Any]:
    """Compare the target tree image against reference imagery.

    Preferred usage (one call, no separate fetch step):
      - reference_type="healthy" + species="Pinus halepensis"
        → fetches healthy refs and compares.
      - reference_type="disease" + disease="Seiridium canker" (+ optional species)
        → fetches disease refs and compares.

    Legacy: pass reference_image_urls directly when you already have them.

    Returns overall_similarity (0-1), matches_reference_type (bool),
    top_differences (list), summary.
    """
    if reference_type not in ("healthy", "disease"):
        reference_type = "healthy"
    return await visual_compare.compare_to_reference(
        target_image_base64=target_image_base64,
        reference_image_urls=reference_image_urls,
        species=species,
        disease=disease,
        focus=focus,
        reference_type=reference_type,  # type: ignore[arg-type]
        max_references=max_references,
    )


if __name__ == "__main__":
    logger.info("Starting RootCause MCP server (stdio transport)…")
    mcp.run(transport="stdio")
