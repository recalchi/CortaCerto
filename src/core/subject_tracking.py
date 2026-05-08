from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class SubjectMask:
    mask: np.ndarray
    backend: str
    confidence: float


def _detect_backend() -> str:
    try:
        import mediapipe  # noqa: F401
        return "mediapipe"
    except ImportError:
        return "ellipse"


class SubjectTracker:
    def __init__(
        self,
        work_resolution: int = 640,
        temporal_smoothing: float = 0.82,
    ) -> None:
        self.work_resolution = work_resolution
        self.temporal_smoothing = temporal_smoothing
        self.backend = _detect_backend()
        self._lock = threading.Lock()
        self._segmenter = None
        self._prev_mask: Optional[np.ndarray] = None
        self._face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

    def reset(self) -> None:
        with self._lock:
            self._prev_mask = None

    def segment_bgr(self, frame_bgr: np.ndarray) -> SubjectMask:
        with self._lock:
            if self.backend == "mediapipe":
                mask = self._segment_mediapipe(frame_bgr)
            else:
                mask = self._segment_ellipse(frame_bgr)

            if self._prev_mask is not None and self._prev_mask.shape == mask.shape:
                mask = (
                    self._prev_mask * self.temporal_smoothing
                    + mask * (1.0 - self.temporal_smoothing)
                )
            self._prev_mask = mask
            confidence = float(mask.mean())
            return SubjectMask(mask=mask, backend=self.backend, confidence=confidence)

    def _segment_mediapipe(self, frame_bgr: np.ndarray) -> np.ndarray:
        import mediapipe as mp

        if self._segmenter is None:
            self._segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(
                model_selection=1
            )

        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self.work_resolution / max(h, w))
        if scale < 1.0:
            resized = cv2.resize(
                frame_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
            )
        else:
            resized = frame_bgr

        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        result = self._segmenter.process(rgb)
        mask_small = np.asarray(result.segmentation_mask, dtype=np.float32)
        mask_small = np.clip((mask_small - 0.08) / 0.72, 0.0, 1.0)
        mask_small = cv2.GaussianBlur(mask_small, (0, 0), 1.2)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask_small = cv2.morphologyEx(mask_small, cv2.MORPH_CLOSE, kernel)
        mask = cv2.resize(mask_small, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.clip(mask, 0.0, 1.0)

    def _segment_ellipse(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        face_box = self._detect_face(frame_bgr)
        if face_box is None:
            face_box = (
                int(w * 0.39),
                int(h * 0.14),
                int(w * 0.22),
                int(h * 0.24),
            )

        x, y, fw, fh = face_box
        center_x = x + fw * 0.5
        center_y = y + fh * 1.9
        radius_x = max(fw * 1.7, w * 0.18)
        radius_y = max(fh * 3.8, h * 0.34)

        xs = np.arange(w, dtype=np.float32)[None, :]
        ys = np.arange(h, dtype=np.float32)[:, None]
        dist = np.sqrt(
            ((xs - center_x) / max(1.0, radius_x)) ** 2
            + ((ys - center_y) / max(1.0, radius_y)) ** 2
        )
        mask = np.clip((1.18 - dist) / 0.45, 0.0, 1.0)
        return cv2.GaussianBlur(mask, (0, 0), 5.0)

    def _detect_face(self, frame_bgr: np.ndarray) -> Optional[tuple[int, int, int, int]]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(max(20, frame_bgr.shape[1] // 20), max(20, frame_bgr.shape[0] // 20)),
        )
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda item: item[2] * item[3])
        return int(x), int(y), int(w), int(h)


def apply_subject_bokeh_bgr(
    frame_bgr: np.ndarray,
    intensity: float,
    tracker: SubjectTracker | None = None,
) -> tuple[np.ndarray, SubjectMask]:
    tracker = tracker or SubjectTracker()
    subject = tracker.segment_bgr(frame_bgr)
    if intensity < 0.05:
        return frame_bgr, subject

    sigma = max(1.5, 2.0 + intensity * 16.0)
    blurred = cv2.GaussianBlur(frame_bgr, (0, 0), sigmaX=sigma, sigmaY=sigma)
    alpha = np.clip(subject.mask[..., None], 0.0, 1.0)
    composite = frame_bgr.astype(np.float32) * alpha + blurred.astype(np.float32) * (1.0 - alpha)
    return np.clip(composite, 0, 255).astype(np.uint8), subject
