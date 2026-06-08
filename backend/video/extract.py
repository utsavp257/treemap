"""Frame extraction (ffmpeg) + DJI SRT telemetry parsing.

`extract_frames` writes JPEGs to `frames/` at the configured fps and
returns a manifest of frame index → (timestamp_s, file_path, optional
telemetry).

DJI drones (Mavic, Mini, Air) record an .srt sidecar with per-frame
GPS/altitude/gimbal data. We parse two known formats best-effort. If
no SRT is supplied the telemetry list comes back empty and the
pipeline degrades to pixel-space mode automatically.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Resolution planning ──────────────────────────────────────────────────
#
# Gemini bills images by 768x768 tiles, not by pixels. Sending a 4K frame
# costs ~15× more tokens than a 1024-wide frame but adds zero detection
# accuracy past a saturation point.
#
# Saturation point depends on drone altitude: lower altitude → each tree
# already fills many pixels, so we don't need a huge frame to detect them.

# Pixels of crown detail we target per crown for the DETECTION pass.
# 80 gives Gemini margin without going near 4K — well above the ~30-50
# detection threshold so we don't miss smaller crowns.
TARGET_PIXELS_PER_5M_CROWN = 80

# Hard bounds on resolution to keep us out of edge cases.
MIN_TARGET_WIDTH = 768       # 1 Gemini tile — anything below saves nothing.
MAX_TARGET_WIDTH = 1920      # 1080p; further is wasted detail.

# Conservative default when no altitude info is available (no SRT, or
# SRT lacks rel_alt fields). Sized for ~30-75m AGL typical crown surveys.
DEFAULT_TARGET_WIDTH = 1280

# Common consumer-drone HFOV (DJI Mavic/Mini/Air sit around 80-84°).
DEFAULT_HFOV_DEG = 80.0


def compute_target_width(
    altitude_m: Optional[float],
    *,
    hfov_deg: float = DEFAULT_HFOV_DEG,
    target_pixels_per_5m: int = TARGET_PIXELS_PER_5M_CROWN,
    min_w: int = MIN_TARGET_WIDTH,
    max_w: int = MAX_TARGET_WIDTH,
    default_w: int = DEFAULT_TARGET_WIDTH,
) -> int:
    """Pick a frame width that keeps every ~5m crown above the detection
    saturation point at this drone altitude.

    Derivation:
        ground_width_m = 2 * altitude * tan(hfov / 2)
        pixels_per_meter = target_pixels_per_5m / 5
        required_width  = ground_width_m * pixels_per_meter
    Clamped to [min_w, max_w].
    """
    if not altitude_m or altitude_m <= 0:
        return default_w

    ground_width_m = 2.0 * altitude_m * math.tan(math.radians(hfov_deg) / 2.0)
    pixels_per_meter = target_pixels_per_5m / 5.0
    target = int(ground_width_m * pixels_per_meter)
    return max(min_w, min(max_w, target))


def median_altitude(samples: list[Telemetry]) -> Optional[float]:
    """Median altitude across the SRT track. Robust to single-frame
    outliers (drone landing, take-off frames)."""
    alts = [s.rel_alt_m for s in samples if s.rel_alt_m is not None and s.rel_alt_m > 0]
    if not alts:
        return None
    return float(statistics.median(alts))


# ── Telemetry types ──────────────────────────────────────────────────────
@dataclass
class Telemetry:
    timestamp_s: float
    lat: Optional[float]
    lng: Optional[float]
    rel_alt_m: Optional[float]
    gimbal_pitch_deg: Optional[float]


@dataclass
class Frame:
    index: int
    timestamp_s: float
    path: Path
    telemetry: Optional[Telemetry]


# ── ffmpeg ───────────────────────────────────────────────────────────────
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_frames(
    video_path: Path,
    out_dir: Path,
    *,
    fps: float = 0.5,
    max_storage_width: Optional[int] = 3840,
) -> list[Path]:
    """Extract frames at the source's native resolution (or capped at
    ``max_storage_width`` for sanity — 4K by default).

    The detection pass downsamples in memory before sending to Gemini
    (cheap input). Atomic-tool crops are taken from THESE full-resolution
    JPEGs on disk (full detail). That's how we keep diagnosis quality
    high without paying 4K-image-token cost for every detection call.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "frame_%05d.jpg"

    if max_storage_width:
        vfilter = (
            f"fps={fps},"
            f"scale='if(gt(iw,{max_storage_width}),{max_storage_width},iw)':-2:flags=lanczos"
        )
    else:
        vfilter = f"fps={fps}"

    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-i", str(video_path),
        "-vf", vfilter,
        "-q:v", "2",  # higher quality JPEG since these feed atomic-tool crops
        str(pattern),
    ]
    logger.info("ffmpeg extract: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={result.returncode}): {result.stderr.strip()}")

    return sorted(out_dir.glob("frame_*.jpg"))


# ── SRT parsing ──────────────────────────────────────────────────────────
_SRT_TIME_RX = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")

# Older DJI format: "[GPS (35.000, -118.000, 100.5)] [BAROMETER:101.5]"
_GPS_OLD_RX = re.compile(r"GPS\s*\(\s*(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)\s*\)")
# Newer DJI format: "[latitude: 35.000] [longitude: -118.000] [rel_alt: 50.0 abs_alt: 100.0]"
_LAT_NEW_RX = re.compile(r"latitude\s*:\s*(-?\d+\.\d+)")
_LNG_NEW_RX = re.compile(r"longitude\s*:\s*(-?\d+\.\d+)")
_ALT_NEW_RX = re.compile(r"rel_alt\s*:\s*(-?\d+\.\d+)")
_GIMBAL_NEW_RX = re.compile(r"gb_pitch\s*:\s*(-?\d+\.\d+)")


def _parse_timecode(s: str) -> Optional[float]:
    m = _SRT_TIME_RX.search(s)
    if not m:
        return None
    h, mn, sec, ms = (int(x) for x in m.groups())
    return h * 3600 + mn * 60 + sec + ms / 1000.0


def parse_srt(srt_path: Path) -> list[Telemetry]:
    """Parse a DJI SRT sidecar, returning telemetry samples in order."""
    try:
        text = srt_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("Could not read SRT %s: %s", srt_path, e)
        return []

    # Split into SRT blocks separated by blank lines.
    blocks = re.split(r"\n\s*\n", text.strip())
    samples: list[Telemetry] = []

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        # The second line is "HH:MM:SS,xxx --> HH:MM:SS,xxx".
        timeline = lines[1] if "-->" in lines[1] else next((ln for ln in lines if "-->" in ln), "")
        start_s = _parse_timecode(timeline.split("-->")[0]) if "-->" in timeline else None
        if start_s is None:
            continue

        body = " ".join(lines[2:])
        lat = lng = alt = gimbal = None

        if (m := _GPS_OLD_RX.search(body)):
            lat = float(m.group(1))
            lng = float(m.group(2))
            alt = float(m.group(3))
        else:
            if (m := _LAT_NEW_RX.search(body)):
                lat = float(m.group(1))
            if (m := _LNG_NEW_RX.search(body)):
                lng = float(m.group(1))
            if (m := _ALT_NEW_RX.search(body)):
                alt = float(m.group(1))

        if (m := _GIMBAL_NEW_RX.search(body)):
            gimbal = float(m.group(1))

        samples.append(
            Telemetry(
                timestamp_s=start_s,
                lat=lat,
                lng=lng,
                rel_alt_m=alt,
                gimbal_pitch_deg=gimbal,
            )
        )

    logger.info("Parsed %d telemetry samples from %s", len(samples), srt_path.name)
    return samples


def telemetry_for_timestamp(
    samples: list[Telemetry], t_s: float
) -> Optional[Telemetry]:
    """Find the SRT sample closest to a given timestamp (seconds)."""
    if not samples:
        return None
    return min(samples, key=lambda s: abs(s.timestamp_s - t_s))


# ── Public bundler ──────────────────────────────────────────────────────
def build_frame_manifest(
    frame_paths: list[Path],
    fps: float,
    srt_samples: list[Telemetry],
) -> list[Frame]:
    """Pair extracted frames with their nearest telemetry sample."""
    frames: list[Frame] = []
    for i, path in enumerate(frame_paths):
        t = (i + 0.5) / fps  # midpoint of the sampling window
        tele = telemetry_for_timestamp(srt_samples, t)
        frames.append(Frame(index=i, timestamp_s=t, path=path, telemetry=tele))
    return frames
