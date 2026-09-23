from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from .models import ManualCropOverride, ReframePoint, ReframeTrack
from .time import MediaTime


@dataclass(frozen=True, slots=True)
class Detection:
    at: MediaTime
    center_x: float
    center_y: float
    confidence: float


class SubjectDetector(Protocol):
    def detect(self, frames: Iterable[tuple[MediaTime, object]]) -> Iterable[Detection | None]: ...


class OpenCVFaceDetector:
    """Optional proxy-frame detector; output coordinates are normalized to the analyzed frame."""

    def __init__(self, cascade_path: str | None = None) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is not installed") from exc
        self._cv2 = cv2
        path = cascade_path or str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        self._cascade = cv2.CascadeClassifier(path)
        if self._cascade.empty():
            raise RuntimeError("OpenCV face cascade could not be loaded")

    def detect(self, frames: Iterable[tuple[MediaTime, object]]) -> Iterable[Detection | None]:
        for at, frame in frames:
            height, width = frame.shape[:2]
            gray = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)
            faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
            if len(faces) == 0:
                yield None
                continue
            x, y, face_width, face_height = max(faces, key=lambda box: int(box[2]) * int(box[3]))
            yield Detection(at, (x + face_width / 2) / width, (y + face_height / 2) / height, 1.0)


@dataclass(frozen=True, slots=True)
class CoordinateMapper:
    source_width: int
    source_height: int
    proxy_width: int
    proxy_height: int

    def proxy_to_source(self, x: float, y: float) -> tuple[float, float]:
        return x * self.source_width / self.proxy_width, y * self.source_height / self.proxy_height

    def normalized_proxy_to_source_normalized(self, x: float, y: float) -> tuple[float, float]:
        px, py = self.proxy_to_source(x * self.proxy_width, y * self.proxy_height)
        return px / self.source_width, py / self.source_height


def build_reframe_track(
    track_id: str,
    timestamps: list[MediaTime],
    detections: list[Detection | None],
    *,
    smoothing_alpha: float = 0.2,
    default_x: float = 0.5,
    default_y: float = 0.5,
    max_lost_frames: int = 30,
    manual_override: ManualCropOverride | None = None,
) -> ReframeTrack:
    if len(timestamps) != len(detections):
        raise ValueError("timestamps and detections must have equal length")
    if not 0 < smoothing_alpha <= 1:
        raise ValueError("smoothing_alpha must be in (0, 1]")
    points: list[ReframePoint] = []
    smooth_x, smooth_y = default_x, default_y
    lost = max_lost_frames + 1
    override = manual_override or ManualCropOverride()
    for at, detection in zip(timestamps, detections, strict=True):
        if override.enabled:
            target_x, target_y, confidence, detected = override.crop_x, override.crop_y, None, False
        elif detection is not None:
            target_x, target_y, confidence, detected = detection.center_x, detection.center_y, detection.confidence, True
            lost = 0
        else:
            lost += 1
            target_x, target_y, confidence, detected = (smooth_x, smooth_y, None, False) if lost <= max_lost_frames else (default_x, default_y, None, False)
        smooth_x += smoothing_alpha * (target_x - smooth_x)
        smooth_y += smoothing_alpha * (target_y - smooth_y)
        points.append(ReframePoint(
            at=at, subject_x=target_x, subject_y=target_y, crop_x=smooth_x, crop_y=smooth_y,
            scale=override.scale if override.enabled else 1.0, confidence=confidence, detected=detected,
        ))
    return ReframeTrack(
        id=track_id,
        points=points,
        smoothing={"algorithm": "exponential_moving_average", "alpha": smoothing_alpha, "max_lost_frames": max_lost_frames, "lost_behavior": "hold_then_ease_to_center"},
        manual_override=override,
    )


def crop_geometry(source_width: int, source_height: int, center_x: float, center_y: float, *, aspect_width: int = 9, aspect_height: int = 16) -> tuple[int, int, int, int]:
    crop_height = source_height
    crop_width = int(round(crop_height * aspect_width / aspect_height))
    if crop_width > source_width:
        crop_width = source_width
        crop_height = int(round(crop_width * aspect_height / aspect_width))
    x = round(center_x * source_width - crop_width / 2)
    y = round(center_y * source_height - crop_height / 2)
    return max(0, min(x, source_width - crop_width)), max(0, min(y, source_height - crop_height)), crop_width, crop_height
