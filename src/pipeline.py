"""
Pipeline orchestrator — ties together all processing steps.
Passes a threading.Event (cancel) through to ffmpeg calls so the
cancel button actually kills running processes immediately.
"""
from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import ProcessingConfig, PRESETS
from .core.analyzer import analyze_video, AudioAnalysis
from .core.editor import (
    cut_silence, convert_to_vertical,
    get_video_duration, RenderStats, CancelledError,
)
from .core.thumbnail import (
    generate_thumbnail, generate_multi_thumbnails,
    detect_person_from_video,
)
from .core.thumbnail_pro import generate_thumbnails_pro


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

        # ── 1b. Face/person detection (used for bokeh + thumbnail layout) ───
        if config.bokeh_intensity >= 0.05 or config.generate_thumbnail:
            prog("Detectando pessoa na cena…", 0.02)
            fx, fy, fs = detect_person_from_video(video_path, at_second=5.0)
            config.face_x    = fx
            config.face_y    = fy
            config.face_size = fs
            prog(f"Pessoa detectada em x={fx:.2f} y={fy:.2f} (tamanho {fs:.2f})", 0.03)

        # ── 2. Audio analysis ───────────────────────────────────────────────
        prog("Analisando áudio…", 0.04)
        analysis = analyze_video(
            video_path,
            silence_threshold_db=config.silence_threshold_db,
            min_silence_ms=config.min_silence_ms,
            audio_padding_ms=config.audio_padding_ms,
            min_segment_s=config.min_segment_s,
            on_progress=lambda msg: prog(msg, 0.08),
        )
        result.analysis = analysis
        pct = analysis.silence_ratio * 100
        prog(f"Análise: {pct:.1f}% silêncio — {len(analysis.speech_segments)} segmentos.", 0.14)

        # ── 3. Cut silence + effects ────────────────────────────────────────
        source = video_path
        if config.remove_silence:
            main_out = os.path.join(output_dir, f"{base}_editado.mp4")
            render_stats = cut_silence(
                video_path,
                analysis,
                main_out,
                crf=config.video_crf,
                preset=config.video_preset,
                color_grade=config.color_grade if config.color_grade.enabled else None,
                music_path=config.music_path,
                noise_reduction=config.noise_reduction,
                bokeh_intensity=config.bokeh_intensity,
                face_x=config.face_x,
                face_y=config.face_y,
                face_size=config.face_size,
                cancel=cancel,
                on_progress=lambda msg, p: prog(msg, 0.14 + p * 0.55),
            )
            result.main_video   = main_out
            result.render_stats = render_stats
            source = main_out
            prog(f"Vídeo editado  [{render_stats.encoder_used}].", 0.70)
        else:
            result.main_video = video_path
            prog("Corte desativado.", 0.70)

        result.final_duration_s = get_video_duration(source)

        # ── 4. Vertical version ─────────────────────────────────────────────
        if config.generate_vertical:
            preset_info = PRESETS[config.platform]
            vert_out = os.path.join(output_dir, f"{base}_vertical.mp4")
            prog("Gerando versão vertical…", 0.72)
            convert_to_vertical(
                source, vert_out,
                target_width=preset_info.width,
                target_height=preset_info.height,
                crf=config.video_crf,
                preset=config.video_preset,
                cancel=cancel,
                on_progress=lambda msg, p: prog(msg, 0.72 + p * 0.12),
            )
            result.vertical_video = vert_out

        # ── 5. Thumbnails — Professional engine v2 ──────────────────────────
        # Uses frame scoring → GrabCut segmentation → artistic background
        # → enhanced subject + glow → big bold typography.
        if config.generate_thumbnail:
            prog("Selecionando frames + recorte + composição…", 0.86)
            title    = config.thumbnail_title    or base.replace("_"," ").replace("-"," ").title()
            subtitle = config.thumbnail_subtitle or PRESETS[config.platform].label

            thumbs = generate_thumbnails_pro(
                source,
                output_dir=output_dir,
                base_name=base,
                title=title,
                subtitle=subtitle,
                count=config.thumbnail_count,
                on_progress=lambda msg: prog(msg, 0.90),
            )
            result.thumbnails_all = thumbs
            result.thumbnail      = thumbs[0] if thumbs else None
            prog(f"{len(thumbs)} thumbnails profissionais geradas.", 0.96)

        # ── 6. Stats ────────────────────────────────────────────────────────
        result.production_time_s = time.monotonic() - t_start

        def fmt(s: float) -> str:
            m, sec = divmod(int(s), 60)
            return f"{m:02d}:{sec:02d}"

        prog(
            f"Concluido em {fmt(result.production_time_s)} | "
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
            on_progress("Cancelado.", 0.0)

    except Exception as exc:
        result.error = str(exc)
        result.production_time_s = time.monotonic() - t_start
        if on_progress:
            on_progress(f"Erro: {exc}", -1.0)

    return result
