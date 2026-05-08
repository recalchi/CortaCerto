from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from .color_grade import ColorGrade
from .subject_tracking import SubjectMask, SubjectTracker, apply_subject_bokeh_bgr


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

    exposure_gain = 2.0 ** (float(grade.brightness) / 100.0 * 0.85)
    rgb *= exposure_gain

    contrast = 1.0 + float(grade.contrast) / 100.0 * 0.75
    rgb = (rgb - 0.5) * contrast + 0.5

    shadow_mask = np.clip((0.55 - luma) / 0.55, 0.0, 1.0)[..., None]
    highlight_mask = np.clip((luma - 0.45) / 0.55, 0.0, 1.0)[..., None]
    white_mask = np.clip((luma - 0.72) / 0.28, 0.0, 1.0)[..., None]
    black_mask = np.clip((0.28 - luma) / 0.28, 0.0, 1.0)[..., None]

    rgb += shadow_mask * (float(grade.shadows) / 100.0) * 0.18
    rgb += highlight_mask * (float(grade.highlights) / 100.0) * 0.14
    rgb += white_mask * (float(grade.whites) / 100.0) * 0.16
    rgb -= black_mask * (float(grade.blacks) / 100.0) * 0.14

    rgb = _apply_white_balance(rgb, grade.temperature, getattr(grade, "tint", 0.0))

    if abs(float(grade.hue)) > 0.5:
        hsv = cv2.cvtColor(np.clip(rgb * 255.0, 0, 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
        hsv = hsv.astype(np.float32)
        hsv[..., 0] = (hsv[..., 0] + float(grade.hue) / 2.0) % 180.0
        rgb = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32) / 255.0

    gray = _luma(rgb)[..., None]
    saturation = 1.0 + float(grade.saturation) / 100.0
    rgb = gray + (rgb - gray) * saturation

    vibrance = float(getattr(grade, "vibrance", 0.0)) / 100.0
    if abs(vibrance) > 0.001:
        chroma = rgb.max(axis=2, keepdims=True) - rgb.min(axis=2, keepdims=True)
        rgb += (rgb - gray) * (1.0 - chroma) * vibrance * 0.8

    rgb = np.clip(rgb, 0.0, 1.0)

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
