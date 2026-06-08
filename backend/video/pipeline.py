"""End-to-end video processing pipeline.

Stages: extract → detect (per frame) → track (across frames) → crop
representative frame → diagnose each tracked tree via the agent.

Stages are async so frame I/O, Gemini calls, and MCP tool calls can
overlap. Heavy CPU work (ffmpeg, PIL cropping) is run on a thread to
keep the event loop responsive.

Results are written into the supplied VideoJob as the stages complete,
so polling `/scan-video/{job_id}` shows live progress.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from ..agent.orchestrator import diagnose_tree
from ..agent.triage import diagnosis_from_tier1, tier1_triage
from ..config import CONFIG
from ..mcp_client import mcp_client
from ..schemas import (
    DiagnoseRequest,
    DiagnosisResult,
    EvidenceStep,
    HealthStatus,
    TrackedTree,
    VideoJob,
    VideoMode,
)
from .detector import detect_in_frame, detect_in_frame_tiled
from .extract import (
    DEFAULT_TARGET_WIDTH,
    MAX_TARGET_WIDTH,
    MIN_TARGET_WIDTH,
    Frame,
    Telemetry,
    build_frame_manifest,
    compute_target_width,
    extract_frames,
    median_altitude,
    parse_srt,
)
from .projection import is_nadir, project_bbox_centroid
from .tracker import (
    Tracker,
    TrackState,
    best_representative_frame,
    majority_status,
)

logger = logging.getLogger(__name__)

# More padding than before. Atomic tools benefit from neighboring context
# (adjacent branches, ground level, surrounding canopy) — they're trying
# to assess SHAPE and PATTERN, not just a tight rectangle.
CROP_PADDING_FRAC = 0.30

# Atomic tools want at least this many pixels per side. Smaller crops
# get upscaled (Lanczos) — costs the same one Gemini tile either way.
CROP_MIN_EDGE_PX = 256

import io


def _image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


async def _mcp_call_json(tool_name: str, args: dict) -> Optional[dict]:
    """Call an MCP tool and JSON-decode the first text content part.

    Returns None on any failure (caller logs context). Centralized so the
    pipeline doesn't repeat the same content-extraction boilerplate.
    """
    if not mcp_client.is_alive or mcp_client.session is None:
        return None
    result = await mcp_client.session.call_tool(tool_name, args)
    for item in (getattr(result, "content", None) or []):
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return None


async def _batch_tier1_triage(
    tracked_trees: list[TrackedTree],
    *,
    crops_dir: Path,
    mode: str,
    species_hint: Optional[str],
    species_baseline: Optional[dict],
    max_concurrent: int = 8,
) -> dict[str, Any]:
    """Fan out tier-1 triage calls across non-healthy trees in parallel.

    Returns a dict mapping track_id → Tier1Triage (or None on failure /
    not applicable). max_concurrent caps simultaneous Gemini calls so we
    stay polite to rate limits even on dense videos.

    Wall-clock impact: 50 trees in series at ~2s each ≈ 100s. With a
    concurrency of 8: ~13s. Same cost, ~8x faster.
    """
    if not CONFIG.tier1_enabled:
        return {}

    sem = asyncio.Semaphore(max_concurrent)

    async def _one(tt: TrackedTree):
        if tt.initial_status == "healthy":
            return tt.track_id, None
        async with sem:
            crop_b64 = await asyncio.to_thread(
                _image_to_base64, crops_dir / f"{tt.track_id}.jpg"
            )
            result = await tier1_triage(
                crop_b64,
                status=tt.initial_status,
                symptoms=tt.initial_symptoms,
                species=species_hint,
                baseline=species_baseline,
                mode_hint=mode,
            )
            return tt.track_id, result

    pairs = await asyncio.gather(*[_one(tt) for tt in tracked_trees])
    return dict(pairs)


async def _diagnose_one_tree(
    tt: TrackedTree,
    *,
    crops_dir: Path,
    mode: str,
    species_hint: Optional[str],
    species_baseline: Optional[dict],
    triage_result: Optional[Any] = None,
) -> str:
    """Diagnose a single tracked tree using a pre-computed tier-1 result.

    Returns one of:
       - "fast_path"  : tier-1 handled it without the full agent
       - "escalated"  : tier-1 escalated to the full agent loop
       - "no_triage"  : tier-1 disabled / failed / not applicable
    Sets tt.diagnosis as a side effect.
    """
    crop_b64 = await asyncio.to_thread(
        _image_to_base64, crops_dir / f"{tt.track_id}.jpg"
    )

    enriched_symptoms = list(tt.initial_symptoms)
    triage_evidence: list[EvidenceStep] = []
    outcome = "no_triage"

    if triage_result is not None:
        triage_evidence.append(
            EvidenceStep(
                tool="tier1_triage",
                inputs_summary=(
                    f"status={tt.initial_status}, "
                    f"symptoms=[{len(tt.initial_symptoms)} items], "
                    f"species={species_hint or 'unknown'}"
                ),
                output=triage_result.model_dump(),
            )
        )

        if triage_result.severity < CONFIG.tier1_severity_threshold:
            # Fast path — tier-1 IS the diagnosis. No deeper analysis.
            core = diagnosis_from_tier1(triage_result, species_hint)
            tt.diagnosis = DiagnosisResult(
                **core.model_dump(),
                evidenceTrace=triage_evidence,
            )
            logger.info(
                "Tier-1 fast-path %s: severity=%.2f → %s",
                tt.track_id, triage_result.severity, core.disease,
            )
            return "fast_path"

        # Escalate — pass tier-1 indicators into the agent's context.
        logger.info(
            "Tier-1 escalating %s: severity=%.2f, indicators=%s",
            tt.track_id, triage_result.severity, triage_result.primary_indicators,
        )
        for ind in triage_result.primary_indicators:
            if ind and ind not in enriched_symptoms:
                enriched_symptoms.append(ind)
        outcome = "escalated"

    req = DiagnoseRequest(
        image_base64=crop_b64,
        status=tt.initial_status,
        visual_symptoms=enriched_symptoms,
        mode_hint=mode,
        species_hint=species_hint,
        species_baseline=species_baseline,
    )
    try:
        full = await diagnose_tree(req)
        # Prepend triage evidence so the UI shows tier-1 as the first step.
        if triage_evidence:
            full = DiagnosisResult(
                **{k: v for k, v in full.model_dump().items() if k != "evidenceTrace"},
                evidenceTrace=triage_evidence + list(full.evidenceTrace),
            )
        tt.diagnosis = full
    except Exception as e:
        logger.warning("Diagnosis failed for %s: %s", tt.track_id, e)

    return outcome


def _load_pil_for_detection(src_path: Path, target_width: int) -> Image.Image:
    """Load a full-res frame, downsample to target_width if larger.

    Returns a PIL Image (the tiler crops sub-tiles from this). The
    on-disk frame remains at full resolution so atomic-tool crops still
    see all the detail.
    """
    im = Image.open(src_path).convert("RGB")
    w, h = im.size
    if w > target_width:
        scale = target_width / float(w)
        im = im.resize(
            (target_width, max(1, int(round(h * scale)))),
            Image.LANCZOS,
        )
    return im


def _crop_from_bbox(
    src_path: Path,
    bbox_0_1000: list[int],
    out_path: Path,
) -> None:
    """Crop the FULL-RESOLUTION frame at the bbox + 30% padding margin.

    Tiny crops get upscaled to CROP_MIN_EDGE_PX so atomic tools always
    have enough pixels for fine-grained assessment (leaf venation, bark
    texture, etc.). The cost to Gemini is the same either way (one tile).
    """
    with Image.open(src_path) as im:
        w, h = im.size
        ymin, xmin, ymax, xmax = bbox_0_1000

        pad_y = (ymax - ymin) * CROP_PADDING_FRAC
        pad_x = (xmax - xmin) * CROP_PADDING_FRAC
        ymin = max(0.0, ymin - pad_y) / 1000.0
        ymax = min(1000.0, ymax + pad_y) / 1000.0
        xmin = max(0.0, xmin - pad_x) / 1000.0
        xmax = min(1000.0, xmax + pad_x) / 1000.0

        box = (int(xmin * w), int(ymin * h), int(xmax * w), int(ymax * h))
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            box = (0, 0, w, h)
        crop = im.crop(box).convert("RGB")

        # Upscale very small crops so atomic tools see ~256+ px detail.
        cw, ch = crop.size
        if min(cw, ch) < CROP_MIN_EDGE_PX:
            scale = CROP_MIN_EDGE_PX / float(min(cw, ch))
            crop = crop.resize(
                (int(round(cw * scale)), int(round(ch * scale))),
                Image.LANCZOS,
            )
        crop.save(out_path, format="JPEG", quality=90)


async def run_video_pipeline(
    job: VideoJob,
    *,
    workdir: Path,
    video_path: Path,
    srt_path: Optional[Path],
) -> None:
    """Run the full pipeline; mutates `job` in place."""
    frames_dir = workdir / "frames"
    crops_dir = workdir / "crops"
    crops_dir.mkdir(exist_ok=True)

    try:
        # ── Stage 1a: parse SRT first so we can size frames adaptively ──
        srt_samples: list[Telemetry] = []
        if srt_path and srt_path.exists():
            srt_samples = await asyncio.to_thread(parse_srt, srt_path)

        median_alt = median_altitude(srt_samples)

        # Resolution choice — three-way priority:
        #   1. Explicit user override (job.frame_max_edge_px) wins.
        #   2. Else: derive from median altitude if SRT supplied one.
        #   3. Else: conservative default.
        if job.frame_max_edge_px:
            target_width = max(
                MIN_TARGET_WIDTH, min(MAX_TARGET_WIDTH, job.frame_max_edge_px)
            )
            resolution_source = "user-override"
        elif median_alt is not None:
            target_width = compute_target_width(median_alt)
            resolution_source = f"altitude-adaptive ({median_alt:.0f}m)"
        else:
            target_width = DEFAULT_TARGET_WIDTH
            resolution_source = "default (no altitude data)"

        job.frame_max_edge_px = target_width
        logger.info(
            "Pipeline: target frame width %dpx — %s",
            target_width, resolution_source,
        )

        # ── Stage 1b: ffmpeg extraction at full source resolution ───────
        # We keep frames at native resolution on disk so atomic-tool crops
        # have all the detail. Detection downsamples in memory per-call.
        job.status = "extracting"
        job.progress = 0.05
        frame_paths = await asyncio.to_thread(
            extract_frames,
            video_path, frames_dir,
            fps=job.fps,
        )
        if not frame_paths:
            raise RuntimeError("No frames extracted from the video.")
        job.frame_count = len(frame_paths)

        job.has_gps = bool(srt_samples) and any(
            s.lat is not None and s.lng is not None for s in srt_samples
        )

        frames: list[Frame] = build_frame_manifest(frame_paths, job.fps, srt_samples)
        logger.info(
            "Pipeline: %d frames extracted at %dpx; GPS=%s; SRT samples=%d",
            len(frames), target_width, job.has_gps, len(srt_samples),
        )

        # ── Stage 2: per-frame detection ─────────────────────────────────
        job.status = "detecting"
        tracker = Tracker()

        detection_counts: list[int] = []
        last_error: str | None = None
        error_count = 0
        for i, frame in enumerate(frames):
            # Load frame at adaptive detection resolution, then tile it
            # 2x2 (10% overlap) so Gemini sees each region with its own
            # object-count budget. Tiles are detected concurrently.
            pil_frame = await asyncio.to_thread(
                _load_pil_for_detection, frame.path, target_width
            )
            detections, err = await detect_in_frame_tiled(pil_frame, mode=job.mode)
            detection_counts.append(len(detections))
            if err is not None:
                error_count += 1
                last_error = err
            logger.info(
                "Frame %d/%d: %d detections%s",
                i + 1, len(frames), len(detections),
                f" (error: {err[:120]})" if err else "",
            )
            tracker.update(frame.index, detections)
            job.progress = 0.10 + 0.55 * ((i + 1) / len(frames))
        logger.info(
            "Detection summary: %d total across %d frames (per-frame: %s); errors=%d",
            sum(detection_counts), len(frames), detection_counts, error_count,
        )

        # If every frame errored out, surface the underlying reason as a job
        # failure rather than reporting "complete with 0 trees" — that
        # masks real issues (quota exhausted, model not found, etc.).
        if error_count == len(frames) and last_error:
            raise RuntimeError(
                f"All {len(frames)} frame detections failed. Last error: {last_error}"
            )

        # ── Stage 3: finalize tracks ─────────────────────────────────────
        job.status = "tracking"
        all_tracks: list[TrackState] = tracker.finalize()

        # Adaptive filter: on short videos every detection lands in 1 frame
        # because the drone moves between samples. Only require 2-frame
        # persistence when we actually have enough frames for that to be
        # a meaningful signal (>= 5 frames).
        min_persistence = 2 if len(frames) >= 5 else 1
        tracks = [t for t in all_tracks if len(t.frame_indices) >= min_persistence]
        job.tracked_tree_count = len(tracks)
        job.progress = 0.70
        logger.info(
            "Pipeline: %d tracks total, %d kept (min_persistence=%d frames)",
            len(all_tracks), len(tracks), min_persistence,
        )

        # ── Stage 4: crop representative frame per tree ──────────────────
        tracked_trees: list[TrackedTree] = []
        for t in tracks:
            rep_frame_idx = best_representative_frame(t)
            if rep_frame_idx is None:
                continue
            rep_frame = next(f for f in frames if f.index == rep_frame_idx)
            rep_bbox = t.bboxes_by_frame[rep_frame_idx]
            crop_path = crops_dir / f"{t.track_id}.jpg"
            await asyncio.to_thread(
                _crop_from_bbox, rep_frame.path, rep_bbox, crop_path
            )

            # Optional GPS projection (crown + nadir only).
            lat = lng = None
            if (
                job.mode == "crown"
                and rep_frame.telemetry is not None
                and is_nadir(rep_frame.telemetry)
            ):
                proj = project_bbox_centroid(rep_bbox, rep_frame.telemetry)
                if proj:
                    lat, lng = proj

            tracked_trees.append(
                TrackedTree(
                    track_id=t.track_id,
                    representative_frame=rep_frame_idx,
                    bbox_normalized=rep_bbox,
                    frames_seen=len(t.frame_indices),
                    detection_confidence=max(t.confidences) if t.confidences else 0.0,
                    initial_status=majority_status(t.statuses),
                    initial_symptoms=t.symptoms,
                    crop_url=f"/scan-video/{job.job_id}/tree/{t.track_id}/image",
                    lat=lat,
                    lng=lng,
                )
            )

        job.trees = tracked_trees
        job.progress = 0.75

        # ── Stage 4b: video-level species ID + baseline pre-fetch ───────
        # When same_species is on AND the user didn't already supply a
        # hint, run identify_species ONCE on the highest-confidence crop.
        # Then pre-fetch lookup_species_baseline for that species so we
        # can inject the baseline directly into each per-tree agent's
        # initial context — eliminating the duplicated lookup-baseline
        # calls that wasted ~30% of budget in earlier runs.
        shared_species_hint = (job.species_hint or "").strip() or None
        shared_species_baseline: Optional[dict] = None

        if (
            shared_species_hint is None
            and job.same_species
            and tracked_trees
            and mcp_client.is_alive
        ):
            best = max(tracked_trees, key=lambda t: t.detection_confidence)
            try:
                best_crop_b64 = await asyncio.to_thread(
                    _image_to_base64, crops_dir / f"{best.track_id}.jpg"
                )
                id_payload = await _mcp_call_json(
                    "identify_species", {"image_base64": best_crop_b64}
                )
                species = ((id_payload or {}).get("species") or "").strip()
                if species and species.lower() != "tree":
                    shared_species_hint = species
                    job.species_hint = species
                    logger.info(
                        "Video-level species ID: %s (conf=%.2f, source=%s)",
                        species,
                        (id_payload or {}).get("confidence", 0.0),
                        (id_payload or {}).get("source", "?"),
                    )
                else:
                    logger.info("Video-level species ID inconclusive; skipping cache.")
            except Exception as e:
                logger.warning("Video-level species ID failed: %s", e)

        # Pre-fetch the baseline whenever we have a species — works for
        # both the auto-identified path AND when the user supplied a hint.
        if shared_species_hint and mcp_client.is_alive:
            try:
                shared_species_baseline = await _mcp_call_json(
                    "lookup_species_baseline", {"species": shared_species_hint}
                )
                if shared_species_baseline:
                    logger.info(
                        "Pre-fetched baseline for %s (matched=%s, source=%s)",
                        shared_species_hint,
                        shared_species_baseline.get("matched"),
                        shared_species_baseline.get("source"),
                    )
            except Exception as e:
                logger.warning("Baseline pre-fetch failed for %s: %s", shared_species_hint, e)

        # ── Stage 5a: batch tier-1 triage (parallel, bounded concurrency) ──
        # Fanning these out shaves wall-clock dramatically on dense videos
        # without changing per-call cost.
        job.status = "diagnosing"
        triage_map = await _batch_tier1_triage(
            tracked_trees,
            crops_dir=crops_dir,
            mode=job.mode,
            species_hint=shared_species_hint,
            species_baseline=shared_species_baseline,
        )
        if triage_map:
            triage_done = sum(1 for v in triage_map.values() if v is not None)
            logger.info(
                "Tier-1 batch complete: %d trees triaged (of %d non-healthy)",
                triage_done,
                sum(1 for tt in tracked_trees if tt.initial_status != "healthy"),
            )

        # ── Stage 5b: per-tree diagnosis loop ─────────────────────────────
        tier1_summary = {"fast_path": 0, "escalated": 0, "no_triage": 0}
        for i, tt in enumerate(tracked_trees):
            outcome = await _diagnose_one_tree(
                tt,
                crops_dir=crops_dir,
                mode=job.mode,
                species_hint=shared_species_hint,
                species_baseline=shared_species_baseline,
                triage_result=triage_map.get(tt.track_id),
            )
            tier1_summary[outcome] = tier1_summary.get(outcome, 0) + 1
            job.progress = 0.75 + 0.24 * ((i + 1) / max(1, len(tracked_trees)))

        job.status = "complete"
        job.progress = 1.0
        logger.info(
            "Pipeline complete: %d trees diagnosed (tier-1: %d fast-path, %d escalated, %d no-triage)",
            len(tracked_trees),
            tier1_summary["fast_path"],
            tier1_summary["escalated"],
            tier1_summary["no_triage"],
        )

    except Exception as e:
        logger.exception("Video pipeline failed.")
        job.status = "failed"
        job.error = str(e)


def cleanup_workdir(workdir: Path) -> None:
    """Remove the per-job temp directory. Crops + frames go with it."""
    try:
        shutil.rmtree(workdir, ignore_errors=True)
    except Exception as e:
        logger.warning("Cleanup of %s failed: %s", workdir, e)


def workdir_for_job(base: Path, job_id: str) -> Path:
    workdir = base / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def new_job_id() -> str:
    return f"job-{int(time.time() * 1000):x}"
