"""Single-call diagnosis fallback.

This is the safety net that fires when the MCP server isn't available
or the orchestrator can't complete the agentic loop. It's a single
Gemini Flash call with structured output — better than nothing, but
without the evidence-grounded depth the agent provides.

The orchestrator wraps the returned DiagnosisCore into a DiagnosisResult
with an empty evidence trace before returning to the API.
"""

from __future__ import annotations

from ..config import CONFIG
from ..gemini_client import generate_structured
from ..schemas import DiagnoseRequest, DiagnosisCore


_PROMPT_TEMPLATE = """You are an arborist diagnosing a tree from a close-up image.

Context from the upstream detection pass:
- Initial health observation: {status}
- Visible symptoms previously noted: {symptoms}

Diagnose this tree using ONLY what is visible in the image plus the context above.
- If no clear disease is visible, set disease to "None detected" with high confidence.
- Do not invent specific pathogen names without visible evidence.
- "cutReason" should be populated only when removal is the recommended action.
- "summary" must be two sentences and reference at least one observation grounded in the image.
- Populate "species" with your best species guess if any species cues are visible (leaf, bark, crown shape), else null.
- "speciesConfidence" calibrated 0-1, null if no guess.
"""


def diagnose(req: DiagnoseRequest) -> DiagnosisCore:
    symptoms = ", ".join(req.visual_symptoms) if req.visual_symptoms else "none"
    prompt = _PROMPT_TEMPLATE.format(status=req.status, symptoms=symptoms)

    return generate_structured(
        model=CONFIG.diagnosis_model,
        prompt=prompt,
        response_schema=DiagnosisCore,
        image_base64=req.image_base64,
        temperature=0.15,
        max_output_tokens=2048,
    )
