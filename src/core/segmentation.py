"""
Person segmentation for thumbnail generation.

Backend priority (auto-selected at import time):
  1. rembg   — U2Net ONNX model, best quality, ~1-3s GPU / ~4-8s CPU
  2. MediaPipe SelfieSegmentation — good for frontal talking-head, ~0.2s
  3. GrabCut (OpenCV) — fallback, no ML dependency, Python 3.14 compatible

The public API is identical regardless of backend:
  segment_person(img, face_box=None, ...) -> (rgba_image, alpha_mask)
  detect_face(img) -> Optional[(x, y, w, h)]

Backend is detected once at module import; the result is stored in
SEGMENTATION_BACKEND (str: "rembg" | "mediapipe" | "grabcut").
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image


# ── Backend detection ────────────────────────────────────────────────────────

def _detect_backend() -> str:
    try:
        import rembg  # noqa: F401
        return "rembg"
    except ImportError:
        pass
    try:
        import mediapipe  # noqa: F401
        return "mediapipe"
    except ImportError:
        pass
    return "grabcut"


SEGMENTATION_BACKEND: str = _detect_backend()


# ── Public API ───────────────────────────────────────────────────────────────

def segment_person(
    img: Image.Image,
    face_box: Optional[tuple[int, int, int, int]] = None,
    iterations: int = 3,
    feather_radius: int = 9,
    work_resolution: int = 720,
) -> tuple[Image.Image, np.ndarray]:
    """
    Segment the main person from *img* and return (rgba_subject, alpha_uint8).

    Backend used is SEGMENTATION_BACKEND (auto-detected at import).
    All backends return the alpha at the ORIGINAL image resolution.

    Args:
        img:              Input PIL Image (any mode).
        face_box:         (x, y, w, h) in pixels — hint for GrabCut rect.
                          Ignored by rembg/MediaPipe which are mask-based.
        iterations:       GrabCut iterations (ignored by other backends).
        feather_radius:   Gaussian feather radius applied to final alpha.
        work_resolution:  GrabCut / MediaPipe processing resolution.
    """
    if SEGMENTATION_BACKEND == "rembg":
        return _segment_rembg(img, feather_radius)
    if SEGMENTATION_BACKEND == "mediapipe":
        return _segment_mediapipe(img, feather_radius, work_resolution)
    return _segment_grabcut(img, face_box, iterations, feather_radius,
                            work_resolution)


def detect_face(img: Image.Image) -> Optional[tuple[int, int, int, int]]:
    """Public face detection — returns (x, y, w, h) or None."""
    bgr = np.array(img.convert("RGB"))[:, :, ::-1].copy()
    return _detect_face_cv(bgr)


def get_backend() -> str:
    """Return the active segmentation backend name."""
    return SEGMENTATION_BACKEND


# ── rembg backend ────────────────────────────────────────────────────────────

def _segment_rembg(
    img: Image.Image,
    feather_radius: int,
) -> tuple[Image.Image, np.ndarray]:
    """
    Uses the U2Net ONNX model via rembg.
    First call downloads ~170 MB model to ~/.u2net/; subsequent calls are fast.
    GPU is used automatically if onnxruntime-gpu is installed.
    """
    from rembg import remove as rembg_remove

    rgba = rembg_remove(img.convert("RGBA"))          # returns RGBA PIL Image
    alpha = np.array(rgba)[:, :, 3]                  # extract alpha channel

    if feather_radius > 0:
        import cv2
        k     = feather_radius * 2 + 1
        alpha = cv2.GaussianBlur(alpha, (k, k), feather_radius * 0.6)

    # Rebuild RGBA with (potentially feathered) alpha
    rgb  = np.array(img.convert("RGB"))
    rgba_arr = np.dstack([rgb, alpha])
    return Image.fromarray(rgba_arr, "RGBA"), alpha


# ── MediaPipe backend ─────────────────────────────────────────────────────────

# Module-level cache so we don't reload the model every call
_mp_segmenter = None


def _segment_mediapipe(
    img: Image.Image,
    feather_radius: int,
    work_resolution: int,
) -> tuple[Image.Image, np.ndarray]:
    """
    Uses MediaPipe SelfieSegmentation (model 1 = landscape, higher quality).
    Runs at work_resolution, upscales mask to original resolution.
    """
    import cv2
    import mediapipe as mp

    global _mp_segmenter
    if _mp_segmenter is None:
        _mp_segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(
            model_selection=1
        )

    rgb_full = np.array(img.convert("RGB"))
    H, W     = rgb_full.shape[:2]

    # Downscale for inference
    scale = work_resolution / max(H, W)
    if scale < 1.0:
        wW, wH   = int(W * scale), int(H * scale)
        rgb_small = cv2.resize(rgb_full, (wW, wH), interpolation=cv2.INTER_AREA)
    else:
        rgb_small = rgb_full
        wW, wH    = W, H

    result      = _mp_segmenter.process(rgb_small)
    mask_small  = (result.segmentation_mask * 255).astype(np.uint8)

    # Upscale to full resolution
    alpha = cv2.resize(mask_small, (W, H), interpolation=cv2.INTER_LINEAR)

    if feather_radius > 0:
        k     = feather_radius * 2 + 1
        alpha = cv2.GaussianBlur(alpha, (k, k), feather_radius * 0.6)

    rgb_arr  = np.array(img.convert("RGB"))
    rgba_arr = np.dstack([rgb_arr, alpha])
    return Image.fromarray(rgba_arr, "RGBA"), alpha


# ── GrabCut backend (default fallback) ───────────────────────────────────────

def _segment_grabcut(
    img: Image.Image,
    face_box: Optional[tuple[int, int, int, int]],
    iterations: int,
    feather_radius: int,
    work_resolution: int,
) -> tuple[Image.Image, np.ndarray]:
    """
    GrabCut initialised by face bounding rect.
    Runs at work_resolution (~720p) for ~4x speed vs full 1080p.
    Quality is good for talking-head shots with clear face detection.
    """
    import cv2

    rgb_full = np.array(img.convert("RGB"))
    bgr_full = cv2.cvtColor(rgb_full, cv2.COLOR_RGB2BGR)
    H, W     = bgr_full.shape[:2]

    # Downscale
    scale = work_resolution / max(H, W)
    if scale < 1.0:
        wW, wH    = int(W * scale), int(H * scale)
        bgr_small = cv2.resize(bgr_full, (wW, wH), interpolation=cv2.INTER_AREA)
    else:
        bgr_small = bgr_full
        scale     = 1.0
        wW, wH    = W, H

    # Face detection / fallback
    if face_box is None:
        face_box = _detect_face_cv(bgr_full)
    if face_box is None:
        fx_f, fy_f = W // 2, int(H * 0.30)
        fw_f, fh_f = int(W * 0.18), int(H * 0.22)
        face_box = (fx_f - fw_f // 2, fy_f - fh_f // 2, fw_f, fh_f)

    # Map face box to small resolution
    fx, fy, fw, fh = face_box
    sfx = int(fx * scale); sfy = int(fy * scale)
    sfw = int(fw * scale); sfh = int(fh * scale)

    # Body rect
    body_x = max(0, sfx - int(sfw * 1.2))
    body_y = max(0, sfy - int(sfh * 0.5))
    body_w = min(wW - body_x, int(sfw * 3.4))
    body_h = min(wH - body_y, int(sfh * 7.0))

    # GrabCut
    mask     = np.zeros((wH, wW), np.uint8)
    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)
    rect     = (body_x, body_y, body_w, body_h)
    try:
        cv2.grabCut(bgr_small, mask, rect, bg_model, fg_model,
                    iterations, cv2.GC_INIT_WITH_RECT)
    except Exception:
        # Fallback: rough alpha from face position
        alpha_full = np.zeros((H, W), np.uint8)
        alpha_full[fy:fy + fh * 5, max(0, fx - fw):fx + fw * 2] = 255
        return _apply_alpha(img, alpha_full), alpha_full

    alpha_small = np.where(
        (mask == cv2.GC_PR_FGD) | (mask == cv2.GC_FGD), 255, 0
    ).astype(np.uint8)

    # Morphological close + upscale
    kernel      = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    alpha_small = cv2.morphologyEx(alpha_small, cv2.MORPH_CLOSE, kernel,
                                   iterations=2)
    alpha_full  = cv2.resize(alpha_small, (W, H), interpolation=cv2.INTER_LINEAR)

    if feather_radius > 0:
        k          = feather_radius * 2 + 1
        alpha_full = cv2.GaussianBlur(alpha_full, (k, k), feather_radius * 0.6)

    return _apply_alpha(img, alpha_full), alpha_full


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_face_cv(bgr: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """Largest-face Haar cascade detection."""
    import cv2

    gray    = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4,
        minSize=(int(bgr.shape[1] * 0.04), int(bgr.shape[0] * 0.04)),
    )
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return int(x), int(y), int(w), int(h)


def _apply_alpha(img: Image.Image, alpha: np.ndarray) -> Image.Image:
    """Combine RGB + alpha mask into RGBA."""
    rgb  = np.array(img.convert("RGB"))
    rgba = np.dstack([rgb, alpha])
    return Image.fromarray(rgba, "RGBA")
