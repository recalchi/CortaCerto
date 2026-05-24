from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from .color_grade import ColorGrade
from .subject_tracking import SubjectMask, SubjectTracker, apply_subject_bokeh_bgr


# ── 3D LUT (.cube) support ────────────────────────────────────────────────────

_LUT_CACHE: dict[str, Optional[np.ndarray]] = {}   # path → (N,N,N,3) float32 or None


def _load_cube_lut(path: str) -> Optional[np.ndarray]:
    """Parse a .cube 3D LUT file, return float32 array of shape (N, N, N, 3).

    Index order: lut[r_i, g_i, b_i] = [r_out, g_out, b_out]
    (Adobe .cube spec: R index slowest, B index fastest)
    """
    if path in _LUT_CACHE:
        return _LUT_CACHE[path]

    result: Optional[np.ndarray] = None
    try:
        size: Optional[int] = None
        data_lines: list[list[float]] = []
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.upper().startswith("LUT_3D_SIZE"):
                    try:
                        size = int(line.split()[-1])
                    except ValueError:
                        pass
                    continue
                if line.upper().startswith("TITLE") or line.upper().startswith("DOMAIN"):
                    continue
                parts = line.split()
                if len(parts) == 3:
                    try:
                        data_lines.append([float(p) for p in parts])
                    except ValueError:
                        pass

        if size is not None and len(data_lines) >= size ** 3:
            arr = np.array(data_lines[: size ** 3], dtype=np.float32)
            # .cube order: B varies fastest → reshape (R, G, B, 3)
            result = arr.reshape(size, size, size, 3)

    except Exception:
        pass

    _LUT_CACHE[path] = result
    return result


def clear_lut_cache(path: Optional[str] = None) -> None:
    """Evict one or all entries from the LUT cache."""
    if path is None:
        _LUT_CACHE.clear()
    else:
        _LUT_CACHE.pop(path, None)


def _apply_lut_nn(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Apply a 3D LUT to an RGB float32 image via nearest-neighbour lookup."""
    n = lut.shape[0] - 1
    r_i = np.clip(np.round(rgb[..., 0] * n).astype(np.int32), 0, n)
    g_i = np.clip(np.round(rgb[..., 1] * n).astype(np.int32), 0, n)
    b_i = np.clip(np.round(rgb[..., 2] * n).astype(np.int32), 0, n)
    return lut[r_i, g_i, b_i].astype(np.float32)


# ── Public API ────────────────────────────────────────────────────────────────

def apply_video_effects_bgr(
    frame_bgr: np.ndarray,
    grade: ColorGrade | None,
    bokeh_intensity: float = 0.0,
    tracker: SubjectTracker | None = None,
) -> tuple[np.ndarray, SubjectMask | None]:
    result = frame_bgr
    subject: SubjectMask | None = None
    if bokeh_intensity >= 0.05:
        result, subject = apply_subject_bokeh_bgr(result, bokeh_intensity, tracker=tracker)
    if grade and grade.enabled:
        result = apply_color_grade_bgr(result, grade)
    return result, subject


def apply_color_grade_bgr(frame_bgr: np.ndarray, grade: ColorGrade) -> np.ndarray:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    luma = _luma(rgb)

    # ── Exposure / Brightness ──────────────────────────────────────────────
    exposure_gain = 2.0 ** (float(grade.brightness) / 100.0 * 0.85)
    rgb *= exposure_gain

    # ── Contrast ──────────────────────────────────────────────────────────
    contrast = 1.0 + float(grade.contrast) / 100.0 * 0.75
    rgb = (rgb - 0.5) * contrast + 0.5

    # ── Tonal masks ───────────────────────────────────────────────────────
    shadow_mask    = np.clip((0.55 - luma) / 0.55,  0.0, 1.0)[..., None]
    highlight_mask = np.clip((luma - 0.45) / 0.55,  0.0, 1.0)[..., None]
    white_mask     = np.clip((luma - 0.72) / 0.28,  0.0, 1.0)[..., None]
    black_mask     = np.clip((0.28 - luma) / 0.28,  0.0, 1.0)[..., None]
    midtone_mask   = np.clip(1.0 - 2.0 * np.abs(luma - 0.5), 0.0, 1.0)[..., None]

    # ── Basic tonal adjustments ───────────────────────────────────────────
    rgb += shadow_mask    * (float(grade.shadows)    / 100.0) * 0.18
    rgb += highlight_mask * (float(grade.highlights) / 100.0) * 0.14
    rgb += white_mask     * (float(grade.whites)     / 100.0) * 0.16
    rgb -= black_mask     * (float(grade.blacks)     / 100.0) * 0.14

    # ── Color wheels (lift / gamma / gain) ────────────────────────────────
    _WS = 0.020  # wheel scale: ±50 units × 0.020 = ±1.0 max shift
    if any(abs(v) > 0.01 for v in [grade.lift_r, grade.lift_g, grade.lift_b]):
        rgb[..., 0] += float(grade.lift_r) * _WS * shadow_mask[..., 0]
        rgb[..., 1] += float(grade.lift_g) * _WS * shadow_mask[..., 0]
        rgb[..., 2] += float(grade.lift_b) * _WS * shadow_mask[..., 0]

    if any(abs(v) > 0.01 for v in [grade.gamma_r, grade.gamma_g, grade.gamma_b]):
        rgb[..., 0] += float(grade.gamma_r) * _WS * midtone_mask[..., 0]
        rgb[..., 1] += float(grade.gamma_g) * _WS * midtone_mask[..., 0]
        rgb[..., 2] += float(grade.gamma_b) * _WS * midtone_mask[..., 0]

    if any(abs(v) > 0.01 for v in [grade.gain_r, grade.gain_g, grade.gain_b]):
        rgb[..., 0] += float(grade.gain_r) * _WS * highlight_mask[..., 0]
        rgb[..., 1] += float(grade.gain_g) * _WS * highlight_mask[..., 0]
        rgb[..., 2] += float(grade.gain_b) * _WS * highlight_mask[..., 0]

    # ── White balance (temperature + tint) ───────────────────────────────
    rgb = _apply_white_balance(rgb, grade.temperature, float(getattr(grade, "tint", 0.0)))

    # ── Hue shift ─────────────────────────────────────────────────────────
    if abs(float(grade.hue)) > 0.5:
        hsv = cv2.cvtColor(np.clip(rgb * 255.0, 0, 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
        hsv = hsv.astype(np.float32)
        hsv[..., 0] = (hsv[..., 0] + float(grade.hue) / 2.0) % 180.0
        rgb = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32) / 255.0

    # ── Saturation ────────────────────────────────────────────────────────
    gray = _luma(rgb)[..., None]
    saturation = 1.0 + float(grade.saturation) / 100.0
    rgb = gray + (rgb - gray) * saturation

    # ── Vibrance ──────────────────────────────────────────────────────────
    vibrance = float(getattr(grade, "vibrance", 0.0)) / 100.0
    if abs(vibrance) > 0.001:
        chroma = rgb.max(axis=2, keepdims=True) - rgb.min(axis=2, keepdims=True)
        rgb += (rgb - gray) * (1.0 - chroma) * vibrance * 0.8

    rgb = np.clip(rgb, 0.0, 1.0)

    # ── 3D LUT ────────────────────────────────────────────────────────────
    lut_path = getattr(grade, "lut_path", "")
    if lut_path and os.path.isfile(lut_path):
        lut = _load_cube_lut(lut_path)
        if lut is not None:
            rgb = np.clip(_apply_lut_nn(rgb, lut), 0.0, 1.0)

    # ── Sharpen ───────────────────────────────────────────────────────────
    if float(grade.sharpen) > 0.5:
        bgr = cv2.cvtColor((rgb * 255.0).astype(np.uint8), cv2.COLOR_RGB2BGR)
        blur = cv2.GaussianBlur(bgr, (0, 0), 1.2)
        amount = float(grade.sharpen) / 100.0 * 1.8
        bgr = cv2.addWeighted(bgr, 1.0 + amount, blur, -amount, 0)
        return np.clip(bgr, 0, 255).astype(np.uint8)

    return cv2.cvtColor((rgb * 255.0).astype(np.uint8), cv2.COLOR_RGB2BGR)


def apply_video_effects_pil(
    img: Image.Image,
    grade: ColorGrade | None,
    bokeh_intensity: float = 0.0,
    tracker: SubjectTracker | None = None,
) -> Image.Image:
    frame_bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    rendered, _subject = apply_video_effects_bgr(
        frame_bgr,
        grade=grade,
        bokeh_intensity=bokeh_intensity,
        tracker=tracker,
    )
    rgb = cv2.cvtColor(rendered, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def _apply_white_balance(rgb: np.ndarray, temperature: float, tint: float) -> np.ndarray:
    if abs(float(temperature)) < 0.5 and abs(float(tint)) < 0.5:
        return rgb

    temp = float(temperature) / 100.0
    tint_norm = float(tint) / 100.0
    base_luma = _luma(rgb)

    balanced = rgb.copy()
    balanced[..., 0] += temp * 0.10 + tint_norm * 0.07
    balanced[..., 1] -= tint_norm * 0.06
    balanced[..., 2] -= temp * 0.10 - tint_norm * 0.03

    delta = (_luma(balanced) - base_luma)[..., None]
    balanced -= delta * 0.85
    return np.clip(balanced, 0.0, 1.0)
