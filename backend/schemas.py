"""Pydantic schemas for the request/response contracts.

These double as Gemini `response_schema` payloads — keeping them in one
place ensures the wire format and the structured-output contract stay
in sync.
"""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


# ── Health status vocabulary ─────────────────────────────────────────────
HealthStatus = Literal["healthy", "monitor", "treat", "cut"]


# ── /detect ──────────────────────────────────────────────────────────────
class DetectRequest(BaseModel):
    image_base64: str
    north: float
    south: float
    east: float
    west: float
    zoom: int


class TreeDetectionRaw(BaseModel):
    """Gemini's per-tree spatial-mode output, in normalized pixel space."""
    box_2d: list[int] = Field(
        ...,
        description="Bounding box [ymin, xmin, ymax, xmax] normalized to 0-1000."
    )
    label: str = Field(..., description="Best-guess species or 'tree' if unknown.")
    health_observation: HealthStatus
    confidence: float = Field(..., ge=0.0, le=1.0)
    visible_symptoms: list[str] = Field(default_factory=list)


class TreeDetection(BaseModel):
    """Post-conversion: geo-anchored, ready for the frontend."""
    lat: float
    lng: float
    crownRadiusM: float
    label: str
    status: HealthStatus
    healthScore: int = Field(..., ge=0, le=100)
    detectionConfidence: float = Field(..., ge=0.0, le=1.0)
    visualSymptoms: list[str] = Field(default_factory=list)
    spreadRiskRadiusM: float = 0.0
    spreadRiskScore: float = 0.0


class DetectResponse(BaseModel):
    trees: list[TreeDetection]
    count: int


# ── /diagnose ────────────────────────────────────────────────────────────
ViewMode = Literal["satellite", "crown", "stem"]


class DiagnoseRequest(BaseModel):
    image_base64: str
    status: HealthStatus
    visual_symptoms: list[str] = Field(default_factory=list)
    mode_hint: Optional[ViewMode] = Field(
        None,
        description="Hint about the source perspective so the agent picks the right tool subset.",
    )
    species_hint: Optional[str] = Field(
        None,
        description=(
            "Farmer/user-provided species (common or scientific). When supplied, "
            "the agent treats this as ground truth and skips identify_species."
        ),
    )
    species_baseline: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Pre-fetched trait baseline for the species. When supplied, the "
            "agent injects it directly into its initial context and skips the "
            "mandatory lookup_species_baseline tool call entirely."
        ),
    )


class DiagnosisCore(BaseModel):
    """Structured-output schema used by the agent's synthesis call.

    No evidence trace, no auxiliary fields the model can't reason about
    directly — the orchestrator assembles the full DiagnosisResult around
    a DiagnosisCore.
    """
    disease: str = Field(..., description='Disease name or "None detected".')
    diseaseConfidence: float = Field(..., ge=0.0, le=1.0)
    summary: str = Field(..., description="Two-sentence diagnosis grounded in the gathered evidence.")
    actionPlan: str
    cutReason: Optional[str] = Field(
        None,
        description="Only populated when the recommended action is removal."
    )
    species: Optional[str] = Field(
        None,
        description="Scientific species name if identified, else null."
    )
    speciesConfidence: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Confidence of species identification, else null."
    )


class EvidenceStep(BaseModel):
    """One step in the agent's adaptive diagnostic loop.

    Each step records a tool call and its result so the frontend can
    render an audit trail of *why* the diagnosis says what it says.
    """
    tool: str
    inputs_summary: str = Field(..., description="Short human-readable summary of arguments (image payloads elided).")
    output: dict[str, Any] = Field(default_factory=dict)


class DiagnosisResult(DiagnosisCore):
    """Full diagnosis response shape returned to the frontend."""
    evidenceTrace: list[EvidenceStep] = Field(default_factory=list)


# ── /scan-video ──────────────────────────────────────────────────────────
VideoMode = Literal["crown", "stem"]
JobStatus = Literal[
    "queued", "extracting", "detecting", "tracking", "diagnosing", "complete", "failed",
]


class TrackedTree(BaseModel):
    track_id: str
    representative_frame: int
    bbox_normalized: list[int] = Field(
        ...,
        description="[ymin, xmin, ymax, xmax] in normalized 0-1000 coords at the representative frame.",
    )
    frames_seen: int
    detection_confidence: float
    initial_status: HealthStatus
    initial_symptoms: list[str] = Field(default_factory=list)
    crop_url: str = Field(..., description="Backend URL serving the cropped JPEG for this track.")
    diagnosis: Optional[DiagnosisResult] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class VideoJob(BaseModel):
    job_id: str
    mode: VideoMode
    status: JobStatus = "queued"
    progress: float = Field(0.0, ge=0.0, le=1.0)
    error: Optional[str] = None
    fps: float = 0.5
    frame_count: int = 0
    tracked_tree_count: int = 0
    has_gps: bool = False
    species_hint: Optional[str] = None
    same_species: bool = Field(
        True,
        description=(
            "When true (default), the pipeline runs identify_species ONCE on the "
            "highest-confidence sick tree and reuses the result as a species_hint "
            "for every other tree. Skip when scanning a mixed-species stand."
        ),
    )
    frame_max_edge_px: Optional[int] = Field(
        None,
        description=(
            "Final frame width applied during extraction. "
            "User-supplied override OR altitude-adaptive choice OR default — "
            "populated by the pipeline once a value is selected."
        ),
    )
    trees: list[TrackedTree] = Field(default_factory=list)
    created_at: float
