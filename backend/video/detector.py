"""Per-frame Gemini spatial-mode detection for drone video.

Crown mode prompts for top-down canopy detection (similar to satellite,
but typically lower altitude and possibly slight oblique).

Stem mode prompts for trunk detection in low-altitude horizontal-camera
footage. Symptoms reported in stem mode reflect bark-level signals
(pitch tubes, frass, bark damage) rather than canopy-level signals.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from ..config import CONFIG
from ..gemini_client import generate_structured
from ..schemas import HealthStatus, VideoMode

logger = logging.getLogger(__name__)


_CROWN_PROMPT = """Detect every individual tree CROWN visible in this drone video frame.

The camera is looking down or slightly oblique from a low-altitude drone (typically 30-120m AGL). Each tree appears as a roughly circular canopy footprint.

For each detected crown, return:
- box_2d: [ymin, xmin, ymax, xmax] in normalized 0-1000 coordinates of the image.
- label: best-guess species name if identifiable, otherwise "tree".
- health_observation: one of "healthy", "monitor", "treat", "cut" based on visible canopy condition only.
- confidence: 0.0-1.0 detection confidence.
- visible_symptoms: short labels for visible issues ("chlorosis", "crown thinning", "canopy gap", "dieback").

Be conservative on health_observation. Don't infer disease from color alone.
Return ONLY a JSON array.
"""


_STEM_PROMPT = """Detect every individual tree TRUNK / STEM visible in this low-altitude drone video frame.

The camera is horizontal or near-horizontal at 2-10m AGL, so you see tree trunks at close range — bark, lower branches, sometimes the trunk base.

For each detected trunk, return:
- box_2d: [ymin, xmin, ymax, xmax] in normalized 0-1000 coordinates of the image.
- label: best-guess species name from bark texture and any visible foliage, otherwise "tree".
- health_observation: one of "healthy", "monitor", "treat", "cut" based on visible BARK condition only — not canopy.
- confidence: 0.0-1.0 detection confidence.
- visible_symptoms: short labels for visible bark/trunk issues ("pitch tubes", "resin flow", "frass", "bark damage", "canker", "mycelial fan", "wound").

Be conservative. Discrete pitch tubes are popcorn-sized resin blobs around a bore hole, not just pinkish bark.
Return ONLY a JSON array.
"""


class _RawFrameDetection(BaseModel):
    box_2d: list[int] = Field(..., description="[ymin, xmin, ymax, xmax] normalized 0-1000.")
    label: str
    health_observation: HealthStatus
    confidence: float = Field(..., ge=0.0, le=1.0)
    visible_symptoms: list[str] = Field(default_factory=list)


@dataclass
class FrameDetection:
    box_2d: list[int]          # [ymin, xmin, ymax, xmax] 0-1000
    label: str
    status: HealthStatus
    confidence: float
    symptoms: list[str]


_PROMPT_BY_MODE: dict[VideoMode, str] = {
    "crown": _CROWN_PROMPT,
    "stem": _STEM_PROMPT,
}


async def detect_in_frame(
    image_base64: str, mode: VideoMode
) -> tuple[list[FrameDetection], str | None]:
    """Run Gemini spatial detection on a single video frame.

    Returns (detections, error_message_or_None). The pipeline aggregates
    error_message so it can mark a whole job failed if every frame errored
    out for the same reason (e.g. quota exhausted).
    """
    prompt = _PROMPT_BY_MODE[mode]
    try:
        raw_list: list[_RawFrameDetection] = await asyncio.to_thread(
            generate_structured,
            model=CONFIG.detection_model,
            prompt=prompt,
            response_schema=list[_RawFrameDetection],
            image_base64=image_base64,
            temperature=0.1,
            max_output_tokens=16384,
        )
    except Exception as e:
        logger.warning("Frame detect failed: %s", e)
        return [], str(e)

    out: list[FrameDetection] = []
    for r in raw_list:
        if len(r.box_2d) != 4:
            continue
        out.append(
            FrameDetection(
                box_2d=r.box_2d,
                label=r.label,
                status=r.health_observation,
                confidence=r.confidence,
                symptoms=r.visible_symptoms,
            )
        )
    return out, None


# ── Tiled detection ─────────────────────────────────────────────────────
#
# Gemini's spatial detection has a practical per-image object count cap
# (~20-30 objects per response, regardless of max_output_tokens). For
# dense forest frames with 50+ trees, the model returns its most-salient
# picks and silently drops the rest.
#
# Workaround: crop the frame into a 2x2 grid with overlap, run detection
# on each tile concurrently, translate bboxes back to global coords, and
# merge via greedy NMS at IoU 0.4 so trees on tile seams don't double-count.


def _iou(a: list[int], b: list[int]) -> float:
    ay1, ax1, ay2, ax2 = a
    by1, bx1, by2, bx2 = b
    iy1, ix1 = max(ay1, by1), max(ax1, bx1)
    iy2, ix2 = min(ay2, by2), min(ax2, bx2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0, ay2 - ay1) * max(0, ax2 - ax1)
    area_b = max(0, by2 - by1) * max(0, bx2 - bx1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _dedupe_by_iou(
    dets: list[FrameDetection], iou_threshold: float = 0.40
) -> list[FrameDetection]:
    """Greedy NMS-style dedup. Highest confidence first; drop anything
    that overlaps a kept detection above the threshold.
    """
    sorted_dets = sorted(dets, key=lambda d: -d.confidence)
    kept: list[FrameDetection] = []
    for d in sorted_dets:
        if any(_iou(d.box_2d, k.box_2d) > iou_threshold for k in kept):
            continue
        kept.append(d)
    return kept


async def detect_in_frame_tiled(
    pil_image: Image.Image,
    mode: VideoMode,
    *,
    grid: tuple[int, int] = (2, 2),
    overlap_frac: float = 0.10,
    dedupe_iou: float = 0.40,
    jpeg_quality: int = 85,
) -> tuple[list[FrameDetection], str | None]:
    """Run detection on a frame split into a (rows x cols) tile grid.

    Each tile is detected independently and concurrently. Bboxes are
    translated from per-tile [0-1000] back to whole-frame [0-1000] and
    deduped at IoU > dedupe_iou to handle trees straddling tile seams.

    Args:
        pil_image: source frame (already at desired resolution).
        mode: "crown" or "stem".
        grid: (rows, cols). Default 2x2.
        overlap_frac: fraction of tile width/height to overlap neighbours
            so trees on the seam are fully visible in at least one tile.
        dedupe_iou: NMS threshold for cross-tile duplicates.

    Returns:
        (merged_detections, error_string_or_None). error_string is
        populated only when EVERY tile failed; partial-failure runs still
        return whatever did succeed.
    """
    sw, sh = pil_image.size
    rows, cols = grid

    tw_base = sw / cols
    th_base = sh / rows
    ox = tw_base * overlap_frac
    oy = th_base * overlap_frac

    # Compute tile rects in source-pixel coords.
    tile_specs: list[tuple[int, int, int, int]] = []
    for r in range(rows):
        for c in range(cols):
            x = max(0, int(round(c * tw_base - ox)))
            y = max(0, int(round(r * th_base - oy)))
            w = min(sw - x, int(round(tw_base + 2 * ox)))
            h = min(sh - y, int(round(th_base + 2 * oy)))
            tile_specs.append((x, y, w, h))

    async def _detect_one_tile(
        spec: tuple[int, int, int, int],
        idx: int,
    ) -> tuple[list[FrameDetection], str | None, int]:
        x, y, w, h = spec
        # Crop + encode this tile.
        tile_pil = pil_image.crop((x, y, x + w, y + h)).convert("RGB")
        buf = io.BytesIO()
        tile_pil.save(buf, format="JPEG", quality=jpeg_quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        dets, err = await detect_in_frame(b64, mode=mode)
        if not dets:
            return [], err, idx

        # Map each detection's bbox from per-tile [0-1000] to global [0-1000].
        translated: list[FrameDetection] = []
        for d in dets:
            ymin_t, xmin_t, ymax_t, xmax_t = d.box_2d
            # tile-norm → tile-pixel
            tymin_px = (ymin_t / 1000.0) * h
            txmin_px = (xmin_t / 1000.0) * w
            tymax_px = (ymax_t / 1000.0) * h
            txmax_px = (xmax_t / 1000.0) * w
            # tile-pixel → global-pixel
            g_ymin = tymin_px + y
            g_xmin = txmin_px + x
            g_ymax = tymax_px + y
            g_xmax = txmax_px + x
            # global-pixel → global-norm
            translated.append(
                FrameDetection(
                    box_2d=[
                        int(round(g_ymin / sh * 1000)),
                        int(round(g_xmin / sw * 1000)),
                        int(round(g_ymax / sh * 1000)),
                        int(round(g_xmax / sw * 1000)),
                    ],
                    label=d.label,
                    status=d.status,
                    confidence=d.confidence,
                    symptoms=list(d.symptoms),
                )
            )
        return translated, err, idx

    results = await asyncio.gather(
        *[_detect_one_tile(spec, i) for i, spec in enumerate(tile_specs)]
    )

    per_tile_counts: list[int] = [0] * len(tile_specs)
    all_dets: list[FrameDetection] = []
    errors: list[str] = []
    for dets, err, idx in results:
        per_tile_counts[idx] = len(dets)
        all_dets.extend(dets)
        if err is not None:
            errors.append(err)

    deduped = _dedupe_by_iou(all_dets, iou_threshold=dedupe_iou)

    logger.info(
        "  tiled detect: per-tile=%s, total raw=%d, after dedup=%d",
        per_tile_counts, len(all_dets), len(deduped),
    )

    final_err = errors[0] if errors and len(errors) == len(tile_specs) else None
    return deduped, final_err
