"""
Smart frame selection for thumbnails.

Scores each candidate frame on:
  - Face presence and size (40 %)
  - Sharpness via variance-of-Laplacian (30 %)
  - Composition (rule-of-thirds, face position) (20 %)
  - Brightness sanity (10 %)

Returns the top-K frames sorted by score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image


@dataclass
class FrameScore:
    timestamp_s:   float
    score:         float
    face_box:      Optional[tuple[int, int, int, int]]   # (x, y, w, h) pixels
    sharpness:     float
    brightness:    float
    composition:   float

    @property
    def has_face(self) -> bool:
        return self.face_box is not None


def score_frame(
    img: Image.Image,
    timestamp_s: float = 0.0,
) -> FrameScore:
    """Score a single frame on all metrics. Pure function."""
    import cv2

    bgr  = np.array(img.convert("RGB"))[:, :, ::-1].copy()
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # ── Face detection ──────────────────────────────────────────────────────
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4,
        minSize=(int(w * 0.04), int(h * 0.04)),
    )
    face_box = None
    face_score = 0.0
    composition = 0.0
    if len(faces) > 0:
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        face_box = (int(x), int(y), int(fw), int(fh))
        face_area_pct = (fw * fh) / (w * h)
        # Optimal face area: 8-15 % of frame (close-up but not extreme)
        face_score = _gaussian_score(face_area_pct, mu=0.10, sigma=0.07)

        # Composition: rule-of-thirds + not-cropped
        cx_norm = (x + fw / 2) / w
        cy_norm = (y + fh / 2) / h
        # Best Y for face: ~33 % from top (head-and-shoulders shot)
        comp_y = 1.0 - min(1.0, abs(cy_norm - 0.33) * 2.5)
        # Best X: 33 % or 66 % (rule of thirds), or center is OK too
        comp_x = max(
            1.0 - min(1.0, abs(cx_norm - 0.33) * 2.5),
            1.0 - min(1.0, abs(cx_norm - 0.66) * 2.5),
            1.0 - min(1.0, abs(cx_norm - 0.50) * 1.5) * 0.7,
        )
        composition = (comp_x + comp_y) / 2.0
    # ── Sharpness (variance of Laplacian) ───────────────────────────────────
    lap        = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness  = float(lap.var())
    # Normalise: typical 1080p talking head ~80-300, blurry < 50
    sharp_norm = min(1.0, sharpness / 250.0)

    # ── Brightness (avg luminance) ──────────────────────────────────────────
    brightness = float(gray.mean())
    # Penalise too-dark or too-bright frames (under 60 / over 200)
    bright_norm = _gaussian_score(brightness / 255.0, mu=0.50, sigma=0.30)

    # ── Weighted total ──────────────────────────────────────────────────────
    score = (
        face_score   * 0.40 +
        sharp_norm   * 0.30 +
        composition  * 0.20 +
        bright_norm  * 0.10
    ) * 100.0

    if face_box is None:
        score *= 0.35   # heavy penalty for face-less frames

    if sharpness < 40:
        score -= 30     # additional blur penalty

    return FrameScore(
        timestamp_s = timestamp_s,
        score       = max(0.0, score),
        face_box    = face_box,
        sharpness   = sharpness,
        brightness  = brightness,
        composition = composition,
    )


def _gaussian_score(value: float, mu: float, sigma: float) -> float:
    """Gaussian centered on mu — peaks at 1.0 at value=mu."""
    return float(np.exp(-((value - mu) ** 2) / (2 * sigma ** 2)))


# ── Public: extract candidate frames and score them ────────────────────────

def select_best_frames(
    video_path: str,
    count: int = 5,
    sample_every_s: float = 3.0,
    skip_first_s: float = 1.0,
    skip_last_s: float = 1.5,
    score_resolution: int = 480,
) -> list[FrameScore]:
    """
    Sample frames every sample_every_s seconds via cv2.VideoCapture (no
    ffmpeg subprocess), score each at score_resolution (downscaled for speed),
    return the top `count` frames by composite score.

    On a 137-s video sampled every 3 s at 480 p, this completes in ~3-5 s
    (vs. ~45 s with the ffmpeg-subprocess approach).
    """
    import cv2
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    duration_s = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, cap.get(cv2.CAP_PROP_FPS))
    if duration_s <= 0:
        cap.release()
        return []

    end_t = max(0.0, duration_s - skip_last_s)
    times: list[float] = []
    t = skip_first_s
    while t < end_t:
        times.append(t)
        t += sample_every_s

    scored: list[FrameScore] = []
    for ts in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        ok, bgr = cap.read()
        if not ok or bgr is None:
            continue
        try:
            scored.append(_score_bgr_fast(bgr, ts, score_resolution))
        except Exception:
            continue

    cap.release()
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:count]


def _score_bgr_fast(
    bgr_full: "np.ndarray",
    timestamp_s: float,
    score_resolution: int,
) -> FrameScore:
    """
    Optimised scoring on a downscaled BGR frame.
    Face box returned in ORIGINAL (full-resolution) pixel coordinates.
    """
    import cv2

    h_full, w_full = bgr_full.shape[:2]
    scale = score_resolution / max(h_full, w_full)
    if scale < 1.0:
        nw, nh = int(w_full * scale), int(h_full * scale)
        bgr = cv2.resize(bgr_full, (nw, nh), interpolation=cv2.INTER_AREA)
    else:
        bgr   = bgr_full
        scale = 1.0

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4,
        minSize=(int(w * 0.04), int(h * 0.04)),
    )
    face_box = None
    face_score = 0.0
    composition = 0.0
    if len(faces) > 0:
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        # Map back to full resolution coordinates
        face_box = (int(x / scale), int(y / scale),
                    int(fw / scale), int(fh / scale))
        face_area_pct = (fw * fh) / (w * h)
        face_score = _gaussian_score(face_area_pct, mu=0.10, sigma=0.07)

        cx_norm = (x + fw / 2) / w
        cy_norm = (y + fh / 2) / h
        comp_y = 1.0 - min(1.0, abs(cy_norm - 0.33) * 2.5)
        comp_x = max(
            1.0 - min(1.0, abs(cx_norm - 0.33) * 2.5),
            1.0 - min(1.0, abs(cx_norm - 0.66) * 2.5),
            1.0 - min(1.0, abs(cx_norm - 0.50) * 1.5) * 0.7,
        )
        composition = (comp_x + comp_y) / 2.0

    sharpness  = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharp_norm = min(1.0, sharpness / 250.0)
    brightness = float(gray.mean())
    bright_norm = _gaussian_score(brightness / 255.0, mu=0.50, sigma=0.30)

    score = (
        face_score   * 0.40 +
        sharp_norm   * 0.30 +
        composition  * 0.20 +
        bright_norm  * 0.10
    ) * 100.0
    if face_box is None:
        score *= 0.35
    if sharpness < 40:
        score -= 30

    return FrameScore(
        timestamp_s=timestamp_s,
        score=max(0.0, score),
        face_box=face_box,
        sharpness=sharpness,
        brightness=brightness,
        composition=composition,
    )
