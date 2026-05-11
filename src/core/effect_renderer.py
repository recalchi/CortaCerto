from __future__ import annotations

import subprocess
import threading
import contextlib
from typing import Callable, Optional

import cv2

from ..ffmpeg_env import detect_video_encoder, ffmpeg
from .color_grade import ColorGrade, build_filter as build_color_filter
from .process_manager import CancelledError, ProcessManager
from .subject_tracking import SubjectTracker
from .video_effects import apply_video_effects_bgr


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
            option = _clip_option_for_output_time(replacements, time_s)
            if option is not None:
                replacement = _read_replacement_frame(option, time_s, source_caps, source_meta, width, height)
                if replacement is not None:
                    frame_bgr = replacement
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
