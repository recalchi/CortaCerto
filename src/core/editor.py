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
from .effect_renderer import render_person_removal_pass
from .process_manager import ProcessManager, CancelledError          # canonical source
from .text_render import TextStyle, render_text_on_frame
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
_TARGET_CANVAS_BY_AR = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1":  (1080, 1080),
    "4:5":  (1080, 1350),
    "5:4":  (1350, 1080),
    "4:3":  (1440, 1080),
    "3:4":  (1080, 1440),
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


def _resolve_target_canvas(aspect_ratio: Optional[str], platform: str) -> Optional[tuple[int, int]]:
    """Return fixed export dimensions for editor preview/export parity."""
    if aspect_ratio and aspect_ratio not in ("original", ""):
        return _TARGET_CANVAS_BY_AR.get(aspect_ratio)
    legacy_ar = _LEGACY_PLATFORM_AR.get(platform)
    if legacy_ar == (9, 16):
        return _TARGET_CANVAS_BY_AR["9:16"]
    return None


def _fit_to_canvas_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,format=yuv420p"
    )


def _drawtext_font_size(size_pct: float, video_height: int) -> int:
    height = max(240, int(video_height or 1080))
    return max(14, int(round(height * 0.045 * max(10.0, float(size_pct)) / 100.0)))


def _probe_video_size(video_path: str) -> tuple[int, int]:
    try:
        result = subprocess.run(
            [
                ffprobe(), "-v", "quiet", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0", video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        parts = result.stdout.strip().split(",")
        if len(parts) >= 2:
            return max(1, int(parts[0])), max(1, int(parts[1]))
    except Exception:
        pass
    return 1920, 1080


def _is_cut_transition(name: str) -> bool:
    return str(name or "").strip().lower() in ("", "corte", "cut")


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
    temperature:     float = 0.0
    hue:             float = 0.0
    exposure:        float = 0.0
    sharpness:       float = 0.0
    vignette:        float = 0.0
    blur_type:       str   = "none"     # none|gaussian|box|pixelate
    blur_intensity:  float = 0.0        # 0..100
    blur_direction:  str   = "both"     # both|horizontal|vertical
    crop_top_pct:    float = 0.0    # 0..50 percent of height to crop from top
    crop_bottom_pct: float = 0.0
    crop_left_pct:   float = 0.0
    crop_right_pct:  float = 0.0
    scale_pct:       float = 100.0  # 10..300 (100 = original size)
    position_x:      float = 0.0    # px in the project frame
    position_y:      float = 0.0
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
    # Person removal / moving subject mask
    person_remove_enabled: bool = False
    person_remove_strength: float = 72.0
    person_remove_feather: float = 10.0


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
    audio_clips: Optional[list[dict]] = None,      # [{source_path, start_s, end_s, source_offset_s, volume_pct}]
    normalize_audio: bool = False,                 # Apply loudnorm post-processing only when enabled
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
    effects   = [SegmentEffect() for _ in segments] if per_clip_data else _plan_effects(segments)
    target_canvas = _resolve_target_canvas(aspect_ratio, platform)

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
            fx.position_x      = float(clip_data.get("position_x",      0.0)   or 0.0)
            fx.position_y      = float(clip_data.get("position_y",      0.0)   or 0.0)
            fx.opacity_pct     = float(clip_data.get("opacity_pct",     100.0) or 100.0)
            fx.rotation_deg    = float(clip_data.get("rotation_deg",    0.0)   or 0.0)
            fx.temperature     = float(clip_data.get("temperature",     0.0)   or 0.0)
            fx.hue             = float(clip_data.get("hue",             0.0)   or 0.0)
            fx.exposure        = float(clip_data.get("exposure",        0.0)   or 0.0)
            fx.sharpness       = float(clip_data.get("sharpness",       0.0)   or 0.0)
            fx.vignette        = float(clip_data.get("vignette",        0.0)   or 0.0)
            fx.blur_type       = str(clip_data.get("blur_type", "none") or "none")
            fx.blur_intensity  = max(0.0, min(100.0, float(clip_data.get("blur_intensity", 0.0) or 0.0)))
            fx.blur_direction  = str(clip_data.get("blur_direction", "both") or "both")
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
            fx.person_remove_enabled = bool(clip_data.get("person_remove_enabled", False))
            fx.person_remove_strength = max(10.0, min(100.0, float(clip_data.get("person_remove_strength", 72.0) or 72.0)))
            fx.person_remove_feather = max(0.0, min(30.0, float(clip_data.get("person_remove_feather", 10.0) or 10.0)))
        for i in range(1, n):
            if _is_cut_transition(effects[i].transition) and not _is_cut_transition(effects[i - 1].transition):
                effects[i].transition = effects[i - 1].transition
                effects[i].transition_duration_s = effects[i - 1].transition_duration_s
        # Clear auto-generated segment fades when a user xfade transition is set
        for i, fx in enumerate(effects):
            if i > 0 and not _is_cut_transition(fx.transition):
                fx.fade_in_s = 0.0
            if i < n - 1 and not _is_cut_transition(effects[i + 1].transition):
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
            any_real_audio = False
            source_audio_cache: dict[str, bool] = {}

            for i, ((start, end), fx) in enumerate(zip(segments, effects)):
                pm.check_cancel()

                # Multi-source: translate project-time → source-file time
                seg_src  = (source_paths[i] if source_paths and i < len(source_paths) and source_paths[i] else None) or video_path
                seg_off  = (source_offsets[i] if source_offsets and i < len(source_offsets) else 0.0)
                if seg_src not in source_audio_cache:
                    source_audio_cache[seg_src] = _has_audio_stream(seg_src)
                seg_has_audio = source_audio_cache[seg_src]
                any_real_audio = any_real_audio or seg_has_audio
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
                raw_seg_path = os.path.join(tmp, f"seg_{i:05d}_raw.ts") if fx.person_remove_enabled else seg_path
                # Speed factor changes output duration
                sp       = max(0.1, fx.speed_factor)
                out_dur  = seg_dur / sp
                timeout  = max(45.0, seg_dur * 60.0)

                _render_segment(
                    seg_src, src_start, src_end, raw_seg_path,
                    fx, "", encoder, enc_args,
                    pm=pm, timeout_s=timeout, has_audio=seg_has_audio,
                    target_size=target_canvas,
                )
                if fx.person_remove_enabled:
                    prog(f"Removendo pessoa do segmento {i + 1}/{n}...", min(0.79, pct + 0.01))
                    render_person_removal_pass(
                        raw_seg_path,
                        seg_path,
                        strength=fx.person_remove_strength,
                        feather=fx.person_remove_feather,
                        cancel=cancel,
                        on_progress=lambda msg, p, base=pct: prog(msg, min(0.79, base + p * 0.04)),
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
                try:
                    _join_with_xfade(
                        seg_paths, output_durations, transitions, default_td,
                        joined_path, pm, encoder, enc_args,
                    )
                except RuntimeError:
                    # Robust fallback: if xfade fails in this environment, keep export alive
                    # by joining with hard cuts instead of crashing.
                    prog("Falha em transições; retomando com cortes simples...", 0.812)
                    concat_input = "concat:" + "|".join(seg_paths)
                    pm.run_checked(
                        [ffmpeg(), "-y", "-i", concat_input, "-c", "copy", joined_path],
                        context="unir segmentos (fallback sem transições)", timeout_s=120,
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
            if noise_reduction and any_real_audio:
                af_parts.append("afftdn=nf=-25")
            if normalize_audio and any_real_audio:
                af_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
            elif (noise_reduction or normalize_audio) and not any_real_audio:
                prog("Audio real ausente; pulando reducao/normalizacao para evitar falha no FFmpeg.", 0.875)

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
            active_visuals = [
                ic for ic in (image_clips or [])
                if ic.get("source_path") and os.path.isfile(ic["source_path"])
                and float(ic.get("end_s", 0)) > float(ic.get("start_s", 0))
            ]
            active_visuals.sort(
                key=lambda ic: (
                    float(ic.get("z_order", 0) or 0),
                    float(ic.get("start_s", 0) or 0),
                )
            )
            if active_visuals:
                pm.check_cancel()
                prog("Aplicando overlays visuais...", 0.91)
                pre_img   = joined_path
                joined_path = os.path.join(tmp, "with_visuals.mp4")
                _apply_visual_overlays(
                    pre_img, active_visuals, joined_path,
                    encoder, enc_args, pm,
                )

            # -- Timeline audio clips --------------------------------------
            active_audio_clips = [
                ac for ac in (audio_clips or [])
                if ac.get("source_path") and os.path.isfile(str(ac.get("source_path")))
                and float(ac.get("end_s", 0) or 0) > float(ac.get("start_s", 0) or 0)
            ]
            if active_audio_clips:
                pm.check_cancel()
                prog("Mixando faixas de audio da timeline...", 0.915)
                pre_audio_clips = joined_path
                joined_path = os.path.join(tmp, "with_timeline_audio.mp4")
                _mix_timeline_audio_clips(
                    pre_audio_clips,
                    active_audio_clips,
                    joined_path,
                    total_dur,
                    pm=pm,
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
            target_ar = None if target_canvas is not None else _resolve_target_aspect(aspect_ratio, platform)
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


def _has_audio_stream(video_path: str) -> bool:
    """Return True when *video_path* has at least one audio stream."""
    try:
        result = subprocess.run(
            [
                ffprobe(), "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


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

_FONT_BY_NAME_REGULAR = {
    "arial": r"C:\Windows\Fonts\arial.ttf",
    "helvetica": r"C:\Windows\Fonts\arial.ttf",
    "georgia": r"C:\Windows\Fonts\georgia.ttf",
    "courier": r"C:\Windows\Fonts\cour.ttf",
    "courier new": r"C:\Windows\Fonts\cour.ttf",
    "impact": r"C:\Windows\Fonts\impact.ttf",
    "verdana": r"C:\Windows\Fonts\verdana.ttf",
    "times": r"C:\Windows\Fonts\times.ttf",
    "times new roman": r"C:\Windows\Fonts\times.ttf",
}
_FONT_BY_NAME_BOLD = {
    "arial": r"C:\Windows\Fonts\arialbd.ttf",
    "helvetica": r"C:\Windows\Fonts\arialbd.ttf",
    "georgia": r"C:\Windows\Fonts\georgiab.ttf",
    "courier": r"C:\Windows\Fonts\courbd.ttf",
    "courier new": r"C:\Windows\Fonts\courbd.ttf",
    "impact": r"C:\Windows\Fonts\impact.ttf",
    "verdana": r"C:\Windows\Fonts\verdanab.ttf",
    "times": r"C:\Windows\Fonts\timesbd.ttf",
    "times new roman": r"C:\Windows\Fonts\timesbd.ttf",
}


def _find_font(bold: bool = False) -> str:
    """Return path to best available font file, or empty string for built-in."""
    candidates = (_FONT_CANDIDATES_BOLD if bold else []) + _FONT_CANDIDATES_REGULAR
    for c in candidates:
        if os.path.exists(c):
            return c
    return ""


def _find_font_by_name(name: str, bold: bool = False) -> str:
    """Best-effort font picker by name, with fallback to default candidates."""
    key = (name or "").strip().lower()
    if key:
        table = _FONT_BY_NAME_BOLD if bold else _FONT_BY_NAME_REGULAR
        candidate = table.get(key)
        if candidate and os.path.exists(candidate):
            return candidate
    return _find_font(bold=bold)


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


def _hex_to_ff_color(hex_color: str, alpha: float = 1.0, fallback: str = "white") -> str:
    """Convert #rrggbb + alpha(0..1) -> ffmpeg color 0xRRGGBBAA."""
    if not isinstance(hex_color, str):
        return fallback
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        return fallback
    try:
        int(h, 16)
    except ValueError:
        return fallback
    aa = max(0, min(255, int(round(max(0.0, min(1.0, alpha)) * 255.0))))
    return f"0x{h}{aa:02x}"


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


def _build_blur_filter(blur_type: str, intensity: float, direction: str = "both") -> str:
    kind = str(blur_type or "none").strip().lower()
    amount = max(0.0, min(100.0, float(intensity or 0.0)))
    if kind in ("", "none") or amount <= 0.5:
        return ""
    direction = str(direction or "both").strip().lower()
    if kind == "pixelate":
        block = max(4, min(80, int(4 + amount * 0.72)))
        return f"pixelize=width={block}:height={block}:mode=avg"
    if kind == "box":
        radius = max(1, min(40, int(round(amount * 0.38))))
        return f"boxblur=luma_radius={radius}:luma_power=2:chroma_radius={max(1, radius // 2)}:chroma_power=1"
    sigma = max(0.2, min(30.0, amount * 0.26))
    if direction == "horizontal":
        return f"gblur=sigma={sigma:.3f}:sigmaV=0.001"
    if direction == "vertical":
        return f"gblur=sigma=0.001:sigmaV={sigma:.3f}"
    return f"gblur=sigma={sigma:.3f}"


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
    has_audio: Optional[bool] = None,
    target_size: Optional[tuple[int, int]] = None,
) -> None:
    duration = end - start
    sp       = max(0.1, fx.speed_factor)
    # Output duration after speed change (used for fade timing)
    out_dur  = duration / sp
    vf_parts: list[str] = []
    if target_size is not None:
        target_w, target_h = target_size
        if target_w > 0 and target_h > 0:
            vf_parts.append(_fit_to_canvas_filter(target_w, target_h))

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

    blur_filter = _build_blur_filter(fx.blur_type, fx.blur_intensity, fx.blur_direction)
    if blur_filter:
        vf_parts.append(blur_filter)

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
    pos_x = float(getattr(fx, "position_x", 0.0) or 0.0)
    pos_y = float(getattr(fx, "position_y", 0.0) or 0.0)
    if target_size is not None and (abs(sc - 1.0) > 0.005 or abs(pos_x) > 0.01 or abs(pos_y) > 0.01):
        target_w, target_h = target_size
        if sc >= 1.0:
            crop_x = f"max(0\\,min(iw-{target_w}\\,(iw-{target_w})/2{-pos_x:+.3f}))"
            crop_y = f"max(0\\,min(ih-{target_h}\\,(ih-{target_h})/2{-pos_y:+.3f}))"
            vf_parts.append(
                f"scale=ceil(iw*{sc:.4f}/2)*2:ceil(ih*{sc:.4f}/2)*2,"
                f"crop={target_w}:{target_h}:{crop_x}:{crop_y}"
            )
        else:
            pad_x = f"max(0\\,min(ow-iw\\,(ow-iw)/2{pos_x:+.3f}))"
            pad_y = f"max(0\\,min(oh-ih\\,(oh-ih)/2{pos_y:+.3f}))"
            vf_parts.append(
                f"scale=ceil(iw*{sc:.4f}/2)*2:ceil(ih*{sc:.4f}/2)*2,"
                f"pad={target_w}:{target_h}:{pad_x}:{pad_y}:color=black"
            )
    elif abs(sc - 1.0) > 0.005:
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
        video_h = target_size[1] if target_size is not None else _probe_video_size(video_path)[1]
        font_size = _drawtext_font_size(fx.text_size_pct, video_h)
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
    has_audio = _has_audio_stream(video_path) if has_audio is None else bool(has_audio)

    def build_cmd(selected_encoder: str, selected_args: list[str], use_hwaccel: bool = True) -> list[str]:
        cmd = [ffmpeg(), "-y"]
        if use_hwaccel:
            cmd += ["-hwaccel", "auto"]
        cmd += [
            "-ss", f"{start:.4f}", "-to", f"{end:.4f}",
            "-i", video_path,
        ]
        if has_audio:
            cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        else:
            cmd += [
                "-f", "lavfi",
                "-t", f"{out_dur:.4f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
            ]
        if vf_parts:
            cmd += ["-vf", ",".join(vf_parts)]
        if af_parts and has_audio:
            cmd += ["-af", ",".join(af_parts)]
        cmd += [
            "-c:v", selected_encoder, *selected_args,
            "-c:a", "aac", "-b:a", "192k",
            "-f", "mpegts", output_path,
        ]
        return cmd

    context = f"segmento {Path(output_path).stem}"
    primary_cmd = build_cmd(encoder, enc_args, use_hwaccel=True)
    try:
        pm.run_checked(primary_cmd, context=context, timeout_s=timeout_s)
    except RuntimeError:
        if encoder == "libx264":
            raise
        try:
            os.remove(output_path)
        except OSError:
            pass
        fallback_cmd = build_cmd("libx264", ["-crf", "18", "-preset", "fast"], use_hwaccel=False)
        pm.run_checked(
            fallback_cmd,
            context=f"{context} (fallback libx264)",
            timeout_s=timeout_s,
        )


# -- Text-overlay burn-in (text_track → drawtext over joined video) -----------

def _text_style_from_export_clip(tc: dict, frame_height: int) -> TextStyle:
    side_margin = max(0.0, min(35.0, float(tc.get("text_side_margin_pct", 5.0) or 5.0)))
    size_pct = float(tc.get("text_size_pct", 100.0) or 100.0)
    bg_enabled = bool(tc.get("text_background_enabled", False))
    shadow_enabled = bool(tc.get("text_shadow_enabled", not bg_enabled))
    return TextStyle(
        text=str(tc.get("text_overlay") or "").strip(),
        font_family=str(tc.get("text_font") or "default"),
        bold=bool(tc.get("text_bold", False)),
        italic=bool(tc.get("text_italic", False)),
        font_size=max(14, int(max(240, frame_height) * 0.045)),
        size_pct=size_pct,
        color=str(tc.get("text_color") or "#ffffff"),
        align=str(tc.get("text_align") or "center"),
        pos_x_pct=50.0 + float(tc.get("text_position_x_pct", 0.0) or 0.0),
        pos_y_pct=float(tc.get("text_position_y_pct", 72.0) or 72.0),
        bg_enabled=bg_enabled,
        bg_color=str(tc.get("text_background_color") or "#000000"),
        bg_alpha=float(tc.get("text_background_alpha", 0.65) or 0.65),
        bg_padding=12 if bg_enabled else 4,
        bg_rounded=True,
        shadow_enabled=shadow_enabled,
        shadow_color=str(tc.get("text_shadow_color") or "#000000"),
        shadow_offset_x=2,
        shadow_offset_y=2,
        shadow_blur=4,
        stroke_enabled=bool(tc.get("text_stroke_enabled", False)),
        stroke_color=str(tc.get("text_stroke_color") or "#000000"),
        stroke_width=max(0, int(round(float(tc.get("text_stroke_width", 2) or 2)))),
        max_width_pct=max(30.0, min(100.0, 100.0 - side_margin * 2.0)),
        line_spacing=float(tc.get("text_line_spacing", 1.25) or 1.25),
    )


def _apply_text_overlays_framepass(
    input_path: str,
    text_clips: list[dict],
    output_path: str,
    encoder: str,
    enc_args: list[str],
    pm: "ProcessManager",
) -> None:
    import cv2

    active = [
        tc for tc in text_clips
        if str(tc.get("text_overlay") or "").strip()
        and float(tc.get("end_s", 0) or 0) > float(tc.get("start_s", 0) or 0)
    ]
    if not active:
        import shutil
        shutil.copy2(input_path, output_path)
        return

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError("Nao foi possivel abrir video para renderizar texto.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError("Dimensoes invalidas para renderizar texto.")

    tmp_video = str(Path(output_path).with_suffix(".text_video.mp4"))
    writer = cv2.VideoWriter(tmp_video, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Nao foi possivel criar video temporario de texto.")

    styles = [(float(tc.get("start_s", 0) or 0), float(tc.get("end_s", 0) or 0), _text_style_from_export_clip(tc, height)) for tc in active]
    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            t = frame_idx / fps
            for start_s, end_s, style in styles:
                if start_s <= t < end_s:
                    frame = render_text_on_frame(frame, style)
            writer.write(frame)
            frame_idx += 1
    finally:
        cap.release()
        writer.release()

    mux_cmd = [
        ffmpeg(), "-y",
        "-i", tmp_video,
        "-i", input_path,
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-c:v", encoder, *enc_args,
        "-c:a", "copy",
        "-shortest",
        output_path,
    ]
    try:
        pm.run_checked(mux_cmd, context="texto/legendas framepass", timeout_s=max(60.0, (frame_idx / fps) * 12.0))
    except RuntimeError:
        if encoder == "libx264":
            raise
        fallback = [
            ffmpeg(), "-y",
            "-i", tmp_video,
            "-i", input_path,
            "-map", "0:v:0",
            "-map", "1:a:0?",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "copy",
            "-shortest",
            output_path,
        ]
        pm.run_checked(fallback, context="texto/legendas framepass (fallback libx264)", timeout_s=max(60.0, (frame_idx / fps) * 12.0))
    finally:
        try:
            os.remove(tmp_video)
        except OSError:
            pass


def _apply_text_overlays(
    input_path: str,
    text_clips: list[dict],
    output_path: str,
    encoder: str,
    enc_args: list[str],
    pm: "ProcessManager",
) -> None:
    """Burn text_track clips onto the video using ffmpeg drawtext with time ranges."""
    try:
        _apply_text_overlays_framepass(input_path, text_clips, output_path, encoder, enc_args, pm)
        return
    except Exception:
        # Fallback to drawtext below if Pillow/OpenCV frame rendering is unavailable.
        pass

    vf_parts: list[str] = []
    _video_w, video_h = _probe_video_size(input_path)
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
        size_pct  = float(tc.get("text_size_pct",       100))
        font_size = _drawtext_font_size(size_pct, video_h)
        col_ffmpeg = _hex_to_ff_color(str(tc.get("text_color", "#ffffff")), 1.0, "white")
        bold      = bool(tc.get("text_bold", False))
        font_name = str(tc.get("text_font", ""))
        font_path = _find_font_by_name(font_name, bold=bold)
        # Windows paths (e.g. C:\...) need ":" escaped for ffmpeg filter syntax.
        # Also normalize to "/" to reduce backslash escaping pitfalls.
        if font_path:
            font_path_esc = font_path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
            font_part = f":fontfile='{font_path_esc}'"
        else:
            font_part = ""
        align_mode = str(tc.get("text_align", "center") or "center").strip().lower()
        if align_mode == "left":
            x_expr = f"w*0.5+w*{x_pct:.4f}"
        elif align_mode == "right":
            x_expr = f"w*0.5+w*{x_pct:.4f}-text_w"
        else:
            x_expr = f"(w-text_w)/2+w*{x_pct:.4f}"
        y_expr    = f"h*{y_pct:.4f}-text_h/2"
        # ffmpeg drawtext enable expression: commas inside must be escaped with backslash
        enable    = f"between(t\\,{start_s:.3f}\\,{end_s:.3f})"

        bg_enabled = bool(tc.get("text_background_enabled", False))
        bg_color   = _hex_to_ff_color(
            str(tc.get("text_background_color", "#000000")),
            float(tc.get("text_background_alpha", 0.65) or 0.65),
            "black@0.65",
        )
        stroke_enabled = bool(tc.get("text_stroke_enabled", False))
        stroke_width   = max(0, float(tc.get("text_stroke_width", 2) or 2))
        stroke_color   = _hex_to_ff_color(str(tc.get("text_stroke_color", "#000000")), 1.0, "black")
        shadow_enabled = bool(tc.get("text_shadow_enabled", True))

        draw = (
            f"drawtext=text='{escaped}'{font_part}"
            f":fontcolor={col_ffmpeg}:fontsize={font_size}"
            f":x={x_expr}:y={y_expr}"
            f":enable='{enable}'"
        )
        if shadow_enabled:
            draw += f":shadowcolor=black@0.75:shadowx=2:shadowy=2"
        else:
            draw += f":shadowcolor=black@0.0:shadowx=0:shadowy=0"
        if bg_enabled:
            draw += f":box=1:boxcolor={bg_color}:boxborderw=8"
        if stroke_enabled and stroke_width > 0:
            draw += f":borderw={stroke_width:.2f}:bordercolor={stroke_color}"

        vf_parts.append(draw)

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
    """Burn visual overlays (image + video) onto the base video."""
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


def _apply_visual_overlays(
    input_path: str,
    visual_clips: list[dict],
    output_path: str,
    encoder: str,
    enc_args: list[str],
    pm: "ProcessManager",
) -> None:
    """Apply image/video overlays in one pass (full-frame, timeline-timed)."""

    # Probe base dimensions + duration
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

    valid: list[dict] = []
    for c in visual_clips:
        try:
            src = str(c.get("source_path") or "").strip()
            if not src or not os.path.isfile(src):
                continue
            start_s = float(c.get("start_s", 0.0) or 0.0)
            end_s = float(c.get("end_s", start_s) or start_s)
            if end_s <= start_s:
                continue
            valid.append({
                "source_path": src,
                "start_s": start_s,
                "end_s": end_s,
                "opacity_pct": float(c.get("opacity_pct", 100.0) or 100.0),
                "clip_type": str(c.get("clip_type", "image") or "image").strip().lower(),
                "z_order": float(c.get("z_order", 0.0) or 0.0),
                "position_x": float(c.get("position_x", 0.0) or 0.0),
                "position_y": float(c.get("position_y", 0.0) or 0.0),
                "scale_pct": float(c.get("scale_pct", 100.0) or 100.0),
                "rotation_deg": float(c.get("rotation_deg", 0.0) or 0.0),
            })
        except Exception:
            continue

    if not valid:
        import shutil
        shutil.copy2(input_path, output_path)
        return

    valid.sort(key=lambda c: (float(c.get("z_order", 0.0)), float(c.get("start_s", 0.0))))

    cmd = [ffmpeg(), "-y", "-i", input_path]
    for c in valid:
        if c["clip_type"] == "image":
            cmd += ["-loop", "1", "-i", c["source_path"]]
        else:
            cmd += ["-i", c["source_path"]]

    parts: list[str] = []
    current = "[0:v]"
    for i, c in enumerate(valid):
        src_idx = i + 1
        start_s = float(c["start_s"])
        end_s = float(c["end_s"])
        clip_dur = max(0.01, end_s - start_s)
        opacity = max(0.0, min(1.0, float(c["opacity_pct"]) / 100.0))
        pos_x = float(c.get("position_x", 0.0) or 0.0)
        pos_y = float(c.get("position_y", 0.0) or 0.0)
        user_scale = max(0.1, min(5.0, float(c.get("scale_pct", 100.0) or 100.0) / 100.0))
        user_rot = float(c.get("rotation_deg", 0.0) or 0.0)
        is_last = i == len(valid) - 1
        out_label = "[outv]" if is_last else f"[ov{i}]"
        x_expr = f"(W-w)/2+{pos_x:.2f}"
        y_expr = f"(H-h)/2+{pos_y:.2f}"

        prep_parts = [
            f"scale={vid_w}:{vid_h}:force_original_aspect_ratio=decrease",
        ]
        if abs(user_scale - 1.0) > 0.001:
            prep_parts.append(f"scale=iw*{user_scale:.5f}:ih*{user_scale:.5f}")
        if abs(user_rot) > 0.01:
            prep_parts.append(f"rotate={user_rot:.5f}*PI/180:c=none:ow=rotw(iw):oh=roth(ih)")
        prep_parts.extend([
            "format=rgba",
        ])
        prep = ",".join(prep_parts)
        if opacity < 0.995:
            prep += f",colorchannelmixer=aa={opacity:.4f}"

        if c["clip_type"] == "image":
            parts.append(f"[{src_idx}:v]{prep}[vov{i}]")
            parts.append(
                f"{current}[vov{i}]overlay=x='{x_expr}':y='{y_expr}'"
                f":enable='between(t\\,{start_s:.3f}\\,{end_s:.3f})'"
                f"{out_label}"
            )
        else:
            parts.append(
                f"[{src_idx}:v]setpts=PTS-STARTPTS,"
                f"trim=duration={clip_dur:.3f},"
                f"{prep},"
                f"setpts=PTS+{start_s:.3f}/TB"
                f"[vov{i}]"
            )
            parts.append(
                f"{current}[vov{i}]overlay=x='{x_expr}':y='{y_expr}':eof_action=pass"
                f"{out_label}"
            )
        current = out_label

    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[outv]", "-map", "0:a?",
        "-c:v", encoder, *enc_args,
        "-c:a", "copy",
        output_path,
    ]
    pm.run_checked(cmd, context="visual overlays", timeout_s=max(60.0, total_dur * 10.0))


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
    def _run_with_encoder(enc_name: str, enc_extra: list[str], context: str) -> None:
        run_cmd = cmd + [
            "-filter_complex", fc_str,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", enc_name, *enc_extra,
            "-c:a", "aac", "-b:a", "192k",
            output,
        ]
        pm.run_checked(run_cmd, context=context, timeout_s=timeout)

    try:
        _run_with_encoder(encoder, enc_args, "xfade join")
    except RuntimeError:
        # Hardware encoders (especially QSV/AMF) can fail with xfade filter graphs
        # depending on driver/runtime. Retry on CPU to avoid export crash.
        if encoder != "libx264":
            _run_with_encoder("libx264", ["-crf", "18", "-preset", "fast"], "xfade join (fallback libx264)")
        else:
            raise


# -- Music mixer --------------------------------------------------------------

def _valid_timeline_audio_clips(audio_clips: list[dict]) -> list[dict]:
    valid: list[dict] = []
    for clip in audio_clips:
        path = str(clip.get("source_path") or "")
        start_s = float(clip.get("start_s", 0.0) or 0.0)
        end_s = float(clip.get("end_s", 0.0) or 0.0)
        if not path or end_s <= start_s:
            continue
        valid.append(clip)
    return valid


def _build_timeline_audio_mix_filter(audio_clips: list[dict]) -> str:
    valid = _valid_timeline_audio_clips(audio_clips)
    parts = ["[0:a]anull[a0]"]
    labels = ["[a0]"]
    for idx, clip in enumerate(valid, start=1):
        start_s = max(0.0, float(clip.get("start_s", 0.0) or 0.0))
        end_s = max(start_s, float(clip.get("end_s", start_s) or start_s))
        duration = max(0.01, end_s - start_s)
        source_offset = float(clip.get("source_offset_s", start_s) or 0.0)
        source_start = max(0.0, start_s - source_offset)
        delay_ms = max(0, int(round(start_s * 1000.0)))
        volume = max(0.0, min(2.0, float(clip.get("volume_pct", 100.0) or 100.0) / 100.0))
        fade_in = max(0.0, float(clip.get("fade_in_s", 0.0) or 0.0))
        fade_out = max(0.0, float(clip.get("fade_out_s", 0.0) or 0.0))
        chain = (
            f"[{idx}:a]atrim=start={source_start:.3f}:duration={duration:.3f},"
            "asetpts=PTS-STARTPTS,"
            f"volume={volume:.4f}"
        )
        if fade_in > 0.001:
            chain += f",afade=t=in:st=0:d={min(fade_in, duration):.3f}"
        if fade_out > 0.001:
            chain += f",afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={min(fade_out, duration):.3f}"
        chain += f",adelay={delay_ms}|{delay_ms}[a{idx}]"
        parts.append(chain)
        labels.append(f"[a{idx}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0[outa]")
    return ";".join(parts)


def _mix_timeline_audio_clips(
    video_path: str,
    audio_clips: list[dict],
    output_path: str,
    video_duration: float,
    pm: ProcessManager,
) -> None:
    valid = [
        clip for clip in _valid_timeline_audio_clips(audio_clips)
        if os.path.isfile(str(clip.get("source_path") or ""))
    ]
    if not valid:
        import shutil
        shutil.copy2(video_path, output_path)
        return
    cmd = [ffmpeg(), "-y", "-i", video_path]
    for clip in valid:
        cmd += ["-i", str(clip.get("source_path") or "")]
    filter_complex = _build_timeline_audio_mix_filter(valid)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[outa]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    pm.run_checked(cmd, context="mix audio timeline", timeout_s=max(60.0, video_duration * 6.0))

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
