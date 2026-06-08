"""Environment-driven configuration.

Model IDs are read from env so they can be swapped to whatever's current
without touching code (e.g. Gemini 3.x when it ships).
"""

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load backend/.env regardless of CWD.
load_dotenv(Path(__file__).resolve().parent / ".env")


@dataclass(frozen=True)
class Config:
    gemini_api_key: str

    # Pl@ntNet — species ID (Phase 2). Optional; if absent the tool falls
    # back to Gemini-only identification with lower accuracy.
    plantnet_api_key: str

    # Workhorse: fast multimodal calls (detection, atomic visual QA).
    detection_model: str
    flash_model: str

    # Heavy thinker: agent orchestration + final synthesis (Phase 4).
    orchestrator_model: str

    # Phase 1 single-call diagnosis. Replaced by orchestrator in Phase 4.
    diagnosis_model: str

    # API host + port for the FastAPI app.
    host: str
    port: int

    # Cost knobs — see backend/.env.example for explanations.
    max_tool_calls: int
    skip_healthy_diagnosis: bool

    # Tier-1 triage: cheap pre-filter that decides whether a flagged tree
    # actually needs the full agent loop. Most "monitor" detections are
    # false alarms; tier-1 confirms or denies them for ~1k tokens.
    tier1_enabled: bool
    tier1_model: str
    tier1_severity_threshold: float


def load_config() -> Config:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to backend/.env "
            "(see backend/.env.example)."
        )

    return Config(
        gemini_api_key=api_key,
        plantnet_api_key=os.getenv("PLANTNET_API_KEY", "").strip(),
        detection_model=os.getenv("GEMINI_DETECTION_MODEL", "gemini-2.5-flash"),
        flash_model=os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash"),
        orchestrator_model=os.getenv("GEMINI_ORCHESTRATOR_MODEL", "gemini-2.5-pro"),
        diagnosis_model=os.getenv("GEMINI_DIAGNOSIS_MODEL", "gemini-2.5-flash"),
        host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("BACKEND_PORT", "8080")),
        max_tool_calls=int(os.getenv("MAX_TOOL_CALLS", "6")),
        skip_healthy_diagnosis=os.getenv("SKIP_HEALTHY_DIAGNOSIS", "true").lower() in ("1", "true", "yes"),
        tier1_enabled=os.getenv("TIER1_ENABLED", "true").lower() in ("1", "true", "yes"),
        tier1_model=os.getenv("TIER1_MODEL", "gemini-3.5-flash"),
        tier1_severity_threshold=float(os.getenv("TIER1_SEVERITY_THRESHOLD", "0.80")),
    )


CONFIG = load_config()
