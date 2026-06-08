"""FastAPI entrypoint — Phase 1.

Slim and honest:
- /detect runs Gemini spatial-mode tree detection on a satellite tile.
- /diagnose runs a structured single-call diagnosis on a tree crop
  (replaced by the MCP-backed agent in Phase 4 without touching this file).
- /health is a liveness probe.

No OpenCV, no rule-based fallbacks, no random data. If Gemini fails,
the user gets a typed error explaining what happened.
"""

import asyncio
import logging
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .agent.orchestrator import diagnose_tree
from .config import CONFIG
from .detection.satellite import detect_trees
from .errors import GeminiError, to_http
from .mcp_client import mcp_client
from .schemas import (
    DetectRequest,
    DetectResponse,
    DiagnoseRequest,
    DiagnosisResult,
    VideoJob,
    VideoMode,
)
from .video.extract import ffmpeg_available
from .video.pipeline import (
    cleanup_workdir,
    new_job_id,
    run_video_pipeline,
    workdir_for_job,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("rootcause")


_VIDEO_JOBS: dict[str, VideoJob] = {}
_VIDEO_WORKDIRS: dict[str, Path] = {}
_VIDEO_BASE = Path(tempfile.gettempdir()) / "rootcause-videos"


@asynccontextmanager
async def lifespan(app: FastAPI):
    _VIDEO_BASE.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        logger.warning("ffmpeg not found on PATH — /scan-video will return 503.")
    try:
        await mcp_client.start()
    except Exception:
        logger.exception(
            "MCP client failed to start — /diagnose will use the single-call fallback."
        )
    try:
        yield
    finally:
        await mcp_client.stop()
        for wd in list(_VIDEO_WORKDIRS.values()):
            cleanup_workdir(wd)


app = FastAPI(title="RootCause.ai backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/detect", response_model=DetectResponse)
async def detect_endpoint(req: DetectRequest) -> DetectResponse:
    logger.info(
        "detect: zoom=%s bounds=(%.5f,%.5f,%.5f,%.5f) image=%dKB",
        req.zoom, req.north, req.south, req.east, req.west,
        len(req.image_base64) // 1024,
    )
    try:
        trees = detect_trees(
            req.image_base64,
            north=req.north,
            south=req.south,
            east=req.east,
            west=req.west,
        )
    except GeminiError as e:
        logger.warning("detect: gemini failure — %s: %s", type(e).__name__, e)
        raise to_http(e) from e
    except Exception as e:
        logger.exception("detect: unexpected failure")
        raise to_http(e) from e

    logger.info("detect: returned %d trees", len(trees))
    return DetectResponse(trees=trees, count=len(trees))


@app.post("/diagnose", response_model=DiagnosisResult)
async def diagnose_endpoint(req: DiagnoseRequest) -> DiagnosisResult:
    logger.info(
        "diagnose: status=%s symptoms=%s image=%dKB",
        req.status, req.visual_symptoms, len(req.image_base64) // 1024,
    )
    try:
        result = await diagnose_tree(req)
    except GeminiError as e:
        logger.warning("diagnose: gemini failure — %s: %s", type(e).__name__, e)
        raise to_http(e) from e
    except Exception as e:
        logger.exception("diagnose: unexpected failure")
        raise to_http(e) from e

    logger.info(
        "diagnose: %s (conf=%.2f) via %d evidence steps",
        result.disease,
        result.diseaseConfidence,
        len(result.evidenceTrace),
    )
    return result


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "models": {
            "detection": CONFIG.detection_model,
            "diagnosis": CONFIG.diagnosis_model,
            "orchestrator": CONFIG.orchestrator_model,
        },
        "mcp_server": {
            "alive": mcp_client.is_alive,
            "tools": mcp_client.tool_names,
        },
        "plantnet_configured": bool(CONFIG.plantnet_api_key),
        "ffmpeg_available": ffmpeg_available(),
    }


# ── /scan-video ──────────────────────────────────────────────────────────
@app.post("/scan-video")
async def scan_video(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(..., description="MP4 drone footage."),
    mode: str = Form(..., description='"crown" or "stem".'),
    fps: float = Form(0.5, description="Frame sampling rate; default 0.5."),
    species_hint: Optional[str] = Form(
        None,
        description="Optional user-provided species, applied to every tree in this video.",
    ),
    same_species: bool = Form(
        True,
        description=(
            "When true, the pipeline runs identify_species ONCE for the whole "
            "video and reuses the result for every tree. Set false for mixed stands."
        ),
    ),
    max_edge_px: Optional[int] = Form(
        None,
        description=(
            "Override the frame width used for detection (px). When unset, the "
            "pipeline picks adaptively from SRT altitude (or a sensible default)."
        ),
    ),
    srt: Optional[UploadFile] = File(None, description="Optional DJI SRT telemetry sidecar."),
):
    if not ffmpeg_available():
        raise HTTPException(status_code=503, detail={"code": "ffmpeg_missing", "message": "ffmpeg is not on PATH."})
    if mode not in ("crown", "stem"):
        raise HTTPException(status_code=400, detail={"code": "bad_mode", "message": "mode must be 'crown' or 'stem'."})
    if fps <= 0 or fps > 5:
        raise HTTPException(status_code=400, detail={"code": "bad_fps", "message": "fps must be in (0, 5]."})

    job_id = new_job_id()
    workdir = workdir_for_job(_VIDEO_BASE, job_id)
    video_path = workdir / "input.mp4"
    srt_path = workdir / "telemetry.srt" if srt is not None else None

    with open(video_path, "wb") as f:
        f.write(await video.read())
    if srt is not None and srt_path is not None:
        with open(srt_path, "wb") as f:
            f.write(await srt.read())

    cleaned_hint = (species_hint or "").strip() or None
    override_width: Optional[int] = None
    if max_edge_px is not None and max_edge_px > 0:
        override_width = int(max_edge_px)

    job = VideoJob(
        job_id=job_id,
        mode=mode,  # type: ignore[arg-type]
        fps=fps,
        species_hint=cleaned_hint,
        same_species=same_species,
        frame_max_edge_px=override_width,
        created_at=time.time(),
    )
    _VIDEO_JOBS[job_id] = job
    _VIDEO_WORKDIRS[job_id] = workdir

    asyncio.create_task(
        run_video_pipeline(job, workdir=workdir, video_path=video_path, srt_path=srt_path)
    )

    logger.info(
        "scan-video accepted: job=%s mode=%s fps=%.2f video=%dKB srt=%s",
        job_id, mode, fps, video_path.stat().st_size // 1024, bool(srt_path),
    )
    return {"job_id": job_id, "status": job.status, "mode": job.mode}


@app.get("/scan-video/{job_id}", response_model=VideoJob)
async def scan_video_status(job_id: str) -> VideoJob:
    job = _VIDEO_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "job_not_found"})
    return job


@app.get("/scan-video/{job_id}/tree/{track_id}/image")
async def scan_video_tree_image(job_id: str, track_id: str):
    workdir = _VIDEO_WORKDIRS.get(job_id)
    if workdir is None:
        raise HTTPException(status_code=404, detail={"code": "job_not_found"})
    crop_path = workdir / "crops" / f"{track_id}.jpg"
    if not crop_path.exists():
        raise HTTPException(status_code=404, detail={"code": "crop_not_found"})
    return FileResponse(crop_path, media_type="image/jpeg")


@app.delete("/scan-video/{job_id}")
async def scan_video_delete(job_id: str):
    job = _VIDEO_JOBS.pop(job_id, None)
    workdir = _VIDEO_WORKDIRS.pop(job_id, None)
    if workdir is not None:
        cleanup_workdir(workdir)
    if job is None and workdir is None:
        raise HTTPException(status_code=404, detail={"code": "job_not_found"})
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=CONFIG.host,
        port=CONFIG.port,
        reload=False,
    )
