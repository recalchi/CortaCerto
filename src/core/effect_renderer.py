from __future__ import annotations

import subprocess
import threading
import contextlib
from typing import Callable, Optional

import cv2
import numpy as np

from ..ffmpeg_env import detect_video_encoder, ffmpeg
from .color_grade import ColorGrade, build_filter as build_color_filter
from .process_manager import CancelledError, ProcessManager
from .subject_tracking import SubjectTracker
from .video_effects import apply_video_effects_bgr

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def render_clip_source_pass(
    input_video: str,
    output_video: str,
    clip_options: list[dict[str, object]],
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> str:
    replacements = [option for option in clip_options if _clip_option_has_source_replacement(option)]
    if not replacements:
        return input_video
    base_replacements = [option for option in replacements if not _clip_option_is_overlay(option)]
    overlays = [option for option in replacements if _clip_option_is_overlay(option)]

    cancel = cancel or threading.Event()
    cap = cv2.VideoCapture(input_video)
    if not cap.isOpened():
        raise RuntimeError("Não foi possível abrir a timeline para aplicar mídias por clipe.")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = max(1.0, cap.get(cv2.CAP_PROP_FPS))
    total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    encoder, enc_args = detect_video_encoder()
    temp_video = f"{output_video}.video.mp4"
    source_caps: dict[str, cv2.VideoCapture] = {}
    source_meta: dict[str, tuple[float, int]] = {}

    if on_progress:
        on_progress(f"Aplicando mídias associadas por clipe | Encode: {encoder}.", 0.0)

    cmd = [
        ffmpeg(),
        "-loglevel", "error",
        "-nostats",
        "-y",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s:v", f"{width}x{height}",
        "-r", f"{fps:.6f}",
        "-i", "-",
        "-an",
        "-c:v", encoder, *enc_args,
        "-pix_fmt", "yuv420p",
        temp_video,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        try:
            while proc.stderr:
                chunk = proc.stderr.read(8192)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
        except Exception:
            pass

    threading.Thread(target=_drain_stderr, daemon=True).start()

    try:
        frame_index = 0
        while True:
            if cancel.is_set():
                raise CancelledError("cancelled")
            ok, frame_bgr = cap.read()
            if not ok or frame_bgr is None:
                break
            time_s = frame_index / fps
            option = _clip_option_for_output_time(base_replacements, time_s)
            if option is not None:
                replacement = _read_replacement_frame(option, time_s, source_caps, source_meta, width, height)
                if replacement is not None:
                    frame_bgr = replacement
                frame_bgr = _apply_clip_frame_options_bgr(frame_bgr, option)
            for overlay_option in _clip_overlay_options_for_output_time(overlays, time_s):
                overlay_frame = _read_replacement_frame(overlay_option, time_s, source_caps, source_meta, width, height)
                if overlay_frame is not None:
                    frame_bgr = _compose_overlay_frame_bgr(frame_bgr, overlay_frame, overlay_option)
            if proc.stdin is None:
                raise RuntimeError("Pipe do encoder indisponível.")
            proc.stdin.write(frame_bgr.tobytes())
            frame_index += 1
            if on_progress and frame_index % 12 == 0:
                on_progress(f"Mídias por clipe | Frame {frame_index}/{total_frames}", frame_index / total_frames)
    except Exception:
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        raise
    finally:
        cap.release()
        for source_cap in source_caps.values():
            with contextlib.suppress(Exception):
                source_cap.release()

    with contextlib.suppress(Exception):
        if proc.stdin:
            proc.stdin.close()
    ret = proc.wait(timeout=max(30, total_frames // max(1, int(fps)) * 4))
    if ret != 0:
        tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"Falha ao aplicar mídias por clipe.\n{tail}")

    with ProcessManager(cancel) as pm:
        pm.run_checked(
            [
                ffmpeg(), "-y",
                "-i", temp_video,
                "-i", input_video,
                "-map", "0:v:0",
                "-map", "1:a:0?",
                "-c:v", "copy",
                "-c:a", "copy",
                "-shortest",
                output_video,
            ],
            context="mux midias por clipe",
            timeout_s=max(60.0, total_frames / fps * 4.0),
        )
    with contextlib.suppress(OSError):
        import os
        os.remove(temp_video)
    if on_progress:
        on_progress("Mídias associadas aplicadas na timeline.", 1.0)
    return output_video


def _clip_option_for_output_time(clip_options: list[dict[str, object]], time_s: float) -> Optional[dict[str, object]]:
    for option in clip_options:
        start_s = float(option.get("output_start_s", option.get("start_s", 0.0)) or 0.0)
        end_s = float(option.get("output_end_s", option.get("end_s", 0.0)) or 0.0)
        if start_s <= time_s < end_s:
            return option
    return None


def _clip_overlay_options_for_output_time(clip_options: list[dict[str, object]], time_s: float) -> list[dict[str, object]]:
    active: list[dict[str, object]] = []
    for option in clip_options:
        start_s = float(option.get("output_start_s", option.get("start_s", 0.0)) or 0.0)
        end_s = float(option.get("output_end_s", option.get("end_s", 0.0)) or 0.0)
        if start_s <= time_s < end_s:
            active.append(option)
    return active


def _clip_option_is_overlay(option: dict[str, object]) -> bool:
    return str(option.get("layer") or "").strip().lower() == "overlay"


def _clip_option_has_source_replacement(option: dict[str, object]) -> bool:
    if not option.get("source_path"):
        return False
    start_s = float(option.get("output_start_s", option.get("start_s", 0.0)) or 0.0)
    end_s = float(option.get("output_end_s", option.get("end_s", 0.0)) or 0.0)
    return end_s > start_s


def _read_replacement_frame(
    option: dict[str, object],
    time_s: float,
    source_caps: dict[str, cv2.VideoCapture],
    source_meta: dict[str, tuple[float, int]],
    width: int,
    height: int,
) -> Optional[object]:
    path = str(option.get("source_path") or "")
    if not path:
        return None
    if _is_image_path(path):
        frame_bgr = cv2.imread(path, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            return None
        return _fit_image_frame_bgr(frame_bgr, width, height)
    cap = source_caps.get(path)
    if cap is None:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        source_caps[path] = cap
        source_meta[path] = (
            max(1.0, cap.get(cv2.CAP_PROP_FPS)),
            max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT))),
        )
    fps, total_frames = source_meta[path]
    start_s = float(option.get("output_start_s", option.get("start_s", 0.0)) or 0.0)
    frame_index = max(0, min(total_frames - 1, int(round((time_s - start_s) * fps))))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame_bgr = cap.read()
    if not ok or frame_bgr is None:
        return None
    return cv2.resize(frame_bgr, (width, height), interpolation=cv2.INTER_AREA)


def _is_image_path(path: str) -> bool:
    from pathlib import Path

    return Path(str(path or "")).suffix.lower() in IMAGE_EXTENSIONS


def _fit_image_frame_bgr(frame_bgr: object, width: int, height: int) -> object:
    src_h, src_w = frame_bgr.shape[:2]
    if src_w <= 0 or src_h <= 0 or width <= 0 or height <= 0:
        return frame_bgr
    scale = min(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width, 3), dtype=resized.dtype)
    x = (width - new_w) // 2
    y = (height - new_h) // 2
    canvas[y:y + new_h, x:x + new_w] = resized
    return canvas


def _compose_overlay_frame_bgr(base_bgr: object, overlay_bgr: object, option: dict[str, object]) -> object:
    height, width = base_bgr.shape[:2]
    overlay = _fit_image_frame_bgr(overlay_bgr, width, height)
    scale_pct = max(25.0, min(300.0, float(option.get("scale_pct", 100.0) or 100.0)))
    pos_x_pct = float(option.get("position_x_pct", 0.0) or 0.0)
    pos_y_pct = float(option.get("position_y_pct", 0.0) or 0.0)

    if scale_pct >= 100.0:
        rendered = _apply_overlay_chroma_bgr(overlay, option, base_bgr)
        rendered = _scale_frame_bgr_positioned(rendered, scale_pct, pos_x_pct, pos_y_pct)
        rendered = _draw_overlay_text_if_needed_bgr(rendered, option)
        return _blend_overlay_bgr(base_bgr, rendered, _option_opacity_factor(option))

    left, top, right, bottom = _scaled_overlay_bounds(width, height, scale_pct, pos_x_pct, pos_y_pct)
    if right <= left or bottom <= top:
        return base_bgr
    out = base_bgr.copy()
    scaled = cv2.resize(overlay, (right - left, bottom - top), interpolation=cv2.INTER_AREA)
    background = out[top:bottom, left:right]
    scaled = _apply_overlay_chroma_bgr(scaled, option, background)
    scaled = _draw_overlay_text_if_needed_bgr(scaled, option)
    scaled = _blend_overlay_bgr(background, scaled, _option_opacity_factor(option))
    out[top:bottom, left:right] = scaled
    return out


def _option_opacity_factor(option: dict[str, object]) -> float:
    try:
        opacity_pct = float(option.get("opacity_pct", 100.0) or 100.0)
    except (TypeError, ValueError):
        opacity_pct = 100.0
    return max(0.0, min(100.0, opacity_pct)) / 100.0


def _blend_overlay_bgr(base_bgr: object, overlay_bgr: object, opacity: float) -> object:
    opacity = max(0.0, min(1.0, float(opacity)))
    if opacity <= 0.0:
        return base_bgr
    if opacity >= 1.0:
        return overlay_bgr
    if base_bgr.shape[:2] != overlay_bgr.shape[:2]:
        overlay_bgr = cv2.resize(overlay_bgr, (base_bgr.shape[1], base_bgr.shape[0]), interpolation=cv2.INTER_AREA)
    return cv2.addWeighted(overlay_bgr, opacity, base_bgr, 1.0 - opacity, 0.0)


def _scaled_overlay_bounds(
    width: int,
    height: int,
    scale_pct: float,
    pos_x_pct: float,
    pos_y_pct: float,
) -> tuple[int, int, int, int]:
    scale = max(0.25, min(0.9999, scale_pct / 100.0))
    visual_w = max(1, int(round(width * scale)))
    visual_h = max(1, int(round(height * scale)))
    free_x = max(0, width - visual_w)
    free_y = max(0, height - visual_h)
    pos_x = max(-100.0, min(100.0, pos_x_pct)) / 100.0
    pos_y = max(-100.0, min(100.0, pos_y_pct)) / 100.0
    left = int(round(free_x / 2 + pos_x * free_x / 2))
    top = int(round(free_y / 2 + pos_y * free_y / 2))
    left = max(0, min(width, left))
    top = max(0, min(height, top))
    return left, top, min(width, left + visual_w), min(height, top + visual_h)


def _apply_overlay_chroma_bgr(frame_bgr: object, option: dict[str, object], background_bgr: object) -> object:
    if not bool(option.get("chroma_enabled", False)):
        return frame_bgr
    target = np.array(_hex_to_bgr(str(option.get("chroma_color") or "#00ff00")), dtype=np.int16)
    arr = np.array(frame_bgr, dtype=np.int16)
    diff = np.linalg.norm(arr - target, axis=2)
    mask = diff <= max(1.0, float(option.get("chroma_tolerance", 45.0) or 45.0))
    if not mask.any():
        return frame_bgr
    out = arr.astype(np.uint8)
    bg = np.array(background_bgr, dtype=np.uint8)
    if bg.shape[:2] != out.shape[:2]:
        bg = cv2.resize(bg, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_AREA)
    out[mask] = bg[mask]
    return out


def _draw_overlay_text_if_needed_bgr(frame_bgr: object, option: dict[str, object]) -> object:
    text = str(option.get("text_overlay") or "").strip()
    if not text:
        return frame_bgr
    return _draw_text_overlay_bgr(
        frame_bgr,
        text,
        float(option.get("text_position_x_pct", 0.0) or 0.0),
        float(option.get("text_position_y_pct", 72.0) or 72.0),
        float(option.get("text_size_pct", 100.0) or 100.0),
        str(option.get("text_color") or "#ffffff"),
        bool(option.get("text_background_enabled", True)),
        str(option.get("text_background_color") or "#000000"),
    )


def _apply_clip_frame_options_bgr(frame_bgr: object, option: dict[str, object]) -> object:
    rendered = frame_bgr
    if bool(option.get("chroma_enabled", False)):
        rendered = _apply_chroma_key_bgr(
            rendered,
            str(option.get("chroma_color") or "#00ff00"),
            float(option.get("chroma_tolerance", 45.0) or 45.0),
        )
    rendered = _scale_frame_bgr_positioned(
        rendered,
        float(option.get("scale_pct", 100.0) or 100.0),
        float(option.get("position_x_pct", 0.0) or 0.0),
        float(option.get("position_y_pct", 0.0) or 0.0),
    )
    text = str(option.get("text_overlay") or "").strip()
    if text:
        rendered = _draw_text_overlay_bgr(
            rendered,
            text,
            float(option.get("text_position_x_pct", 0.0) or 0.0),
            float(option.get("text_position_y_pct", 72.0) or 72.0),
            float(option.get("text_size_pct", 100.0) or 100.0),
            str(option.get("text_color") or "#ffffff"),
            bool(option.get("text_background_enabled", True)),
            str(option.get("text_background_color") or "#000000"),
        )
    return rendered


def _scale_frame_bgr_centered(frame_bgr: object, scale_pct: float) -> object:
    return _scale_frame_bgr_positioned(frame_bgr, scale_pct, 0.0, 0.0)


def _scale_frame_bgr_positioned(
    frame_bgr: object,
    scale_pct: float,
    pos_x_pct: float = 0.0,
    pos_y_pct: float = 0.0,
) -> object:
    frame = frame_bgr
    height, width = frame.shape[:2]
    scale = max(0.25, min(3.0, float(scale_pct) / 100.0))
    pos_x = max(-100.0, min(100.0, float(pos_x_pct))) / 100.0
    pos_y = max(-100.0, min(100.0, float(pos_y_pct))) / 100.0
    if abs(scale - 1.0) < 0.0001 and abs(pos_x) < 0.0001 and abs(pos_y) < 0.0001:
        return frame
    if scale > 1.0:
        crop_w = max(1, int(width / scale))
        crop_h = max(1, int(height / scale))
        max_x = max(0, width - crop_w)
        max_y = max(0, height - crop_h)
        x1 = int(round(max_x / 2 + pos_x * max_x / 2))
        y1 = int(round(max_y / 2 + pos_y * max_y / 2))
        x1 = max(0, min(max_x, x1))
        y1 = max(0, min(max_y, y1))
        cropped = frame[y1:y1 + crop_h, x1:x1 + crop_w]
        return cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LINEAR)
    resized_w = max(1, int(width * scale))
    resized_h = max(1, int(height * scale))
    resized = cv2.resize(frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros_like(frame)
    free_x = max(0, width - resized_w)
    free_y = max(0, height - resized_h)
    x1 = int(round(free_x / 2 + pos_x * free_x / 2))
    y1 = int(round(free_y / 2 + pos_y * free_y / 2))
    x1 = max(0, min(free_x, x1))
    y1 = max(0, min(free_y, y1))
    canvas[y1:y1 + resized_h, x1:x1 + resized_w] = resized
    return canvas


def _apply_chroma_key_bgr(frame_bgr: object, color: str, tolerance: float) -> object:
    target_rgb = _hex_to_rgb(_normalize_hex_color(color))
    target_bgr = np.array([target_rgb[2], target_rgb[1], target_rgb[0]], dtype=np.int16)
    frame_i16 = frame_bgr.astype(np.int16)
    diff = np.linalg.norm(frame_i16 - target_bgr, axis=2)
    mask = diff <= max(1.0, float(tolerance))
    if not mask.any():
        return frame_bgr
    output = frame_bgr.copy()
    checker = ((np.indices(mask.shape).sum(axis=0) // 18) % 2) * 42 + 24
    output[mask] = np.stack([checker, checker, checker], axis=2)[mask].astype(np.uint8)
    return output


def _draw_text_overlay_bgr(
    frame_bgr: object,
    text: str,
    pos_x_pct: float = 0.0,
    pos_y_pct: float = 72.0,
    size_pct: float = 100.0,
    text_color: str = "#ffffff",
    background_enabled: bool = True,
    background_color: str = "#000000",
) -> object:
    frame = frame_bgr.copy()
    height, width = frame.shape[:2]
    x, y = _text_anchor(width, height, pos_x_pct, pos_y_pct)
    font_scale = max(0.35, min(2.2, width / 1280.0 * (float(size_pct) / 100.0)))
    box_h = max(24, int(34 * font_scale))
    lines = _text_overlay_lines(text)
    box_w = min(width - 1, max(90, int(max(len(line) for line in lines) * box_h * 0.55)))
    total_h = box_h * len(lines)
    pad = max(4, int(8 * font_scale))
    if background_enabled:
        bg_rgb = _hex_to_rgb(_normalize_hex_color(background_color, "#000000"))
        bg_bgr = (bg_rgb[2], bg_rgb[1], bg_rgb[0])
        cv2.rectangle(
            frame,
            (max(0, x - pad), max(0, y - pad)),
            (min(width - 1, x + box_w + pad), min(height - 1, y + total_h + pad)),
            bg_bgr,
            thickness=-1,
        )
    fg_rgb = _hex_to_rgb(_normalize_hex_color(text_color, "#ffffff"))
    fg_bgr = (fg_rgb[2], fg_rgb[1], fg_rgb[0])
    for idx, line in enumerate(lines):
        cv2.putText(
            frame,
            line,
            (x, y + idx * box_h + max(16, box_h - pad)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            fg_bgr,
            2,
            cv2.LINE_AA,
        )
    return frame


def _text_overlay_lines(text: str, max_lines: int = 4, max_chars: int = 80) -> list[str]:
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n") if line.strip()]
    if not lines:
        return [""]
    return [line[:max_chars] for line in lines[:max_lines]]


def _text_anchor(width: int, height: int, pos_x_pct: float, pos_y_pct: float) -> tuple[int, int]:
    x_ratio = (max(-100.0, min(100.0, float(pos_x_pct))) + 100.0) / 200.0
    y_ratio = max(0.0, min(100.0, float(pos_y_pct))) / 100.0
    return (int(round(max(0, width - 1) * x_ratio)), int(round(max(0, height - 1) * y_ratio)))


def _normalize_hex_color(value: str, default: str = "#00ff00") -> str:
    text = str(value or "").strip()
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) == 7:
        try:
            int(text[1:], 16)
            return text.lower()
        except ValueError:
            pass
    return default


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    color = _normalize_hex_color(value)
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex_to_bgr(value: str) -> tuple[int, int, int]:
    red, green, blue = _hex_to_rgb(_normalize_hex_color(value))
    return blue, green, red


def render_effects_pass(
    input_video: str,
    output_video: str,
    color_grade: Optional[ColorGrade],
    bokeh_intensity: float,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> str:
    if not color_grade and bokeh_intensity < 0.05:
        return input_video
    if color_grade and not color_grade.enabled and bokeh_intensity < 0.05:
        return input_video

    cancel = cancel or threading.Event()
    if bokeh_intensity < 0.05 and color_grade and color_grade.enabled:
        return _render_color_grade_ffmpeg(
            input_video,
            output_video,
            color_grade,
            cancel=cancel,
            on_progress=on_progress,
        )

    cap = cv2.VideoCapture(input_video)
    if not cap.isOpened():
        raise RuntimeError("Não foi possível abrir o vídeo para renderizar os efeitos.")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = max(1.0, cap.get(cv2.CAP_PROP_FPS))
    total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    encoder, enc_args = detect_video_encoder()
    if on_progress:
        on_progress(
            f"Bokeh fast em CPU; encode de saída com {encoder}.",
            0.0,
        )
    cmd = [
        ffmpeg(),
        "-loglevel", "error",
        "-nostats",
        "-y",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s:v", f"{width}x{height}",
        "-r", f"{fps:.6f}",
        "-i", "-",
        "-an",
        "-c:v", encoder, *enc_args,
        "-pix_fmt", "yuv420p",
        output_video,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        try:
            while proc.stderr:
                chunk = proc.stderr.read(8192)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
        except Exception:
            pass

    threading.Thread(target=_drain_stderr, daemon=True).start()

    tracker = SubjectTracker()
    rendered_frames = 0
    try:
        while True:
            if cancel.is_set():
                raise CancelledError("cancelled")

            ok, frame_bgr = cap.read()
            if not ok or frame_bgr is None:
                break

            rendered, _subject = apply_video_effects_bgr(
                frame_bgr,
                grade=color_grade,
                bokeh_intensity=bokeh_intensity,
                tracker=tracker,
            )
            if proc.stdin is None:
                raise RuntimeError("Pipe do encoder indisponível.")
            proc.stdin.write(rendered.tobytes())
            rendered_frames += 1

            if on_progress and rendered_frames % 3 == 0:
                on_progress(
                    f"Aplicando bokeh fast | Frame {rendered_frames}/{total_frames} | CPU + {encoder}",
                    rendered_frames / total_frames,
                )
    except Exception:
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        raise
    finally:
        cap.release()

    with contextlib.suppress(Exception):
        if proc.stdin:
            proc.stdin.close()
    ret = proc.wait(timeout=max(30, total_frames // max(1, int(fps)) * 4))
    if ret != 0:
        tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"Falha ao renderizar os efeitos.\n{tail}")

    if on_progress:
        on_progress(f"Efeitos renderizados. Bokeh: CPU | Encode: {encoder}.", 1.0)
    return output_video


def _render_color_grade_ffmpeg(
    input_video: str,
    output_video: str,
    color_grade: ColorGrade,
    cancel: threading.Event,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> str:
    vf = build_color_filter(color_grade)
    if not vf:
        return input_video

    encoder, enc_args = detect_video_encoder()
    if on_progress:
        on_progress(
            f"Bokeh desativado; color grade via ffmpeg. Encode: {encoder}.",
            0.0,
        )

    with ProcessManager(cancel) as pm:
        pm.run_checked(
            [
                ffmpeg(), "-y",
                "-i", input_video,
                "-vf", vf,
                "-an",
                "-c:v", encoder, *enc_args,
                "-pix_fmt", "yuv420p",
                output_video,
            ],
            context="color grade",
            timeout_s=900,
        )

    if on_progress:
        on_progress(f"Color grade renderizado via ffmpeg. Encode: {encoder}.", 1.0)
    return output_video
