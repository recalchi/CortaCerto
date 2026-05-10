from __future__ import annotations

import subprocess
import threading
from typing import Callable, Optional

import cv2

from ..ffmpeg_env import detect_video_encoder, ffmpeg
from .color_grade import ColorGrade, build_filter as build_color_filter
from .process_manager import CancelledError, ProcessManager
from .subject_tracking import SubjectTracker
from .video_effects import apply_video_effects_bgr


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
                    f"Etapa 5/7 - Aplicando bokeh fast | Frame {rendered_frames}/{total_frames} | CPU + {encoder}",
                    rendered_frames / total_frames,
                )
    except Exception:
        if proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass
        proc.kill()
        proc.wait(timeout=5)
        cap.release()
        raise

    cap.release()
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
