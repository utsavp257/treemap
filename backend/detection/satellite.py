"""Satellite-tile tree detection via Gemini spatial understanding.

Gemini 2.5's spatial mode returns bounding boxes normalized to [0, 1000].
We do the pixel-to-lat/lng math in Python from the tile bounds — never
asking the model to do geo arithmetic (the previous bug).
"""

from __future__ import annotations

import logging

from ..config import CONFIG
from ..gemini_client import generate_structured
from ..schemas import TreeDetection, TreeDetectionRaw

logger = logging.getLogger(__name__)


_PROMPT = """Detect every individual tree crown visible in this top-down satellite image.

For each detected tree, return:
- box_2d: [ymin, xmin, ymax, xmax] in normalized 0-1000 coordinates of the image.
- label: a best-guess species name if identifiable from crown shape and color, otherwise "tree".
- health_observation: one of "healthy", "monitor", "treat", "cut", reflecting the visible canopy condition only.
- confidence: 0.0-1.0 detection confidence.
- visible_symptoms: short labels for visible issues (e.g. "discoloration", "crown thinning", "canopy gap", "dieback"). Empty list if none.

Be conservative on health_observation — do not infer disease from color alone; only mark "treat" or "cut" when canopy damage is clearly visible.
Return ONLY a JSON array.
"""


# Health-observation -> headline score. Phase 1 keeps this simple.
# The agentic diagnosis in Phase 4 will replace these scores with
# evidence-grounded values.
_BASE_SCORE = {
    "healthy": 88,
    "monitor": 62,
    "treat": 38,
    "cut": 18,
}


def _bbox_to_geo(
    box: list[int],
    north: float,
    south: float,
    east: float,
    west: float,
) -> tuple[float, float, float]:
    """Convert a normalized [ymin, xmin, ymax, xmax] bbox to lat/lng + crown radius (m)."""
    ymin, xmin, ymax, xmax = box
    cy = (ymin + ymax) / 2.0 / 1000.0
    cx = (xmin + xmax) / 2.0 / 1000.0

    lat = north - cy * (north - south)
    lng = west + cx * (east - west)

    # Crown radius from bbox size, projected to meters via latitude span.
    lat_span_m = abs(north - south) * 111_320.0
    bbox_size_norm = ((ymax - ymin) + (xmax - xmin)) / 2.0 / 1000.0
    crown_radius_m = max(2.0, min(20.0, bbox_size_norm * lat_span_m / 2.0))
    return lat, lng, crown_radius_m


def detect_trees(
    image_base64: str,
    *,
    north: float,
    south: float,
    east: float,
    west: float,
) -> list[TreeDetection]:
    """Run Gemini spatial detection on a satellite tile and return geo-anchored trees."""
    raw_results: list[TreeDetectionRaw] = generate_structured(
        model=CONFIG.detection_model,
        prompt=_PROMPT,
        response_schema=list[TreeDetectionRaw],
        image_base64=image_base64,
        temperature=0.1,
        max_output_tokens=16384,
    )

    trees: list[TreeDetection] = []
    for raw in raw_results:
        if len(raw.box_2d) != 4:
            logger.warning("Skipping bbox with bad shape: %s", raw.box_2d)
            continue

        lat, lng, crown_radius_m = _bbox_to_geo(raw.box_2d, north, south, east, west)
        status = raw.health_observation
        trees.append(
            TreeDetection(
                lat=round(lat, 7),
                lng=round(lng, 7),
                crownRadiusM=round(crown_radius_m, 1),
                label=raw.label,
                status=status,
                healthScore=_BASE_SCORE[status],
                detectionConfidence=raw.confidence,
                visualSymptoms=raw.visible_symptoms,
                spreadRiskRadiusM=15.0 if status in ("treat", "cut") else 0.0,
                spreadRiskScore=0.7 if status == "cut" else 0.4 if status == "treat" else 0.0,
            )
        )

    return trees
