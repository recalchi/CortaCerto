"""
Video editing via ffmpeg.

Key design decisions vs previous version:
- zoompan REMOVED: it's an image filter, not a video filter; was causing
  multi-minute hangs on every "animated zoom" segment.
- Zoom now = simple scale+crop (fast, GPU-compatible, sub-second).
- Cancel = threading.Event propagated to ProcessManager which kills every
  live Popen immediately (replaces old per-call _run_cancelable).
- Timeout = each segment has a hard deadline (segment_dur * 60s, min 45s).
- Hardware decode = -hwaccel auto for all inputs (DXVA2/D3D11/CUDA).
- GPU encode = detected once at startup via detect_video_encoder().
- Audio = two-step post-process on final joined file:
    Step A (video):  color grade + face-aware bokeh via filter_complex (NVENC)
    Step B (audio):  afftdn noise-gate + EBU R128 loudnorm (audio-only, fast)
"""
from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .analyzer import AudioAnalysis
from .color_grade import ColorGrade, build_filter as build_color_filter
from .process_manager import ProcessManager, CancelledError          # canonical source
from ..ffmpeg_env import ffmpeg, ffprobe, detect_video_encoder


# ── Re-export CancelledError so existing imports from .editor still work ────
__all__ = ["CancelledError", "cut_silence", "convert_to_vertical",
           "get_video_duration", "get_video_fps", "RenderStats",
           "build_bokeh_filter_complex"]


# ── Data ────────────────────────────────────────────────────────────────────

@dataclass
class SegmentEffect:
    zoom_factor: float = 1.0   # 1.06 = 6% static zoom-in
    fade_in_s:   float = 0.0
    fade_out_s:  float = 0.0


@dataclass
class RenderStats:
    segments_total:        int   = 0
    segments_zoomed:       int   = 0
    segments_transitioned: int   = 0
    encoder_used:          str   = ""
    render_time_s:         float = 0.0


# ── Public ───────────────────────────────────────────────────────────────────

def cut_silence(
    video_path: str,
    analysis: AudioAnalysis,
    output_path: str,
    crf: int = 18,
    preset: str = "fast",
    color_grade: Optional[ColorGrade] = None,
    music_path: Optional[str] = None,
    noise_reduction: bool = True,
    bokeh_intensity: float = 0.0,
    face_x: float = 0.50,
    face_y: float = 0.38,
    face_size: float = 0.22,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> RenderStats:
    segments = analysis.speech_segments
    if not segments:
        raise ValueError(
            "Nenhum trecho de fala detectado.\n"
            "Tente reduzir o limiar de silêncio (ex: -50 dB)."
        )

    encoder, enc_args = detect_video_encoder()
    n         = len(segments)
    total_dur = sum(e - s for s, e in segments)
    effects   = _plan_effects(segments)
    stats     = RenderStats(
        segments_total        = n,
        segments_zoomed       = sum(1 for e in effects if e.zoom_factor > 1.0),
        segments_transitioned = sum(1 for e in effects if e.fade_out_s > 0),
        encoder_used          = encoder,
    )
    t0 = time.monotonic()

    def prog(msg: str, pct: float) -> None:
        if on_progress:
            on_progress(msg, pct)

    # Single ProcessManager owns ALL ffmpeg children in this pipeline run.
    # cancel_event is wired through, so pm.check_cancel() + pm.kill_all()
    # are both reachable from the UI cancel button.
    with ProcessManager(cancel) as pm:
        with tempfile.TemporaryDirectory() as tmp:
            seg_paths: list[str] = []
            processed_s = 0.0

            for i, ((start, end), fx) in enumerate(zip(segments, effects)):
                pm.check_cancel()

                seg_dur = end - start
                pct     = 0.10 + (i / n) * 0.68

                eta_str = ""
                if i > 0 and processed_s > 0:
                    elapsed = time.monotonic() - t0
                    rate    = processed_s / elapsed
                    eta_s   = (total_dur - processed_s) / rate if rate > 0 else 0
                    eta_str = f"  (~{int(eta_s)}s restantes)"

                prog(f"Segmento {i + 1}/{n}  [{encoder}]{eta_str}", pct)

                seg_path = os.path.join(tmp, f"seg_{i:05d}.ts")
                timeout  = max(45.0, seg_dur * 60.0)

                _render_segment(
                    video_path, start, end, seg_path,
                    fx, "", encoder, enc_args,
                    pm=pm, timeout_s=timeout,
                )
                seg_paths.append(seg_path)
                processed_s += seg_dur

            pm.check_cancel()
            prog("Unindo segmentos…", 0.80)

            concat_input = "concat:" + "|".join(seg_paths)
            needs_postprocess = music_path or noise_reduction
            joined_path = (
                output_path if not needs_postprocess
                else os.path.join(tmp, "joined.mp4")
            )

            pm.run_checked(
                [ffmpeg(), "-y", "-i", concat_input, "-c", "copy", joined_path],
                context="unir segmentos", timeout_s=120,
            )

            # ── Step A: video re-encode (bokeh + color grade via NVENC) ──────
            grade_vf = build_color_filter(color_grade) if color_grade else ""
            fc_str, fc_out = build_bokeh_filter_complex(
                bokeh_intensity, face_x, face_y, face_size, grade_vf
            )
            needs_vf = bool(fc_str) or bool(grade_vf)

            if needs_vf:
                pm.check_cancel()
                prog("Aplicando color grade e bokeh…", 0.82)
                pre_vf      = joined_path
                joined_path = os.path.join(tmp, "grade.mp4")
                timeout_vf  = max(120.0, total_dur * 40.0)

                if fc_str:
                    cmd_vf = [
                        ffmpeg(), "-y", "-i", pre_vf,
                        "-filter_complex", fc_str,
                        "-map", f"[{fc_out}]", "-map", "0:a",
                        "-c:v", encoder, *enc_args,
                        "-c:a", "copy",
                        joined_path,
                    ]
                else:
                    cmd_vf = [
                        ffmpeg(), "-y", "-i", pre_vf,
                        "-vf", grade_vf,
                        "-c:v", encoder, *enc_args,
                        "-c:a", "copy",
                        joined_path,
                    ]
                pm.run_checked(cmd_vf, context="color grade", timeout_s=timeout_vf)
                prog("Color grade aplicado.", 0.86)

            # ── Step B: audio normalization (fast — video stream copied) ─────
            af_parts: list[str] = []
            if noise_reduction:
                af_parts.append("afftdn=nf=-25")
            af_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")

            pm.check_cancel()
            prog("Normalizando áudio…", 0.88)
            pre_audio   = joined_path
            joined_path = os.path.join(tmp, "audio.mp4")
            pm.run_checked(
                [ffmpeg(), "-y", "-i", pre_audio,
                 "-c:v", "copy",
                 "-af", ",".join(af_parts),
                 "-c:a", "aac", "-b:a", "192k",
                 joined_path],
                context="áudio", timeout_s=max(60.0, total_dur * 5.0),
            )

            # ── Music mix ────────────────────────────────────────────────────
            if music_path and os.path.exists(music_path):
                pm.check_cancel()
                prog("Mixando música de fundo…", 0.92)
                pre_music   = joined_path
                joined_path = os.path.join(tmp, "with_music.mp4")
                _mix_music(pre_music, music_path, joined_path, total_dur, pm=pm)

            # Copy to final output
            if joined_path != output_path:
                import shutil
                shutil.move(joined_path, output_path)

    stats.render_time_s = time.monotonic() - t0
    return stats


def convert_to_vertical(
    video_path: str,
    output_path: str,
    target_width: int = 1080,
    target_height: int = 1920,
    crf: int = 18,
    preset: str = "fast",
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> None:
    if on_progress:
        on_progress("Convertendo para formato vertical…", 0.0)

    encoder, enc_args = detect_video_encoder()
    vf = f"scale=-1:{target_height}:flags=lanczos,crop={target_width}:{target_height}"

    with ProcessManager(cancel) as pm:
        pm.run_checked(
            [
                ffmpeg(), "-y",
                "-hwaccel", "auto",
                "-i", video_path,
                "-vf", vf,
                "-c:v", encoder, *enc_args,
                "-c:a", "aac", "-b:a", "192k",
                output_path,
            ],
            context="conversão vertical", timeout_s=600,
        )

    if on_progress:
        on_progress("Versão vertical gerada.", 1.0)


def get_video_duration(video_path: str) -> float:
    result = subprocess.run(
        [ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def get_video_fps(video_path: str) -> float:
    result = subprocess.run(
        [ffprobe(), "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        num, den = result.stdout.strip().split("/")
        return float(num) / float(den)
    except Exception:
        return 30.0


# ── Effect planning ──────────────────────────────────────────────────────────

def _plan_effects(segments: list[tuple[float, float]]) -> list[SegmentEffect]:
    n = len(segments)
    fx = [SegmentEffect() for _ in range(n)]
    if n == 0:
        return fx

    # Opening and closing fades on first and last segment only
    fx[0].fade_in_s   = 0.5
    fx[-1].fade_out_s = 0.6

    # Static zoom on every 4th segment (starting at index 2), min 1.5 s
    for i in range(2, n, 4):
        if segments[i][1] - segments[i][0] >= 1.5:
            fx[i].zoom_factor = 1.06

    # ONE mid-film transition at ~55% (not near the end to avoid doubling)
    if n >= 6:
        mid_idx = int(n * 0.55)
        mid_idx = min(mid_idx, n - 4)
        if 0 < mid_idx < n - 1:
            dur = segments[mid_idx][1] - segments[mid_idx][0]
            if dur > 1.0:
                fx[mid_idx].fade_out_s    = max(fx[mid_idx].fade_out_s, 0.35)
                fx[mid_idx + 1].fade_in_s = max(fx[mid_idx + 1].fade_in_s, 0.30)

    return fx


# ── Bokeh (background soft-focus) ────────────────────────────────────────────

def build_bokeh_filter_complex(
    intensity: float,
    face_x: float = 0.50,
    face_y: float = 0.38,
    face_size: float = 0.22,
    grade_vf: str = "",
) -> tuple[str, str]:
    """
    Build an ffmpeg filter_complex string for face-aware background blur.
    Returns (filter_complex_str, output_label).
    Returns ("", "") if intensity < 0.05 (caller uses -vf grade instead).

    The ellipse is centred on the detected body (face + torso) so the person
    stays SHARP while the background is BLURRED.
    Uses min/max instead of clamp/lerp (not in ffmpeg blend evaluator).
    """
    if intensity < 0.05:
        return "", ""

    sigma = int(3 + intensity * 10)   # blur sigma 3–13

    # Body centre (slightly below face to include shoulders)
    bx = face_x
    by = min(0.85, face_y + face_size * 1.4)

    rx = max(0.28, face_size * 2.0)   # sharp-zone width  (fraction of W/2)
    ry = max(0.48, face_size * 4.0)   # sharp-zone height (fraction of H/2)

    dist = (f"hypot((X-W*{bx:.3f})/(W/2*{rx:.3f})"
            f"\\,(Y-H*{by:.3f})/(H/2*{ry:.3f}))")
    t    = f"min(1\\,max(0\\,(1.1-{dist})/0.5))"
    expr = f"A*(1-{t})+B*{t}"   # A = blurred, B = sharp

    parts = [
        "[0:v]split=2[_b_orig][_b_bg]",
        f"[_b_bg]gblur=sigma={sigma}[_b_blur]",
        f"[_b_blur][_b_orig]blend=all_expr='{expr}'[_b_out]",
    ]

    out_label = "_b_out"
    if grade_vf:
        parts.append(f"[_b_out]{grade_vf}[_b_final]")
        out_label = "_b_final"

    return ";".join(parts), out_label


# ── Segment renderer ─────────────────────────────────────────────────────────

def _render_segment(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    fx: SegmentEffect,
    color_vf: str,
    encoder: str,
    enc_args: list[str],
    pm: ProcessManager,
    timeout_s: float,
) -> None:
    duration = end - start
    vf_parts: list[str] = []

    if color_vf:
        vf_parts.append(color_vf)

    if fx.zoom_factor > 1.0:
        zf = fx.zoom_factor
        vf_parts.append(
            f"scale=ceil(iw*{zf:.3f}/2)*2:ceil(ih*{zf:.3f}/2)*2,"
            f"crop=iw/{zf:.3f}:ih/{zf:.3f}"
        )

    if fx.fade_in_s > 0:
        vf_parts.append(f"fade=t=in:st=0:d={fx.fade_in_s:.2f}")
    if fx.fade_out_s > 0:
        fade_st = max(0.0, duration - fx.fade_out_s)
        vf_parts.append(f"fade=t=out:st={fade_st:.2f}:d={fx.fade_out_s:.2f}")

    cmd = [
        ffmpeg(), "-y",
        "-hwaccel", "auto",
        "-ss", f"{start:.4f}", "-to", f"{end:.4f}",
        "-i", video_path,
        "-map", "0:v:0", "-map", "0:a:0",
    ]

    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += ["-c:v", encoder, *enc_args, "-c:a", "aac", "-b:a", "192k",
            "-f", "mpegts", output_path]

    pm.run_checked(cmd, context=f"segmento {Path(output_path).stem}",
                   timeout_s=timeout_s)


# ── Music mixer ──────────────────────────────────────────────────────────────

def _mix_music(
    video_path: str,
    music_path: str,
    output_path: str,
    video_duration: float,
    pm: ProcessManager,
) -> None:
    fade_out_start = max(0.0, video_duration - 2.5)
    af = (
        f"[1:a]volume=0.13,"
        f"afade=t=in:st=0:d=0.8,"
        f"afade=t=out:st={fade_out_start:.2f}:d=2.5,"
        f"aloop=loop=-1:size=2e+09[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[outa]"
    )
    pm.run_checked(
        [
            ffmpeg(), "-y",
            "-i", video_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", af,
            "-map", "0:v", "-map", "[outa]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_path,
        ],
        context="música", timeout_s=300,
    )
