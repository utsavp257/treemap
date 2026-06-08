"""Tier-1 triage — a cheap single-call classifier.

Drone-detection over-flags "monitor" / "treat" / "cut" on perfectly fine
trees (motion blur, shadow, low contrast). Running the full agentic loop
on those false alarms wastes ~$0.05 per tree. Tier-1 is the in-between
step: one Gemini Flash-Lite call (~$0.0004) decides whether each flagged
tree is actually worth the deep dive.

Calibration is explicit in the prompt — false positives at this stage
trigger the expensive deep dive, so the model is asked to be conservative
about severity rather than inflate it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..config import CONFIG
from ..gemini_client import generate_structured
from ..schemas import DiagnosisCore

logger = logging.getLogger(__name__)


# ── Schema ──────────────────────────────────────────────────────────────
class Tier1Triage(BaseModel):
    """One-shot triage result. Feeds either the fast-path diagnosis (low
    severity) or escalates with annotated indicators to the full agent.
    """

    confirmed_unhealthy: bool = Field(
        ...,
        description="True if this tree is actually showing real stress / disease signs.",
    )
    severity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Calibrated severity score: "
            "0.0-0.2 false alarm, 0.2-0.4 minor variation, "
            "0.4-0.6 borderline, 0.6-0.8 clearly unhealthy, "
            "0.8-1.0 severely compromised."
        ),
    )
    primary_indicators: list[str] = Field(
        default_factory=list,
        description="1-3 short labels of the visible signs (or [] if no real issues).",
    )
    quick_assessment: str = Field(
        ...,
        description="One honest sentence on what's visible.",
    )
    suggested_action: str = Field(
        ...,
        description="One sentence on the next step.",
    )


# ── Prompt builder ──────────────────────────────────────────────────────
def _build_prompt(
    status: str,
    symptoms: list[str],
    species: Optional[str],
    baseline: Optional[dict[str, Any]],
    mode_hint: Optional[str],
    threshold: float,
) -> str:
    symptoms_str = ", ".join(symptoms) if symptoms else "none"
    species_str = species or "unknown"

    # Baseline anchor — give the model a number to calibrate against
    # without paying for the full baseline JSON.
    baseline_clause = ""
    if baseline:
        bits: list[str] = []
        density = baseline.get("canopy_density_pct_healthy")
        if isinstance(density, list) and len(density) == 2:
            bits.append(f"healthy canopy density {density[0]}-{density[1]}%")
        hue = baseline.get("leaf_hue_range_hsv_deg")
        if isinstance(hue, list) and len(hue) == 2:
            bits.append(f"leaf hue range {hue[0]}-{hue[1]}° HSV")
        common_diseases = baseline.get("common_diseases") or []
        if common_diseases:
            bits.append("common pathologies: " + ", ".join(common_diseases[:3]))
        if bits:
            baseline_clause = "\nSpecies reference: " + "; ".join(bits) + "."

    mode_clause = ""
    if mode_hint in ("crown", "satellite"):
        mode_clause = " (Top-down crown view.)"
    elif mode_hint == "stem":
        mode_clause = " (Low-altitude trunk/bark close-up.)"

    # The KEY structural change: calibration anchors + worked example
    # come FIRST. The noisy upstream detection appears LAST and is
    # explicitly framed as a hypothesis to verify, not as fact.
    return f"""TRIAGE TASK: Look at the image and score the tree's actual health.

Be brutally honest about calibration — the upstream drone detector OVER-FLAGS healthy trees ~70% of the time. Your job is to verify with FRESH EYES, not to confirm what the detector already said.

CALIBRATION ANCHORS:
- 0.0-0.2 — false alarm; tree looks fully healthy in the image
- 0.2-0.4 — minor variation, within normal range for the species
- 0.4-0.6 — borderline; some visible signs but unclear severity
- 0.6-0.8 — clearly unhealthy with visible stressors
- 0.8-1.0 — severely compromised, urgent attention needed

WORKED EXAMPLE:
  Detector says: monitor, symptoms ["chlorosis"]
  Image shows:   dense green canopy, no visible yellowing, normal density
  CORRECT severity = 0.10 — false alarm. DO NOT inflate to 0.5 just because
  the detector flagged it.

DECISION GATE:
- severity < {threshold:.2f} → your output IS the final diagnosis (no deeper analysis)
- severity ≥ {threshold:.2f} → primary_indicators are forwarded to the expensive deep-diagnosis agent

Each false positive (≥{threshold:.2f} when the image is actually fine) wastes ~$0.20.
Each false negative (<{threshold:.2f} when the tree is actually sick) lets a problem through.
Calibrate accordingly.

────────────────────────────────────────────────────────────────────
UPSTREAM DETECTION (treat as a NOISY HYPOTHESIS, not fact):
- Status flag: {status}
- Symptoms reported by detector: {symptoms_str}
- Species: {species_str}{mode_clause}{baseline_clause}
────────────────────────────────────────────────────────────────────

Now score the image. If the image disagrees with the detection, TRUST THE IMAGE."""


# ── Public entry point ──────────────────────────────────────────────────
async def tier1_triage(
    image_base64: str,
    *,
    status: str,
    symptoms: list[str],
    species: Optional[str] = None,
    baseline: Optional[dict[str, Any]] = None,
    mode_hint: Optional[str] = None,
) -> Optional[Tier1Triage]:
    """Run one Gemini call to triage a flagged tree.

    Returns None on any failure (caller logs context and proceeds to the
    full agent loop as a safe fallback).
    """
    prompt = _build_prompt(
        status,
        symptoms,
        species,
        baseline,
        mode_hint,
        threshold=CONFIG.tier1_severity_threshold,
    )

    try:
        result: Tier1Triage = await asyncio.to_thread(
            generate_structured,
            model=CONFIG.tier1_model,
            prompt=prompt,
            response_schema=Tier1Triage,
            image_base64=image_base64,
            temperature=0.1,
            # Tier-1 is a triage decision, not deep reasoning. Disable
            # thinking entirely — 3.5-flash otherwise burns the output
            # budget on internal thoughts BEFORE writing the JSON, and
            # we end up with truncated half-written responses.
            thinking_budget=0,
            # Even without thinking, give the structured response plenty
            # of room (indicators list + assessment + action sentences).
            max_output_tokens=2048,
        )
        return result
    except Exception:
        logger.exception("Tier-1 triage call failed.")
        return None


def diagnosis_from_tier1(
    tier1: Tier1Triage,
    species: Optional[str],
) -> DiagnosisCore:
    """Build a DiagnosisCore from a fast-path tier-1 result.

    Called when severity < threshold. We pivot the `disease` text on the
    severity band AND the observed indicators so the user sees something
    informative — not just "Borderline — triage only" — even though we
    skipped the deep agent loop.
    """
    sev = tier1.severity
    indicators = [i.strip() for i in tier1.primary_indicators if i and i.strip()][:3]
    indicator_str = ", ".join(indicators)

    if sev < 0.2:
        # False alarm from upstream detection.
        disease = "None detected"
        confidence = round(min(0.95, 1.0 - sev), 2)
        default_action = "No action required; tree appears healthy."
    elif sev < 0.4:
        # Minor variation within species norms.
        disease = "Minor variation, within normal range"
        confidence = round(0.75 - sev * 0.5, 2)
        default_action = "No action required; continue routine monitoring."
    elif sev < 0.6:
        # Visible but mild stress — name the indicators so the report
        # is actionable without a deep diagnosis.
        disease = (
            f"Mild crown stress ({indicator_str})"
            if indicator_str else "Mild crown stress"
        )
        confidence = round(sev, 2)
        default_action = (
            "Monitor on next scheduled survey; no immediate intervention needed."
        )
    else:
        # severity 0.6 - threshold: moderate stress that doesn't quite
        # warrant the full agent under the current cost gate. Surface
        # the indicators prominently so a field crew can prioritize.
        disease = (
            f"Moderate crown stress ({indicator_str})"
            if indicator_str else "Moderate crown stress"
        )
        confidence = round(sev, 2)
        default_action = (
            "Schedule field inspection within 2 weeks to confirm cause."
        )

    summary = (tier1.quick_assessment or "").strip()
    if indicators and summary and indicator_str not in summary:
        summary = f"{summary} Indicators noted: {indicator_str}."
    if not summary:
        summary = (
            f"Triage assessment at severity {sev:.2f}"
            + (f"; indicators: {indicator_str}." if indicator_str else ".")
        )

    action = (tier1.suggested_action or "").strip() or default_action

    return DiagnosisCore(
        disease=disease,
        diseaseConfidence=confidence,
        summary=summary,
        actionPlan=action,
        cutReason=None,
        species=species,
        speciesConfidence=(0.85 if species else None),
    )
