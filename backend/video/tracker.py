"""IoU + centroid tracker — stable per-video tree IDs across frames.

A simple online tracker: for each new frame, match incoming detections
to existing live tracks by IoU; unmatched detections start new tracks;
tracks with no match for `max_misses` frames are closed.

Sufficient for drone footage where trees move slowly across the frame
(the drone moves, not the trees). No appearance embedding needed.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import HealthStatus
from .detector import FrameDetection

logger = logging.getLogger(__name__)


@dataclass
class TrackState:
    track_id: str
    bbox: list[int]  # [ymin, xmin, ymax, xmax] 0-1000 — latest frame's bbox
    last_seen_frame: int
    misses: int
    # Per-track accumulated info
    frame_indices: list[int] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    statuses: list[HealthStatus] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    bboxes_by_frame: dict[int, list[int]] = field(default_factory=dict)


def _iou(a: list[int], b: list[int]) -> float:
    ay1, ax1, ay2, ax2 = a
    by1, bx1, by2, bx2 = b
    inter_y1 = max(ay1, by1)
    inter_x1 = max(ax1, bx1)
    inter_y2 = min(ay2, by2)
    inter_x2 = min(ax2, bx2)
    iw = max(0, inter_x2 - inter_x1)
    ih = max(0, inter_y2 - inter_y1)
    inter = iw * ih
    area_a = max(0, ay2 - ay1) * max(0, ax2 - ax1)
    area_b = max(0, by2 - by1) * max(0, bx2 - bx1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _centroid_dist(a: list[int], b: list[int]) -> float:
    acy, acx = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    bcy, bcx = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    return math.hypot(acy - bcy, acx - bcx)


class Tracker:
    """Greedy IoU-first matcher with centroid-distance tiebreak.

    Configuration:
        iou_threshold:      minimum IoU to bind a detection to a track.
        centroid_fallback:  if no track has IoU >= threshold, accept the
                            closest centroid within `centroid_dist_max`
                            (handles fast drone movement that separates
                            bboxes between samples).
        max_misses:         a track closed after this many consecutive
                            frames without a match.
    """

    def __init__(
        self,
        iou_threshold: float = 0.30,
        centroid_dist_max: float = 80.0,   # in normalized 0-1000 space
        max_misses: int = 2,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.centroid_dist_max = centroid_dist_max
        self.max_misses = max_misses
        self._live: dict[str, TrackState] = {}
        self._closed: dict[str, TrackState] = {}

    def update(self, frame_index: int, detections: list[FrameDetection]) -> None:
        if not detections:
            self._age_unmatched(frame_index, matched_ids=set())
            return

        # Build a match-score matrix between every live track and every detection.
        live_ids = list(self._live.keys())
        scores: list[tuple[float, str, int]] = []  # (priority, track_id, det_idx)

        for tid in live_ids:
            tstate = self._live[tid]
            for di, det in enumerate(detections):
                iou = _iou(tstate.bbox, det.box_2d)
                if iou >= self.iou_threshold:
                    scores.append((-iou, tid, di))  # negative so smaller is better
                else:
                    cd = _centroid_dist(tstate.bbox, det.box_2d)
                    if cd <= self.centroid_dist_max:
                        # Use distance directly; iou-matched pairs always win
                        # because their priority is negative.
                        scores.append((cd, tid, di))

        scores.sort(key=lambda s: s[0])

        matched_track_ids: set[str] = set()
        matched_det_idxs: set[int] = set()

        for _, tid, di in scores:
            if tid in matched_track_ids or di in matched_det_idxs:
                continue
            matched_track_ids.add(tid)
            matched_det_idxs.add(di)
            self._extend_track(tid, frame_index, detections[di])

        # Detections that did not match any live track become new tracks.
        for di, det in enumerate(detections):
            if di in matched_det_idxs:
                continue
            self._open_track(frame_index, det)

        self._age_unmatched(frame_index, matched_ids=matched_track_ids)

    def _extend_track(
        self, track_id: str, frame_index: int, det: FrameDetection
    ) -> None:
        s = self._live[track_id]
        s.bbox = det.box_2d
        s.last_seen_frame = frame_index
        s.misses = 0
        s.frame_indices.append(frame_index)
        s.confidences.append(det.confidence)
        s.labels.append(det.label)
        s.statuses.append(det.status)
        for sym in det.symptoms:
            if sym not in s.symptoms:
                s.symptoms.append(sym)
        s.bboxes_by_frame[frame_index] = det.box_2d

    def _open_track(self, frame_index: int, det: FrameDetection) -> None:
        tid = uuid.uuid4().hex[:8]
        self._live[tid] = TrackState(
            track_id=tid,
            bbox=det.box_2d,
            last_seen_frame=frame_index,
            misses=0,
            frame_indices=[frame_index],
            confidences=[det.confidence],
            labels=[det.label],
            statuses=[det.status],
            symptoms=list(det.symptoms),
            bboxes_by_frame={frame_index: det.box_2d},
        )

    def _age_unmatched(self, frame_index: int, matched_ids: set[str]) -> None:
        for tid in list(self._live.keys()):
            if tid in matched_ids:
                continue
            s = self._live[tid]
            s.misses += 1
            if s.misses > self.max_misses:
                self._closed[tid] = s
                self._live.pop(tid)

    def finalize(self) -> list[TrackState]:
        """Return every track ever opened — live ones become closed."""
        self._closed.update(self._live)
        self._live.clear()
        return list(self._closed.values())


# ── Helpers used by the pipeline after tracking ──────────────────────────
def majority_status(statuses: list[HealthStatus]) -> HealthStatus:
    """Pick the most severe non-healthy status seen, falling back to healthy."""
    rank = {"healthy": 0, "monitor": 1, "treat": 2, "cut": 3}
    return max(statuses, key=lambda s: rank.get(s, 0)) if statuses else "healthy"


def best_representative_frame(track: TrackState) -> Optional[int]:
    """Pick the frame with the highest detection confidence."""
    if not track.frame_indices:
        return None
    paired = sorted(
        zip(track.confidences, track.frame_indices), key=lambda p: p[0], reverse=True
    )
    return paired[0][1]
