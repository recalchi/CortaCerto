"""Video editing helpers powered by ffmpeg.

This module owns timeline cutting, vertical conversion, duration/FPS probing,
and optional audio/music post-processing. The heavier visual pass (color grade
and bokeh fast) is handled by effect_renderer.py so preview and export share the
same effect implementation.
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


# -- Re-export CancelledError so existing imports from .editor still work ----
__all__ = ["CancelledError", "cut_silence", "convert_to_vertical",
           "get_video_duration", "get_video_fps", "RenderStats",
           "build_bokeh_filter_complex"]


# Map "9:16" → (9, 16). Returns None when the AR shouldn't trigger a crop
# (i.e. user picked "original" or an unknown value).
_AR_PARSE = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1":  (1, 1),
    "4:5":  (4, 5),
    "5:4":  (5, 4),
    "4:3":  (4, 3),
    "3:4":  (3, 4),
}
# Legacy platform-name → aspect-ratio fallback for clients that haven't
# upgraded to the new `aspect_ratio` parameter yet.
_LEGACY_PLATFORM_AR = {
    "youtube": None,           # 16:9 = no crop needed for typical sources
    "reels":   (9, 16),
    "tiktok":  (9, 16),
    "shorts":  (9, 16),
}


def _resolve_target_aspect(aspect_ratio: Optional[str], platform: str) -> Optional[tuple[int, int]]:
    """Return (w, h) ratio for cropping, or None if no crop should run.

    Priority:
      1. `aspect_ratio` if it's a known value AND not "16:9" / "original"
         (we treat 16:9 as the default → no crop).
      2. `platform` legacy mapping.
    """
    if aspect_ratio:
        if aspect_ratio in ("16:9", "original"):
            return None   # no crop — assume source is already widescreen
        if aspect_ratio in _AR_PARSE:
            return _AR_PARSE[aspect_ratio]
    return _LEGACY_PLATFORM_AR.get(platform)


# -- Data --------------------------------------------------------------------

@dataclass
class SegmentEffect:
    zoom_factor: float = 1.0   # 1.06 = 6% static zoom-in
    fade_in_s:   float = 0.0
    fade_out_s:  float = 0.0
    # Etapa 6 — speed & transitions
    speed_factor:          float = 1.0    # 0.25=slow-mo 4x, 2.0=2× fast
    transition:            str   = "Corte"  # "Fade","Dissolver","Wipe Esq","Wipe Dir","Zoom"
    transition_duration_s: float = 0.4
    # Etapa C — audio per-clip
    volume_pct: float = 100.0   # 0..200, 100 = unity
    pan_pct:    float = 0.0     # -100 = full left, 0 = centre, +100 = full right
    # Inspector visual edits (from the web UI)
    brightness:      float = 0.0    # -100..100  (0 = no change)
    contrast:        float = 0.0    # -100..100  (0 = no change)
    saturation:      float = 0.0    # -100..100  (0 = no change)
    crop_top_pct:    float = 0.0    # 0..50 percent of height to crop from top
    crop_bottom_pct: float = 0.0
    crop_left_pct:   float = 0.0
    crop_right_pct:  float = 0.0
    scale_pct:       float = 100.0  # 10..300 (100 = original size)
    opacity_pct:     float = 100.0  # 0..100
    rotation_deg:    float = 0.0    # -180..180
    # Text overlay
    text_overlay:        str   = ""
    text_position_x_pct: float = 0.0
    text_position_y_pct: float = 72.0
    text_size_pct:       float = 100.0
    text_color:          str   = "#ffffff"
    text_bold:           bool  = False
    text_italic:         bool  = False
    text_align:          str   = "center"
    # Chroma key
    chroma_enabled:   bool  = False
    chroma_color:     str   = "#00ff00"
    chroma_tolerance: float = 45.0


@dataclass
class RenderStats:
    segments_total:        int   = 0
    segments_zoomed:       int   = 0
    segments_transitioned: int   = 0
    encoder_used:          str   = ""
    render_time_s:         float = 0.0


# -- Public -------------------------------------------------------------------

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
    per_clip_data: Optional[list[dict]] = None,   # Etapa 6: per-segment speed + transition
    text_clips: Optional[list[dict]] = None,       # [{text_overlay, start_s, end_s, ...style}]
    image_clips: Optional[list[dict]] = None,      # [{source_path, start_s, end_s, opacity_pct}]
    normalize_audio: bool = True,                  # Apply loudnorm post-processing
    platform: str = "youtube",                     # legacy: "youtube"|"reels"|"tiktok"|"shorts"
    aspect_ratio: Optional[str] = None,            # NEW: "16:9"|"9:16"|"1:1"|"4:5"|"4:3"|"3:4" (overrides platform)
    # Multi-source support: per-clip source file path and project-time offset.
    # source_paths[i] = path to the source video for segment i (None = use video_path)
    # source_offsets[i] = project_time - source_time offset (subtract from start/end)
    source_paths: Optional[list[Optional[str]]] = None,
    source_offsets: Optional[list[float]] = None,
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

    # Etapa 6: apply per-clip speed/transition overrides
    if per_clip_data:
        for i, clip_data in enumerate(per_clip_data):
            if i >= n:
                break
            fx = effects[i]
            sf = float(clip_data.get("speed_factor", 1.0) or 1.0)
            if sf > 0.01:
                fx.speed_factor = sf
            tr = str(clip_data.get("transition") or "Corte").strip()
            if tr:
                fx.transition = tr
            td = float(clip_data.get("transition_duration_s", 0.4) or 0.4)
            fx.transition_duration_s = max(0.1, td)
            # Etapa C — per-clip audio: volume, pan, user fades
            vol = float(clip_data.get("volume_pct", 100.0) or 100.0)
            fx.volume_pct = max(0.0, min(200.0, vol))
            pan = float(clip_data.get("pan_pct", 0.0) or 0.0)
            fx.pan_pct = max(-100.0, min(100.0, pan))
            user_fi = float(clip_data.get("fade_in_s", 0.0) or 0.0)
            user_fo = float(clip_data.get("fade_out_s", 0.0) or 0.0)
            if user_fi > 0.001:
                fx.fade_in_s = user_fi
            if user_fo > 0.001:
                fx.fade_out_s = user_fo
            # Inspector visual edits
            fx.brightness      = float(clip_data.get("brightness",      0.0)   or 0.0)
            fx.contrast        = float(clip_data.get("contrast",        0.0)   or 0.0)
            fx.saturation      = float(clip_data.get("saturation",      0.0)   or 0.0)
            fx.crop_top_pct    = float(clip_data.get("crop_top_pct",    0.0)   or 0.0)
            fx.crop_bottom_pct = float(clip_data.get("crop_bottom_pct", 0.0)   or 0.0)
            fx.crop_left_pct   = float(clip_data.get("crop_left_pct",   0.0)   or 0.0)
            fx.crop_right_pct  = float(clip_data.get("crop_right_pct",  0.0)   or 0.0)
            fx.scale_pct       = float(clip_data.get("scale_pct",       100.0) or 100.0)
            fx.opacity_pct     = float(clip_data.get("opacity_pct",     100.0) or 100.0)
            fx.rotation_deg    = float(clip_data.get("rotation_deg",    0.0)   or 0.0)
            # Text overlay
            fx.text_overlay        = str(clip_data.get("text_overlay",        "") or "")
            fx.text_position_x_pct = float(clip_data.get("text_position_x_pct", 0.0)   or 0.0)
            fx.text_position_y_pct = float(clip_data.get("text_position_y_pct", 72.0)  or 72.0)
            fx.text_size_pct       = float(clip_data.get("text_size_pct",       100.0) or 100.0)
            fx.text_color          = str(clip_data.get("text_color",          "#ffffff") or "#ffffff")
            fx.text_bold           = bool(clip_data.get("text_bold",           False))
            fx.text_italic         = bool(clip_data.get("text_italic",         False))
            fx.text_align          = str(clip_data.get("text_align",          "center") or "center")
            # Chroma key
            fx.chroma_enabled      = bool(clip_data.get("chroma_enabled",      False))
            fx.chroma_color        = str(clip_data.get("chroma_color",        "#00ff00") or "#00ff00")
            fx.chroma_tolerance    = float(clip_data.get("chroma_tolerance",   45.0) or 45.0)
        # Clear auto-generated segment fades when a user xfade transition is set
        for i, fx in enumerate(effects):
            if i > 0 and fx.transition.strip().lower() not in ("corte", "cut", ""):
                fx.fade_in_s = 0.0
            if i < n - 1 and effects[i + 1].transition.strip().lower() not in ("corte", "cut", ""):
                fx.fade_out_s = 0.0

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
            seg_paths:       list[str]   = []
            output_durations: list[float] = []
            processed_s = 0.0

            for i, ((start, end), fx) in enumerate(zip(segments, effects)):
                pm.check_cancel()

                # Multi-source: translate project-time → source-file time
                seg_src  = (source_paths[i] if source_paths and i < len(source_paths) and source_paths[i] else None) or video_path
                seg_off  = (source_offsets[i] if source_offsets and i < len(source_offsets) else 0.0)
                src_start = max(0.0, start - seg_off)
                src_end   = max(src_start + 0.01, end - seg_off)

                seg_dur = src_end - src_start
                pct     = 0.10 + (i / n) * 0.68

                eta_str = ""
                if i > 0 and processed_s > 0:
                    elapsed = time.monotonic() - t0
                    rate    = processed_s / elapsed
                    eta_s   = (total_dur - processed_s) / rate if rate > 0 else 0
                    eta_str = f"  (~{int(eta_s)}s restantes)"

                speed_tag = f"  {fx.speed_factor:.2g}×" if abs(fx.speed_factor - 1.0) > 0.01 else ""
                prog(f"Segmento {i + 1}/{n}  [{encoder}]{speed_tag}{eta_str}", pct)

                seg_path = os.path.join(tmp, f"seg_{i:05d}.ts")
                # Speed factor changes output duration
                sp       = max(0.1, fx.speed_factor)
                out_dur  = seg_dur / sp
                timeout  = max(45.0, seg_dur * 60.0)

                _render_segment(
                    seg_src, src_start, src_end, seg_path,
                    fx, "", encoder, enc_args,
                    pm=pm, timeout_s=timeout,
                )
                seg_paths.append(seg_path)
                output_durations.append(out_dur)
                processed_s += seg_dur

            pm.check_cancel()
            prog("Unindo segmentos...", 0.80)

            needs_postprocess = music_path or noise_reduction
            joined_path = (
                output_path if not needs_postprocess
                else os.path.join(tmp, "joined.mp4")
            )

            # Etapa 6: use xfade join if any non-"Corte" transition is set
            transitions    = [fx.transition for fx in effects]
            has_xfade      = any(
                t.strip().lower() not in ("corte", "cut", "")
                for t in transitions[1:]    # first segment has no "before" transition
            )
            default_td = effects[1].transition_duration_s if n > 1 else 0.4

            if has_xfade and n > 1:
                prog("Aplicando transições...", 0.81)
                _join_with_xfade(
                    seg_paths, output_durations, transitions, default_td,
                    joined_path, pm, encoder, enc_args,
                )
            else:
                concat_input = "concat:" + "|".join(seg_paths)
                pm.run_checked(
                    [ffmpeg(), "-y", "-i", concat_input, "-c", "copy", joined_path],
                    context="unir segmentos", timeout_s=120,
                )

            # -- Step A: video re-encode (bokeh + color grade via NVENC) ------
            grade_vf = build_color_filter(color_grade) if color_grade else ""
            fc_str, fc_out = build_bokeh_filter_complex(
                bokeh_intensity, face_x, face_y, face_size, grade_vf
            )
            needs_vf = bool(fc_str) or bool(grade_vf)

            if needs_vf:
                pm.check_cancel()
                prog("Aplicando color grade e bokeh...", 0.82)
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

            # -- Step B: audio normalization (fast - video stream copied) -----
            af_parts: list[str] = []
            if noise_reduction:
                af_parts.append("afftdn=nf=-25")
            if normalize_audio:
                af_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")

            pm.check_cancel()
            if af_parts:
                prog("Normalizando áudio...", 0.88)
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

            # -- Text overlays (text_track clips) --------------------------------
            active_texts = [
                tc for tc in (text_clips or [])
                if tc.get("text_overlay", "").strip()
            ]
            if active_texts:
                pm.check_cancel()
                prog("Aplicando textos/legendas…", 0.90)
                pre_text   = joined_path
                joined_path = os.path.join(tmp, "with_text.mp4")
                _apply_text_overlays(
                    pre_text, active_texts, joined_path,
                    encoder, enc_args, pm,
                )

            # -- Image overlays (overlay_track image clips) -------------------
            active_images = [
                ic for ic in (image_clips or [])
                if ic.get("source_path") and os.path.isfile(ic["source_path"])
                and float(ic.get("end_s", 0)) > float(ic.get("start_s", 0))
            ]
            if active_images:
                pm.check_cancel()
                prog("Aplicando imagens de overlay…", 0.91)
                pre_img   = joined_path
                joined_path = os.path.join(tmp, "with_images.mp4")
                _apply_image_overlays(
                    pre_img, active_images, joined_path,
                    encoder, enc_args, pm,
                )

            # -- Music mix ----------------------------------------------------
            if music_path and os.path.exists(music_path):
                pm.check_cancel()
                prog("Mixando música de fundo...", 0.92)
                pre_music   = joined_path
                joined_path = os.path.join(tmp, "with_music.mp4")
                _mix_music(pre_music, music_path, joined_path, total_dur, pm=pm)

            # -- Aspect-ratio crop ---------------------------------------------
            # New `aspect_ratio` parameter takes precedence over legacy `platform`.
            # When `aspect_ratio` is set, we crop the centred region matching its
            # ratio. When it's None, fall back to the legacy platform mapping
            # (reels/tiktok/shorts → 9:16).
            target_ar = _resolve_target_aspect(aspect_ratio, platform)
            if target_ar is not None:
                pm.check_cancel()
                ar_w, ar_h = target_ar
                ar_label = f"{ar_w}:{ar_h}"
                prog(f"Recortando para formato {ar_label}…", 0.95)
                pre_crop    = joined_path
                joined_path = os.path.join(tmp, f"crop_{ar_w}x{ar_h}.mp4")
                # Centre-crop. We compute the crop region by picking whichever
                # dimension is the limiting factor:
                #   if source_AR > target_AR  → height stays, width = h * target_AR
                #   if source_AR < target_AR  → width stays,  height = w / target_AR
                # ffmpeg expressions handle both cases via if().
                crop_vf = (
                    f"crop="
                    f"'if(gt(iw/ih,{ar_w}/{ar_h}),ih*{ar_w}/{ar_h},iw)':"
                    f"'if(gt(iw/ih,{ar_w}/{ar_h}),ih,iw*{ar_h}/{ar_w})':"
                    f"'(iw-if(gt(iw/ih,{ar_w}/{ar_h}),ih*{ar_w}/{ar_h},iw))/2':"
                    f"'(ih-if(gt(iw/ih,{ar_w}/{ar_h}),ih,iw*{ar_h}/{ar_w}))/2'"
                )
                pm.run_checked(
                    [ffmpeg(), "-y", "-i", pre_crop,
                     "-vf", crop_vf,
                     "-c:v", encoder, *enc_args,
                     "-c:a", "copy",
                     joined_path],
                    context=f"crop {ar_label}",
                    timeout_s=max(60.0, total_dur * 20.0),
                )

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
        on_progress("Convertendo para formato vertical...", 0.0)

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


# -- Effect planning ----------------------------------------------------------

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


# -- Bokeh (background soft-focus) --------------------------------------------

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

    sigma = int(3 + intensity * 10)   # blur sigma 3-13

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


# -- Text overlay helpers -----------------------------------------------------

# Common font paths tried in order (Windows → Linux → macOS)
_FONT_CANDIDATES_REGULAR = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
_FONT_CANDIDATES_BOLD = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _find_font(bold: bool = False) -> str:
    """Return path to best available font file, or empty string for built-in."""
    candidates = (_FONT_CANDIDATES_BOLD if bold else []) + _FONT_CANDIDATES_REGULAR
    for c in candidates:
        if os.path.exists(c):
            return c
    return ""


def _escape_drawtext(text: str) -> str:
    """Escape a string for use in ffmpeg drawtext filter value."""
    # Order matters: backslash first
    text = text.replace("\\", "\\\\\\\\")
    text = text.replace("'",  "\\'")
    text = text.replace(":",  "\\:")
    text = text.replace(",",  "\\,")
    text = text.replace("[",  "\\[")
    text = text.replace("]",  "\\]")
    return text


# -- Segment renderer ---------------------------------------------------------

def _build_atempo(speed: float) -> str:
    """Build an atempo audio filter chain for the given playback speed.

    atempo supports 0.5–100.0; for speed < 0.5 we chain two stages.
    """
    speed = max(0.1, min(10.0, float(speed)))
    if abs(speed - 1.0) < 0.001:
        return "anull"
    if speed < 0.5:
        # e.g. 0.25× → atempo=0.5,atempo=0.5
        return f"atempo={speed * 2:.4f},atempo=0.5000"
    return f"atempo={speed:.4f}"


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
    sp       = max(0.1, fx.speed_factor)
    # Output duration after speed change (used for fade timing)
    out_dur  = duration / sp
    vf_parts: list[str] = []

    if color_vf:
        vf_parts.append(color_vf)

    # ── Inspector visual edits ──────────────────────────────────────────────
    # 0. Chroma key — must be first so subsequent filters work on keyed output
    if fx.chroma_enabled and len(fx.chroma_color) == 7 and fx.chroma_color.startswith("#"):
        # similarity: 0.0 (exact match) .. 1.0 (match everything)
        sim = max(0.01, min(1.0, fx.chroma_tolerance / 100.0))
        # colorkey replaces matched color with black (YUV — no true alpha without overlay)
        vf_parts.append(f"colorkey=color={fx.chroma_color}:similarity={sim:.3f}:blend=0.05")

    # 1. Color: brightness / contrast / saturation (ffmpeg eq filter)
    br = fx.brightness / 100.0          # -1.0..1.0 (0 = neutral)
    ct = 1.0 + fx.contrast / 100.0      # 0..2      (1 = neutral)
    sa = 1.0 + fx.saturation / 100.0    # 0..2      (1 = neutral)
    if abs(fx.brightness) > 0.5 or abs(fx.contrast) > 0.5 or abs(fx.saturation) > 0.5:
        ct_clamped = max(0.0, min(1000.0, ct))
        sa_clamped = max(0.0, min(3.0,    sa))
        vf_parts.append(f"eq=brightness={br:.4f}:contrast={ct_clamped:.4f}:saturation={sa_clamped:.4f}")

    # 2. Crop (percentages 0-50 each edge)
    cl = fx.crop_left_pct   / 100.0
    cr = fx.crop_right_pct  / 100.0
    ct_p = fx.crop_top_pct  / 100.0
    cb = fx.crop_bottom_pct / 100.0
    if cl + cr + ct_p + cb > 0.001:
        vf_parts.append(
            f"crop=iw*(1-{cl:.4f}-{cr:.4f})"
            f":ih*(1-{ct_p:.4f}-{cb:.4f})"
            f":iw*{cl:.4f}:ih*{ct_p:.4f}"
        )

    # 3. Scale (100 = original; keep even dimensions for encoder)
    sc = max(0.1, fx.scale_pct / 100.0)
    if abs(sc - 1.0) > 0.005:
        vf_parts.append(f"scale=ceil(iw*{sc:.4f}/2)*2:ceil(ih*{sc:.4f}/2)*2")

    # 4. Rotation (degrees → radians; expand canvas to fit rotated frame)
    if abs(fx.rotation_deg) > 0.1:
        rad = fx.rotation_deg * 3.14159265358979 / 180.0
        vf_parts.append(f"rotate={rad:.6f}:ow=rotw({rad:.6f}):oh=roth({rad:.6f}):c=black")

    # 5. Opacity: blend with black (works on opaque YUV streams)
    op = max(0.0, min(1.0, fx.opacity_pct / 100.0))
    if op < 0.995:
        # Multiply each channel by opacity factor (darkens toward black)
        vf_parts.append(f"colorchannelmixer={op:.4f}:{op:.4f}:{op:.4f}:0:{op:.4f}:{op:.4f}:{op:.4f}:0:{op:.4f}:{op:.4f}:{op:.4f}")

    # 6. Text overlay via drawtext
    if fx.text_overlay.strip():
        escaped = _escape_drawtext(fx.text_overlay)
        # Convert #rrggbb → 0xrrggbbff (ffmpeg color with full alpha)
        hex_col = fx.text_color.lstrip("#")
        col_ffmpeg = f"0x{hex_col}ff" if len(hex_col) == 6 else "white"
        font_size = max(12, int(24 * fx.text_size_pct / 100.0))
        # x: centre + percentage offset; y: percentage of height
        x_off = fx.text_position_x_pct / 100.0
        y_pct = fx.text_position_y_pct / 100.0
        x_expr = f"(w-text_w)/2+w*{x_off:.4f}"
        y_expr = f"h*{y_pct:.4f}-text_h/2"
        font_path = _find_font(fx.text_bold)
        font_part = f":fontfile='{font_path}'" if font_path else ""
        vf_parts.append(
            f"drawtext=text='{escaped}'{font_part}"
            f":fontcolor={col_ffmpeg}:fontsize={font_size}"
            f":x={x_expr}:y={y_expr}"
            f":shadowcolor=black@0.75:shadowx=2:shadowy=2"
        )
    # ────────────────────────────────────────────────────────────────────────

    # Speed: setpts changes video tempo (must come before fades)
    if abs(sp - 1.0) > 0.001:
        vf_parts.append(f"setpts={1.0 / sp:.6f}*PTS")

    if fx.zoom_factor > 1.0:
        zf = fx.zoom_factor
        vf_parts.append(
            f"scale=ceil(iw*{zf:.3f}/2)*2:ceil(ih*{zf:.3f}/2)*2,"
            f"crop=iw/{zf:.3f}:ih/{zf:.3f}"
        )

    if fx.fade_in_s > 0:
        vf_parts.append(f"fade=t=in:st=0:d={fx.fade_in_s:.2f}")
    if fx.fade_out_s > 0:
        fade_st = max(0.0, out_dur - fx.fade_out_s)
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

    # Audio filters: tempo + volume + pan + fades (Etapa C)
    af_parts: list[str] = []
    if abs(sp - 1.0) > 0.001:
        af_parts.append(_build_atempo(sp))
    # per-clip volume
    vol_gain = max(0.0, float(getattr(fx, "volume_pct", 100.0))) / 100.0
    if abs(vol_gain - 1.0) > 0.002:
        af_parts.append(f"volume={vol_gain:.4f}")
    # per-clip pan (-100..100 → left/right weight)
    pan_norm = max(-1.0, min(1.0, float(getattr(fx, "pan_pct", 0.0)) / 100.0))
    if abs(pan_norm) > 0.01:
        l_gain = 1.0 - max(0.0, pan_norm)   # 1.0 full, 0 when panned right
        r_gain = 1.0 + min(0.0, pan_norm)   # 1.0 full, 0 when panned left
        # pan filter: "pan=stereo|c0=<l>*c0|c1=<r>*c1"
        af_parts.append(f"pan=stereo|c0={l_gain:.4f}*c0|c1={r_gain:.4f}*c1")
    # audio fades
    if fx.fade_in_s > 0:
        af_parts.append(f"afade=t=in:st=0:d={fx.fade_in_s:.2f}")
    if fx.fade_out_s > 0:
        afo_st = max(0.0, out_dur - fx.fade_out_s)
        af_parts.append(f"afade=t=out:st={afo_st:.2f}:d={fx.fade_out_s:.2f}")
    if af_parts:
        cmd += ["-af", ",".join(af_parts)]

    cmd += ["-c:v", encoder, *enc_args, "-c:a", "aac", "-b:a", "192k",
            "-f", "mpegts", output_path]

    pm.run_checked(cmd, context=f"segmento {Path(output_path).stem}",
                   timeout_s=timeout_s)


# -- Text-overlay burn-in (text_track → drawtext over joined video) -----------

def _apply_text_overlays(
    input_path: str,
    text_clips: list[dict],
    output_path: str,
    encoder: str,
    enc_args: list[str],
    pm: "ProcessManager",
) -> None:
    """Burn text_track clips onto the video using ffmpeg drawtext with time ranges."""
    vf_parts: list[str] = []
    for tc in text_clips:
        text = tc.get("text_overlay", "").strip()
        if not text:
            continue
        start_s = float(tc.get("start_s", 0))
        end_s   = float(tc.get("end_s",   start_s + 3))
        if end_s <= start_s:
            continue

        escaped   = _escape_drawtext(text)
        x_pct     = float(tc.get("text_position_x_pct", 0))   / 100.0
        y_pct     = float(tc.get("text_position_y_pct", 72))  / 100.0
        size_pct  = float(tc.get("text_size_pct",       100)) / 100.0
        font_size = max(12, int(24 * size_pct))
        hex_col   = tc.get("text_color", "#ffffff").lstrip("#")
        col_ffmpeg = f"0x{hex_col}ff" if len(hex_col) == 6 else "white"
        bold      = bool(tc.get("text_bold", False))
        font_path = _find_font(bold)
        font_part = f":fontfile='{font_path}'" if font_path else ""
        x_expr    = f"(w-text_w)/2+w*{x_pct:.4f}"
        y_expr    = f"h*{y_pct:.4f}-text_h/2"
        # ffmpeg drawtext enable expression: commas inside must be escaped with backslash
        enable    = f"between(t\\,{start_s:.3f}\\,{end_s:.3f})"

        vf_parts.append(
            f"drawtext=text='{escaped}'{font_part}"
            f":fontcolor={col_ffmpeg}:fontsize={font_size}"
            f":x={x_expr}:y={y_expr}"
            f":shadowcolor=black@0.75:shadowx=2:shadowy=2"
            f":enable='{enable}'"
        )

    if not vf_parts:
        import shutil
        shutil.copy2(input_path, output_path)
        return

    total_dur = 0.0
    try:
        import subprocess as _sp
        from src.ffmpeg_env import ffprobe as _ffprobe
        r = _sp.run(
            [_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", input_path],
            capture_output=True, text=True,
        )
        total_dur = float(r.stdout.strip() or 0)
    except Exception:
        pass

    vf_str  = ",".join(vf_parts)
    timeout = max(60.0, total_dur * 10.0)
    pm.run_checked(
        [ffmpeg(), "-y", "-i", input_path,
         "-vf", vf_str,
         "-c:v", encoder, *enc_args,
         "-c:a", "copy",
         output_path],
        context="texto/legendas",
        timeout_s=timeout,
    )


# -- Image-overlay burn-in (overlay_track image clips) -----------------------

def _apply_image_overlays(
    input_path: str,
    image_clips: list[dict],
    output_path: str,
    encoder: str,
    enc_args: list[str],
    pm: "ProcessManager",
) -> None:
    """Burn image overlay clips onto the video using ffmpeg overlay filter.

    Each image is scaled (with letterbox) to match the video frame size, then
    overlaid for the clip's [start_s, end_s] window.  Multiple clips (even from
    different files) are chained through successive overlay stages.
    """
    # Probe video dimensions for scaling
    vid_w, vid_h = 1920, 1080
    total_dur = 0.0
    try:
        r = subprocess.run(
            [ffprobe(), "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", input_path],
            capture_output=True, text=True, timeout=10,
        )
        parts = r.stdout.strip().split(",")
        if len(parts) == 2:
            vid_w, vid_h = int(parts[0]), int(parts[1])
    except Exception:
        pass

    try:
        r = subprocess.run(
            [ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", input_path],
            capture_output=True, text=True, timeout=10,
        )
        total_dur = float(r.stdout.strip() or 0)
    except Exception:
        pass

    cmd = [ffmpeg(), "-y", "-i", input_path]

    # Deduplicate image paths: same file → same input index
    src_to_idx: dict[str, int] = {}
    next_idx = 1  # index 0 = video
    for c in image_clips:
        sp = c["source_path"]
        if sp not in src_to_idx:
            cmd += ["-i", sp]
            src_to_idx[sp] = next_idx
            next_idx += 1

    # Pre-filter to only clips with valid duration (prevents label-mismatch bugs)
    valid_clips = [
        c for c in image_clips
        if float(c.get("end_s", 0)) > float(c.get("start_s", 0))
    ]
    if not valid_clips:
        import shutil
        shutil.copy2(input_path, output_path)
        return

    fc_parts: list[str] = []
    current = "[0:v]"

    for i, c in enumerate(valid_clips):
        start_s   = float(c.get("start_s", 0))
        end_s     = float(c.get("end_s",   start_s + 3))
        opacity   = max(0.0, min(1.0, float(c.get("opacity_pct", 100)) / 100.0))
        src_idx   = src_to_idx[c["source_path"]]
        is_last   = (i == len(valid_clips) - 1)
        out_label = "[outv]" if is_last else f"[ov{i}]"

        # Scale image to exact video frame size (letterbox with black bars)
        scale_filter = (
            f"scale={vid_w}:{vid_h}:force_original_aspect_ratio=decrease,"
            f"pad={vid_w}:{vid_h}:(ow-iw)/2:(oh-ih)/2:black"
        )
        # Apply opacity via colorchannelmixer if < 100 %
        if opacity < 0.995:
            op = f"{opacity:.4f}"
            scale_filter += (
                f",colorchannelmixer={op}:{op}:{op}:0:{op}:{op}:{op}:0:{op}:{op}:{op}"
            )
        fc_parts.append(f"[{src_idx}:v]{scale_filter}[simg{i}]")

        # Overlay with time-range enable
        fc_parts.append(
            f"{current}[simg{i}]overlay=0:0"
            f":enable='between(t\\,{start_s:.3f}\\,{end_s:.3f})'"
            f"{out_label}"
        )
        current = out_label

    fc_str  = ";".join(fc_parts)
    timeout = max(60.0, total_dur * 10.0)

    cmd += [
        "-filter_complex", fc_str,
        "-map", "[outv]", "-map", "0:a",
        "-c:v", encoder, *enc_args,
        "-c:a", "copy",
        output_path,
    ]
    pm.run_checked(cmd, context="image overlays", timeout_s=timeout)


# -- Transition join (xfade) --------------------------------------------------

_XFADE_MAP: dict[str, str] = {
    "fade":      "fade",
    "dissolver": "dissolve",
    "dissolve":  "dissolve",
    "wipe esq":  "wipeleft",
    "wipe dir":  "wiperight",
    "wipeleft":  "wipeleft",
    "wiperight": "wiperight",
    "zoom":      "zoom",
}


def _xfade_name(transition: str) -> str:
    """Map UI transition label → ffmpeg xfade transition name."""
    return _XFADE_MAP.get(transition.strip().lower(), "fade")


def _join_with_xfade(
    seg_paths: list[str],
    durations:  list[float],   # output durations (after speed) per segment
    transitions: list[str],    # transition[i] = transition BEFORE segment i
    transition_dur: float,
    output: str,
    pm: ProcessManager,
    encoder: str,
    enc_args: list[str],
) -> None:
    """Join segments with ffmpeg xfade filter_complex for smooth transitions.

    Segments with "Corte" transition receive a near-instant cut (0.001s fade)
    so the same filter_complex handles mixed cut + xfade timelines.
    """
    n = len(seg_paths)
    if n == 0:
        return
    if n == 1:
        pm.run_checked(
            [ffmpeg(), "-y", "-i", seg_paths[0], "-c", "copy", output],
            context="xfade-single", timeout_s=60,
        )
        return

    # Clamp transition duration to a safe range
    td = max(0.08, min(1.5, float(transition_dur)))

    cmd = [ffmpeg(), "-y"]
    for p in seg_paths:
        cmd += ["-i", p]

    # ── filter_complex ────────────────────────────────────────────────────────
    fc: list[str] = []

    # Audio: simple concat (xfade on video only; audio cross-fade is costly)
    audio_inputs = "".join(f"[{i}:a]" for i in range(n))
    fc.append(f"{audio_inputs}concat=n={n}:v=0:a=1[outa]")

    # Video: chain xfades
    # offset for xfade[i] = sum of (durations[0..i-1]) - sum of overlaps[1..i]
    cumulative_offset = 0.0
    current_v = "[0:v]"
    for i in range(1, n):
        is_cut = transitions[i].strip().lower() in ("corte", "cut", "")
        effective_td = 0.001 if is_cut else td
        xf_name      = "fade" if is_cut else _xfade_name(transitions[i])

        # offset = where in the OUTPUT timeline the xfade starts
        offset_val = max(0.001, cumulative_offset + durations[i - 1] - effective_td)

        out_label = f"[xf{i}]" if i < n - 1 else "[outv]"
        fc.append(
            f"{current_v}[{i}:v]"
            f"xfade=transition={xf_name}:duration={effective_td:.4f}:offset={offset_val:.4f}"
            f"{out_label}"
        )
        current_v = out_label

        # Accumulate: next offset uses duration minus the overlap for this step
        cumulative_offset += durations[i - 1] - effective_td

    fc_str    = ";".join(fc)
    total_dur = sum(durations) - sum(
        (td if transitions[i].strip().lower() not in ("corte", "cut", "") else 0.001)
        for i in range(1, n)
    )
    timeout = max(120.0, total_dur * 30.0)

    cmd += [
        "-filter_complex", fc_str,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", encoder, *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        output,
    ]
    pm.run_checked(cmd, context="xfade join", timeout_s=timeout)


# -- Music mixer --------------------------------------------------------------

def _mix_music(
    video_path: str,
    music_path: str,
    output_path: str,
    video_duration: float,
    pm: ProcessManager,
    music_volume_pct: float = 13.0,
) -> None:
    fade_out_start = max(0.0, video_duration - 2.5)
    # music_volume_pct 0..200 → ffmpeg volume filter 0..2.0
    music_vol = max(0.0, float(music_volume_pct) / 100.0)
    af = (
        f"[1:a]volume={music_vol:.4f},"
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
