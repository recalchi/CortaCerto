from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Allow running as `python scripts/run_long_export_real_test.py` from repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.analyzer import AudioAnalysis
from src.core.editor import cut_silence, get_video_duration
from src.ffmpeg_env import ffmpeg, ffprobe


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        text=True,
    ).strip()
    return float(out)


def _generate_long_source(path: Path, duration_s: int, size: str, fps: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
        "-t", str(duration_s),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(path),
    ]
    subprocess.check_call(cmd)


def _progress_printer(prefix: str):
    def _cb(message: str, pct: float) -> None:
        print(f"[{prefix}] {pct*100:05.1f}% | {message}")
    return _cb


def _run_case(
    *,
    case_name: str,
    source: Path,
    output: Path,
    analysis: AudioAnalysis,
    per_clip_data: list[dict[str, Any]] | None,
    text_clips: list[dict[str, Any]] | None,
    image_clips: list[dict[str, Any]] | None,
    crf: int,
    preset: str,
) -> dict[str, Any]:
    t0 = time.monotonic()
    stats = cut_silence(
        video_path=str(source),
        analysis=analysis,
        output_path=str(output),
        on_progress=_progress_printer(case_name),
        per_clip_data=per_clip_data,
        text_clips=text_clips,
        image_clips=image_clips,
        crf=crf,
        preset=preset,
        normalize_audio=True,
        platform="youtube",
    )
    elapsed = time.monotonic() - t0
    if not output.exists():
        raise RuntimeError(f"{case_name}: arquivo de saída não foi criado")
    out_dur = _ffprobe_duration(output)
    return {
        "case": case_name,
        "elapsed_s": round(elapsed, 2),
        "input_duration_s": round(_ffprobe_duration(source), 2),
        "output_duration_s": round(out_dur, 2),
        "output_path": str(output),
        "output_size_mb": round(output.stat().st_size / (1024 * 1024), 2),
        "encoder_used": stats.encoder_used,
        "segments_total": stats.segments_total,
        "segments_transitioned": stats.segments_transitioned,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Teste real de export longo do CortaCerto")
    ap.add_argument("--duration", type=int, default=1200, help="Duração da mídia sintética em segundos (default: 1200 = 20min)")
    ap.add_argument("--size", default="854x480", help="Resolução da mídia sintética (default: 854x480)")
    ap.add_argument("--fps", type=int, default=24, help="FPS da mídia sintética (default: 24)")
    ap.add_argument("--segment-len", type=int, default=30, help="Tamanho dos segmentos do caso multi-segmentado em segundos")
    ap.add_argument("--crf", type=int, default=23, help="CRF de export (default: 23)")
    ap.add_argument("--preset", default="veryfast", help="Preset ffmpeg de export (default: veryfast)")
    ap.add_argument("--with-overlays", action="store_true", help="Incluir texto e imagem sobrepostos no caso multi-segmentado")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path("artifacts") / "long_export_tests" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    source = out_dir / f"synthetic_long_{args.duration}s.mp4"

    print(f"[SETUP] FFmpeg: {ffmpeg()}")
    print(f"[SETUP] Gerando fonte longa: {source}")
    _generate_long_source(source, args.duration, args.size, args.fps)
    input_duration = get_video_duration(str(source))
    print(f"[SETUP] Duração detectada: {input_duration:.2f}s")

    results: list[dict[str, Any]] = []

    # Caso A: export contínuo (um único segmento longo)
    full_analysis = AudioAnalysis(
        duration_s=input_duration,
        speech_segments=[(0.0, input_duration)],
        silence_ratio=0.0,
    )
    full_output = out_dir / "export_full_long.mp4"
    results.append(_run_case(
        case_name="full-long",
        source=source,
        output=full_output,
        analysis=full_analysis,
        per_clip_data=[{"speed_factor": 1.0, "transition": "Corte", "transition_duration_s": 0.2}],
        text_clips=None,
        image_clips=None,
        crf=args.crf,
        preset=args.preset,
    ))

    # Caso B: export multi-segmentado (estressa pipeline de segmentos/transições)
    seg_len = max(5, args.segment_len)
    segments: list[tuple[float, float]] = []
    per_clip: list[dict[str, Any]] = []
    t = 0.0
    idx = 0
    while t < input_duration - 0.01:
        end = min(input_duration, t + seg_len)
        segments.append((t, end))
        per_clip.append({
            "speed_factor": 1.0,
            "transition": "Fade" if idx > 0 and idx % 5 == 0 else "Corte",
            "transition_duration_s": 0.25,
        })
        idx += 1
        t = end
    seg_analysis = AudioAnalysis(
        duration_s=input_duration,
        speech_segments=segments,
        silence_ratio=0.0,
    )
    text_clips = None
    image_clips = None
    if args.with_overlays:
        overlay_img = out_dir / "overlay.png"
        subprocess.check_call([
            ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=yellow@0.9:s=320x96:d=1",
            "-frames:v", "1", str(overlay_img),
        ])
        text_clips = [{
            "text_overlay": "Teste longo CortaCerto",
            "start_s": 10.0,
            "end_s": min(input_duration, 60.0),
            "text_position_x_pct": 0.0,
            "text_position_y_pct": 88.0,
            "text_size_pct": 120.0,
            "text_color": "#ffffff",
            "text_bold": True,
        }]
        image_clips = [{
            "source_path": str(overlay_img),
            "start_s": 30.0,
            "end_s": min(input_duration, 90.0),
            "opacity_pct": 85.0,
            "scale_pct": 100.0,
            "rotation_deg": 0.0,
        }]
    seg_output = out_dir / "export_segmented_long.mp4"
    results.append(_run_case(
        case_name="segmented-long",
        source=source,
        output=seg_output,
        analysis=seg_analysis,
        per_clip_data=per_clip,
        text_clips=text_clips,
        image_clips=image_clips,
        crf=args.crf,
        preset=args.preset,
    ))

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": os.name,
        "input": {
            "path": str(source),
            "duration_s": round(input_duration, 2),
            "size": args.size,
            "fps": args.fps,
            "with_overlays": bool(args.with_overlays),
        },
        "cases": results,
    }
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Testes concluídos sem crash. Relatório: {report_path}")
    for item in results:
        print(
            f"[RESULT] {item['case']}: elapsed={item['elapsed_s']}s | "
            f"out_dur={item['output_duration_s']}s | size={item['output_size_mb']}MB | "
            f"segments={item['segments_total']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
