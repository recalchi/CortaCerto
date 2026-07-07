"""
Pipeline orchestrator — ties together all processing steps.
Passes a threading.Event (cancel) through to ffmpeg calls so the
cancel button actually kills running processes immediately.
"""
from __future__ import annotations

import os
import shutil
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import ProcessingConfig, PRESETS
from .core.analyzer import analyze_video, AudioAnalysis
from .core.effect_renderer import render_clip_source_pass, render_effects_pass
from .core.editor import (
    cut_silence, convert_to_vertical,
    get_video_duration, RenderStats, CancelledError, _mix_music,
    _has_audio_stream,
)
from .core.process_manager import ProcessManager
from .core.thumbnail import detect_person_from_video
from .core.thumbnail_pro import generate_thumbnails_pro
from .ffmpeg_env import detect_video_encoder, ffmpeg


@dataclass
class PipelineResult:
    output_dir: str
    main_video:      Optional[str] = None
    vertical_video:  Optional[str] = None
    thumbnail:       Optional[str] = None
    thumbnails_all:  list[str] = field(default_factory=list)  # multi-frame variants
    analysis:        Optional[AudioAnalysis] = None
    render_stats:    Optional[RenderStats] = None
    original_duration_s: float = 0.0
    final_duration_s:    float = 0.0
    production_time_s:   float = 0.0
    error: Optional[str] = None
    cancelled: bool = False

    @property
    def success(self) -> bool:
        return self.error is None and not self.cancelled

    @property
    def silence_removed_s(self) -> float:
        return max(0.0, self.original_duration_s - self.final_duration_s)

    @property
    def compression_pct(self) -> float:
        if self.original_duration_s <= 0:
            return 0.0
        return self.silence_removed_s / self.original_duration_s * 100.0


def run_pipeline(
    video_path: str,
    output_dir: str,
    config: ProcessingConfig,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> PipelineResult:
    result = PipelineResult(output_dir=output_dir)
    os.makedirs(output_dir, exist_ok=True)
    base        = Path(video_path).stem
    t_start     = time.monotonic()
    cancel      = cancel or threading.Event()

    def prog(msg: str, pct: float) -> None:
        if on_progress:
            on_progress(msg, pct)

    try:
        # ── 1. Original duration ────────────────────────────────────────────
        result.original_duration_s = get_video_duration(video_path)
        source_has_audio = _has_audio_stream(video_path)
        encoder, _ = detect_video_encoder()
        prog(f"[GPU] Selected mode: encode={encoder}; efeitos OpenCV/bokeh rodam em CPU.", 0.01)
        prog(f"[EXPORT] Saídas selecionadas: {_export_output_plan(config)}.", 0.01)
        effective_clip_options = _clip_options_for_track_options(config.clip_options, config.track_options)
        clip_plan = _clip_option_plan(effective_clip_options)
        if clip_plan:
            prog(f"[EXPORT] Ajustes por clipe recebidos: {clip_plan}.", 0.01)

        # ── 1b. Face/person detection (used for bokeh + thumbnail layout) ───
        if config.bokeh_intensity >= 0.05 or config.generate_thumbnail:
            prog("[1/6] Detectando sujeito na cena...", 0.02)
            fx, fy, fs = detect_person_from_video(video_path, at_second=5.0)
            config.face_x    = fx
            config.face_y    = fy
            config.face_size = fs
            prog(f"[1/6] Sujeito detectado em x={fx:.2f} y={fy:.2f} (tamanho {fs:.2f})", 0.05)

        # ── 2. Audio analysis ───────────────────────────────────────────────
        if config.manual_segments is not None:
            manual_segments = _normalize_manual_segments(
                config.manual_segments,
                result.original_duration_s,
            )
            manual_time = sum(end - start for start, end in manual_segments)
            analysis = AudioAnalysis(
                duration_s=result.original_duration_s,
                speech_segments=manual_segments,
                silence_ratio=1.0 - (manual_time / result.original_duration_s) if result.original_duration_s > 0 else 0.0,
            )
            result.analysis = analysis
            prog(f"[2/6] Timeline manual aplicada: {len(manual_segments)} clipes.", 0.15)
        elif config.remove_silence:
            prog("[2/6] Analisando áudio...", 0.06)
            analysis = analyze_video(
                video_path,
                silence_threshold_db=config.silence_threshold_db,
                min_silence_ms=config.min_silence_ms,
                audio_padding_ms=config.audio_padding_ms,
                min_segment_s=config.min_segment_s,
                on_progress=lambda msg: prog(f"[2/6] {msg}", 0.10),
            )
            result.analysis = analysis
        else:
            analysis = AudioAnalysis(
                duration_s=result.original_duration_s,
                speech_segments=[(0.0, result.original_duration_s)] if result.original_duration_s > 0 else [],
                silence_ratio=0.0,
            )
            result.analysis = analysis
            prog("[2/6] Corte de silêncio desativado; pulando análise de áudio.", 0.16)

        if config.remove_silence or config.manual_segments is not None:
            pct = analysis.silence_ratio * 100
            prog(f"[2/6] Análise: {pct:.1f}% silêncio e {len(analysis.speech_segments)} segmentos.", 0.16)

        # ── 3. Cut silence + effects ────────────────────────────────────────
        source = video_path
        export_keep: set[str] = set()
        export_intermediate: set[str] = set()
        clip_options = _clip_options_with_output_ranges(effective_clip_options, config.manual_segments)
        if config.remove_silence:
            main_out = os.path.join(output_dir, f"{base}_editado.mp4")
            # Etapa 6: per-clip speed + transition data (base track only)
            _per_clip_data = _build_per_clip_data(clip_options)
            render_stats = cut_silence(
                video_path,
                analysis,
                main_out,
                crf=config.video_crf,
                preset=config.video_preset,
                color_grade=None,
                music_path=None,
                noise_reduction=False,
                bokeh_intensity=0.0,
                face_x=config.face_x,
                face_y=config.face_y,
                face_size=config.face_size,
                cancel=cancel,
                on_progress=lambda msg, p: prog(f"[3/6] {msg}", 0.16 + p * 0.34),
                per_clip_data=_per_clip_data or None,
            )
            result.main_video   = main_out
            result.render_stats = render_stats
            source = main_out
            export_keep.add(main_out)
            if config.color_grade.enabled or config.bokeh_intensity >= 0.05 or _has_audio_postprocess(config):
                export_intermediate.add(main_out)
            prog(f"[3/6] Timeline consolidada [{render_stats.encoder_used}].", 0.50)
        else:
            result.main_video = video_path
            prog("[3/6] Corte de silêncio desativado; mantendo timeline original.", 0.50)

        result.final_duration_s = get_video_duration(source)

        if _has_clip_source_replacements(clip_options):
            clip_source_out = os.path.join(output_dir, f"{base}_clip_sources.mp4")
            export_intermediate.add(clip_source_out)
            source = render_clip_source_pass(
                source,
                clip_source_out,
                clip_options,
                cancel=cancel,
                on_progress=lambda msg, p: prog(f"[4/6] {msg}", 0.50 + p * 0.10),
            )
            export_keep.add(source)
            result.main_video = source
            result.final_duration_s = get_video_duration(source)
            prog("[4/6] Mídias associadas aplicadas aos clipes.", 0.60)

        if config.bokeh_intensity < 0.05:
            prog("[EXPORT] Bokeh desativado; pulando segmentação.", 0.51)
            if config.generate_thumbnail:
                prog("[EXPORT] Observação: thumbnails usam segmentação própria quando ativadas.", 0.52)

        if config.color_grade.enabled or config.bokeh_intensity >= 0.05:
            effect_out = os.path.join(output_dir, f"{base}_effects.mp4")
            export_intermediate.add(effect_out)
            rendered = render_effects_pass(
                source,
                effect_out,
                color_grade=config.color_grade if config.color_grade.enabled else None,
                bokeh_intensity=config.bokeh_intensity,
                cancel=cancel,
                on_progress=lambda msg, p: prog(f"[4/6] {msg}", 0.50 + p * 0.20),
            )
            muxed_out = os.path.join(output_dir, f"{base}_effects_muxed.mp4")
            export_intermediate.add(muxed_out)
            with ProcessManager(cancel) as pm:
                pm.run_checked(
                    [
                        ffmpeg(), "-y",
                        "-i", rendered,
                        "-i", source,
                        "-map", "0:v:0",
                        "-map", "1:a:0?",
                        "-c:v", "copy",
                        "-c:a", "copy",
                        "-shortest",
                        muxed_out,
                    ],
                    context="mux efeitos",
                    timeout_s=max(120.0, result.final_duration_s * 6.0),
                )
            source = muxed_out
            export_keep.add(muxed_out)
            prog("[4/6] Passe de efeitos sincronizado com a timeline.", 0.70)
        else:
            prog("[4/6] Sem color grade/bokeh; usando caminho rápido sem passe frame a frame.", 0.70)

        if _has_clip_audio_adjustments(clip_options):
            with ProcessManager(cancel) as pm:
                clip_audio_out = os.path.join(output_dir, f"{base}_clip_audio.mp4")
                export_intermediate.add(clip_audio_out)
                audio_filter = _build_clip_volume_filter(clip_options)
                prog("[5/6] Aplicando volume por clipe...", 0.72)
                pm.run_checked(
                    [
                        ffmpeg(), "-y",
                        "-i", source,
                        "-c:v", "copy",
                        "-af", audio_filter,
                        "-c:a", "aac",
                        "-b:a", "192k",
                        clip_audio_out,
                    ],
                    context="volume por clipe",
                    timeout_s=max(60.0, result.final_duration_s * 5.0),
                )
                source = clip_audio_out
                export_keep.add(source)
                prog("[5/6] Volume por clipe aplicado.", 0.74)

        audio_filters = _build_audio_postprocess_filters(config)
        if audio_filters and not source_has_audio:
            prog("[5/6] Audio real ausente; pulando reducao/normalizacao.", 0.74)
            audio_filters = []
        if audio_filters or config.music_path:
            with ProcessManager(cancel) as pm:
                audio_out = os.path.join(output_dir, f"{base}_audio.mp4")
                export_intermediate.add(audio_out)
                if audio_filters:
                    prog(f"[5/6] Aplicando audio: {_audio_postprocess_label(config)}.", 0.74)
                    pm.run_checked(
                        [
                            ffmpeg(), "-y",
                            "-i", source,
                            "-c:v", "copy",
                            "-af", ",".join(audio_filters),
                            "-c:a", "aac",
                            "-b:a", "192k",
                            audio_out,
                        ],
                        context="audio",
                        timeout_s=max(60.0, result.final_duration_s * 5.0),
                    )
                    source = audio_out
                    export_keep.add(audio_out)
                    prog("[5/6] Tratamento de audio aplicado.", 0.82)

                if config.music_path and os.path.exists(config.music_path):
                    music_out = os.path.join(output_dir, f"{base}_master.mp4")
                    _mix_music(source, config.music_path, music_out, result.final_duration_s, pm=pm,
                               music_volume_pct=float(getattr(config, "music_volume_pct", 13.0)))
                    source = music_out
                    export_keep.add(music_out)
                prog("[5/6] Trilha final pronta.", 0.86)
        else:
            prog("[5/6] Áudio mantido sem pós-processamento.", 0.86)

        final_project_out = os.path.join(output_dir, f"{base}_editado.mp4")
        finalized, moved_source = _finalize_project_output(source, final_project_out, video_path)
        if moved_source:
            export_intermediate.add(source)
        source = finalized
        result.main_video = source
        export_keep = {source}
        _cleanup_intermediate_exports(export_intermediate - export_keep)
        result.final_duration_s = get_video_duration(source)
        prog(f"[EXPORT] Arquivo final pronto: {Path(source).name}", 0.87)

        # ── 4. Vertical version ─────────────────────────────────────────────
        if config.generate_vertical:
            preset_info = PRESETS[config.platform]
            vert_out = os.path.join(output_dir, f"{base}_vertical.mp4")
            prog("[6/6] Gerando versão vertical...", 0.88)
            convert_to_vertical(
                source, vert_out,
                target_width=preset_info.width,
                target_height=preset_info.height,
                crf=config.video_crf,
                preset=config.video_preset,
                cancel=cancel,
                on_progress=lambda msg, p: prog(f"[6/6] {msg}", 0.88 + p * 0.06),
            )
            result.vertical_video = vert_out

        # ── 5. Thumbnails — Professional engine v2 ──────────────────────────
        # Uses frame scoring → GrabCut segmentation → artistic background
        # → enhanced subject + glow → big bold typography.
        if config.generate_thumbnail:
            prog("[6/6] Selecionando frames e thumbnails...", 0.88)
            title    = config.thumbnail_title    or base.replace("_"," ").replace("-"," ").title()
            subtitle = config.thumbnail_subtitle or PRESETS[config.platform].label

            thumbs = generate_thumbnails_pro(
                source,
                output_dir=output_dir,
                base_name=base,
                title=title,
                subtitle=subtitle,
                count=config.thumbnail_count,
                on_progress=lambda msg: prog(f"[6/6] {msg}", 0.92),
            )
            result.thumbnails_all = thumbs
            result.thumbnail      = thumbs[0] if thumbs else None
            prog(f"[6/6] {len(thumbs)} thumbnails profissionais geradas.", 0.98)

        # ── 6. Stats ────────────────────────────────────────────────────────
        result.production_time_s = time.monotonic() - t_start

        def fmt(s: float) -> str:
            m, sec = divmod(int(s), 60)
            return f"{m:02d}:{sec:02d}"

        prog(
            f"[6/6] Concluído em {fmt(result.production_time_s)} | "
            f"Original: {fmt(result.original_duration_s)} -> "
            f"Final: {fmt(result.final_duration_s)} "
            f"(-{result.compression_pct:.1f}%)",
            1.0,
        )

    except CancelledError:
        result.cancelled = True
        result.error     = "Cancelado pelo usuário."
        result.production_time_s = time.monotonic() - t_start
        if on_progress:
            on_progress("[CANCEL] Export cancelado pelo usuário.", 0.0)

    except Exception as exc:
        result.error = str(exc)
        result.production_time_s = time.monotonic() - t_start
        if on_progress:
            on_progress(f"Erro: {exc}", -1.0)

    return result


def _cleanup_intermediate_exports(paths: set[str]) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def _finalize_project_output(
    source: str,
    final_project_out: str,
    original_video: str,
) -> tuple[str, bool]:
    if source == final_project_out:
        return final_project_out, False
    os.makedirs(os.path.dirname(final_project_out), exist_ok=True)
    if source == original_video:
        shutil.copy2(source, final_project_out)
        return final_project_out, False
    os.replace(source, final_project_out)
    return final_project_out, True


def _normalize_manual_segments(
    segments: list[tuple[float, float]],
    duration_s: float,
) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    duration_s = max(0.0, float(duration_s))
    for start, end in segments:
        start_s = max(0.0, min(duration_s, float(start)))
        end_s = max(0.0, min(duration_s, float(end)))
        if end_s > start_s:
            normalized.append((start_s, end_s))
    return normalized


def _export_output_plan(config: ProcessingConfig) -> str:
    outputs = ["vídeo final"]
    if config.generate_vertical:
        outputs.append("versão vertical")
    if config.generate_thumbnail:
        outputs.append(f"{max(1, int(config.thumbnail_count))} thumbnails")
    return ", ".join(outputs)


def _build_audio_postprocess_filters(config: ProcessingConfig) -> list[str]:
    filters: list[str] = []
    if config.noise_reduction:
        filters.append("afftdn=nf=-18")
    if getattr(config, "audio_voice_filter", False):
        filters.extend(["highpass=f=80", "lowpass=f=12000"])
    if getattr(config, "audio_compressor", False):
        filters.append("acompressor=threshold=-18dB:ratio=2.5:attack=8:release=120")
    if getattr(config, "audio_normalization", True):
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return filters


def _has_audio_postprocess(config: ProcessingConfig) -> bool:
    return bool(_build_audio_postprocess_filters(config) or config.music_path)


def _audio_postprocess_label(config: ProcessingConfig) -> str:
    parts: list[str] = []
    if config.noise_reduction:
        parts.append("reducao de ruido leve")
    if getattr(config, "audio_voice_filter", False):
        parts.append("filtro de voz")
    if getattr(config, "audio_compressor", False):
        parts.append("compressao leve")
    if getattr(config, "audio_normalization", True):
        parts.append("nivelamento loudnorm")
    return ", ".join(parts) or "sem filtros"


def _clip_option_plan(clip_options: list[dict[str, object]]) -> str:
    adjusted = 0
    text = 0
    audio = 0
    transitions = 0
    chroma = 0
    blur = 0
    overlays = 0
    for option in clip_options:
        try:
            scale_pct = float(option.get("scale_pct", 100.0))
            volume_pct = float(option.get("volume_pct", 100.0))
        except (TypeError, ValueError, AttributeError):
            continue
        transition = str(option.get("transition") or "Corte")
        text_overlay = str(option.get("text_overlay") or "").strip()
        chroma_enabled = bool(option.get("chroma_enabled", False))
        blur_type = str(option.get("blur_type") or "none").strip().lower()
        try:
            blur_intensity = float(option.get("blur_intensity", 0.0) or 0.0)
        except (TypeError, ValueError):
            blur_intensity = 0.0
        if _is_overlay_clip_option(option):
            overlays += 1
        if abs(scale_pct - 100.0) > 0.01:
            adjusted += 1
        if abs(volume_pct - 100.0) > 0.01:
            audio += 1
        if transition != "Corte":
            transitions += 1
        if text_overlay:
            text += 1
        if chroma_enabled:
            chroma += 1
        if blur_type not in ("", "none") and blur_intensity > 0.01:
            blur += 1
    parts: list[str] = []
    if adjusted:
        parts.append(f"escala em {adjusted} clipe(s)")
    if audio:
        parts.append(f"volume em {audio} clipe(s)")
    if transitions:
        parts.append(f"transição em {transitions} clipe(s)")
    if text:
        parts.append(f"texto em {text} clipe(s)")
    if chroma:
        parts.append(f"chroma em {chroma} clipe(s)")
    if blur:
        parts.append(f"desfoque em {blur} clipe(s)")
    if overlays:
        parts.append(f"{overlays} overlay(s) visual(is)")
    return ", ".join(parts)


def _clip_options_for_track_options(
    clip_options: list[dict[str, object]],
    track_options: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not clip_options:
        return []
    options = _normalized_track_options(track_options)
    visual_visible = options["visual_visible"]
    text_visible = options["text_visible"]
    audio_muted = options["audio_muted"]
    result: list[dict[str, object]] = []
    for option in clip_options:
        prepared = dict(option)
        if not visual_visible:
            prepared["source_path"] = ""
            prepared["scale_pct"] = 100.0
            prepared["position_x_pct"] = 0.0
            prepared["position_y_pct"] = 0.0
            prepared["chroma_enabled"] = False
        if not text_visible:
            prepared["text_overlay"] = ""
        if audio_muted:
            prepared["volume_pct"] = 0.0
        result.append(prepared)
    return result


def _normalized_track_options(track_options: dict[str, object] | None) -> dict[str, bool]:
    raw = track_options if isinstance(track_options, dict) else {}
    return {
        "visual_visible": raw.get("visual_visible", True) is not False,
        "text_visible": raw.get("text_visible", True) is not False,
        "audio_muted": bool(raw.get("audio_muted", False)),
    }


def _clip_options_with_output_ranges(
    clip_options: list[dict[str, object]],
    manual_segments: list[tuple[float, float]] | None,
) -> list[dict[str, object]]:
    if not clip_options:
        return []
    options: list[dict[str, object]] = []
    cursor = 0.0
    for idx, option in enumerate(clip_options):
        prepared = dict(option)
        start_s = float(prepared.get("start_s", 0.0) or 0.0)
        end_s = float(prepared.get("end_s", start_s) or start_s)
        if manual_segments and _is_overlay_clip_option(prepared):
            for output_start, output_end in _project_source_range_to_output_ranges(start_s, end_s, manual_segments):
                overlay_prepared = dict(prepared)
                overlay_prepared["output_start_s"] = output_start
                overlay_prepared["output_end_s"] = output_end
                options.append(overlay_prepared)
            continue
        if manual_segments and idx < len(manual_segments):
            seg_start, seg_end = manual_segments[idx]
            duration = max(0.0, float(seg_end) - float(seg_start))
            prepared["output_start_s"] = cursor
            prepared["output_end_s"] = cursor + duration
            cursor += duration
        else:
            prepared["output_start_s"] = start_s
            prepared["output_end_s"] = end_s
        options.append(prepared)
    return options


def _is_overlay_clip_option(option: dict[str, object]) -> bool:
    return str(option.get("layer") or "").strip().lower() == "overlay"


def _project_source_range_to_output_ranges(
    start_s: float,
    end_s: float,
    manual_segments: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for seg_start, seg_end in manual_segments:
        seg_start = float(seg_start)
        seg_end = float(seg_end)
        duration = max(0.0, seg_end - seg_start)
        overlap_start = max(start_s, seg_start)
        overlap_end = min(end_s, seg_end)
        if overlap_end > overlap_start:
            ranges.append((
                cursor + (overlap_start - seg_start),
                cursor + (overlap_end - seg_start),
            ))
        cursor += duration
    return ranges


def _has_clip_source_replacements(clip_options: list[dict[str, object]]) -> bool:
    for option in clip_options:
        start_s = float(option.get("output_start_s", option.get("start_s", 0.0)) or 0.0)
        end_s = float(option.get("output_end_s", option.get("end_s", 0.0)) or 0.0)
        if option.get("source_path") and end_s > start_s:
            return True
    return False


def _has_clip_audio_adjustments(clip_options: list[dict[str, object]]) -> bool:
    return bool(_clip_volume_adjustments(clip_options))


def _build_clip_volume_filter(clip_options: list[dict[str, object]]) -> str:
    adjustments = _clip_volume_adjustments(clip_options)
    if not adjustments:
        return "anull"
    filters = []
    for start_s, end_s, volume_pct in adjustments:
        volume = max(0.0, float(volume_pct) / 100.0)
        filters.append(f"volume={volume:.4f}:enable='between(t,{start_s:.3f},{end_s:.3f})'")
    return ",".join(filters)


def _build_per_clip_data(clip_options: list[dict[str, object]]) -> list[dict]:
    """Extract per-base-clip edits for cut_silence."""
    result: list[dict] = []
    for opt in clip_options:
        if str(opt.get("layer") or "base").strip().lower() == "overlay":
            continue
        try:
            sf = float(opt.get("speed_factor", 1.0) or 1.0)
        except (TypeError, ValueError):
            sf = 1.0
        tr = str(opt.get("transition") or "Corte").strip()
        try:
            td = float(opt.get("transition_duration_s", 0.4) or 0.4)
        except (TypeError, ValueError):
            td = 0.4
        try:
            blur_intensity = float(opt.get("blur_intensity", 0.0) or 0.0)
        except (TypeError, ValueError):
            blur_intensity = 0.0
        result.append(
            {
                "speed_factor": sf,
                "transition": tr,
                "transition_duration_s": td,
                "blur_type": str(opt.get("blur_type") or "none"),
                "blur_intensity": max(0.0, min(100.0, blur_intensity)),
                "blur_direction": str(opt.get("blur_direction") or "both"),
            }
        )
    return result


def _clip_volume_adjustments(clip_options: list[dict[str, object]]) -> list[tuple[float, float, float]]:
    adjustments: list[tuple[float, float, float]] = []
    for option in clip_options:
        try:
            volume_pct = float(option.get("volume_pct", 100.0))
            start_s = float(option.get("output_start_s", option.get("start_s", 0.0)) or 0.0)
            end_s = float(option.get("output_end_s", option.get("end_s", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if end_s > start_s and abs(volume_pct - 100.0) > 0.01:
            adjustments.append((start_s, end_s, volume_pct))
    return adjustments
