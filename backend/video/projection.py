"""Pixel-to-world projection for nadir drone cameras.

Only correct when the camera is nadir-pointing (gimbal_pitch ≈ -90°).
For stem-mode footage (horizontal camera) we skip projection entirely
— there's no meaningful frame-center → lat/lng mapping.

Assumptions for the simple model:
- Gimbal at ~-90° (looking straight down).
- Frame center = drone GPS position.
- Ground sampling distance derived from altitude + assumed FOV.
- Earth treated as locally flat (true within hundreds of metres).
"""

from __future__ import annotations

import math
from typing import Optional

from .extract import Telemetry


# Reasonable defaults for the common DJI consumer drones.
# (DJI Mavic 3 / Air 3 horizontal FOV ≈ 73-84° depending on lens; we
# pick 80° as a middle estimate. Tune per-drone via env later if needed.)
_DEFAULT_HFOV_DEG = 80.0


def is_nadir(t: Telemetry, tolerance_deg: float = 12.0) -> bool:
    if t.gimbal_pitch_deg is None:
        return False
    return abs(t.gimbal_pitch_deg - (-90.0)) <= tolerance_deg


def project_bbox_centroid(
    bbox_0_1000: list[int],
    t: Telemetry,
    *,
    hfov_deg: float = _DEFAULT_HFOV_DEG,
    aspect_ratio: float = 16.0 / 9.0,
) -> Optional[tuple[float, float]]:
    """Project a normalized-bbox centroid to lat/lng.

    Returns None if the telemetry is incomplete or the camera isn't
    nadir-pointing.
    """
    if t.lat is None or t.lng is None or t.rel_alt_m is None:
        return None
    if not is_nadir(t):
        return None

    cy = (bbox_0_1000[0] + bbox_0_1000[2]) / 2.0 / 1000.0   # 0..1
    cx = (bbox_0_1000[1] + bbox_0_1000[3]) / 2.0 / 1000.0   # 0..1

    # Offset from frame center, in fractions of half-width / half-height.
    dx_frac = (cx - 0.5) * 2.0
    dy_frac = (cy - 0.5) * 2.0

    half_h_rad = math.radians(hfov_deg) / 2.0
    # Half-width on the ground (m) at this altitude.
    half_w_ground_m = t.rel_alt_m * math.tan(half_h_rad)
    half_h_ground_m = half_w_ground_m / aspect_ratio

    # Easting / northing offsets in meters. Image x → east, image y (top-down) → south.
    east_m = dx_frac * half_w_ground_m
    south_m = dy_frac * half_h_ground_m

    # Convert meters to degrees. 1° latitude ≈ 111,320 m.
    lat = t.lat - south_m / 111_320.0
    cos_lat = math.cos(math.radians(t.lat)) or 1e-9
    lng = t.lng + east_m / (111_320.0 * cos_lat)
    return lat, lng
