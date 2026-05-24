"""
Audio analysis: silence detection via ffmpeg silencedetect filter.
No pydub/audioop dependency - works on any Python version.
"""
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..ffmpeg_env import ffmpeg, ffprobe


@dataclass
class AudioAnalysis:
    duration_s: float
    speech_segments: List[Tuple[float, float]]  # (start_s, end_s)
    silence_ratio: float


def analyze_video(
    video_path: str,
    silence_threshold_db: float = -40.0,
    min_silence_ms: int = 700,
    audio_padding_ms: int = 150,
    min_segment_s: float = 0.3,
    on_progress: Optional[Callable[[str], None]] = None,
) -> AudioAnalysis:
    if on_progress:
        on_progress("Detectando silêncios com ffmpeg...")

    duration_s = _get_duration(video_path)
    silence_periods = _run_silencedetect(video_path, silence_threshold_db, min_silence_ms / 1000.0)
    return _build_analysis(silence_periods, duration_s, audio_padding_ms / 1000.0, min_segment_s)


def _get_duration(video_path: str) -> float:
    result = subprocess.run(
        [ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError("Não foi possível obter a duração do vídeo; verifique se o arquivo é válido.")


def _run_silencedetect(video_path: str, noise_db: float, min_silence_s: float) -> List[Tuple[float, float]]:
    """
    Run ffmpeg silencedetect and return list of (silence_start, silence_end) in seconds.
    ffmpeg writes silencedetect output to stderr.

    Speed optimisations applied:
      -vn          skip video decoding entirely (only audio is needed)
      -ac 1        downmix to mono before analysis
      -ar 8000     resample to 8 kHz — plenty for silence detection, much faster
    """
    result = subprocess.run(
        [
            ffmpeg(), "-i", video_path,
            "-vn",                   # no video decode
            "-ac", "1",              # mono
            "-ar", "8000",           # 8 kHz (silence detection doesn't need hi-fi)
            "-af", f"silencedetect=noise={noise_db:.1f}dB:duration={min_silence_s:.3f}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=300,                 # 5 min hard cap — prevents infinite freeze
    )

    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", result.stderr)]
    ends   = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", result.stderr)]

    # Pair up starts and ends; a trailing unmatched start means silence until EOF
    periods: List[Tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else float("inf")
        periods.append((start, end))
    return periods


def _build_analysis(
    silence_periods: List[Tuple[float, float]],
    duration_s: float,
    padding_s: float,
    min_segment_s: float,
) -> AudioAnalysis:
    # Invert silence -> speech
    speech: List[List[float]] = []
    cursor = 0.0
    for sil_start, sil_end in sorted(silence_periods):
        seg_end = min(sil_start, duration_s)
        if seg_end > cursor:
            speech.append([cursor, seg_end])
        cursor = min(sil_end, duration_s)
    if cursor < duration_s:
        speech.append([cursor, duration_s])

    # Apply padding (expand each segment inward so we don't clip speech edges)
    padded: List[List[float]] = []
    for start, end in speech:
        s = max(0.0, start - padding_s)
        e = min(duration_s, end + padding_s)
        if (e - s) >= min_segment_s:
            padded.append([s, e])

    # Merge overlaps created by padding
    merged: List[List[float]] = []
    for seg in sorted(padded):
        if merged and seg[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], seg[1])
        else:
            merged.append(seg)

    speech_time = sum(e - s for s, e in merged)
    silence_ratio = 1.0 - (speech_time / duration_s) if duration_s > 0 else 0.0

    return AudioAnalysis(
        duration_s=duration_s,
        speech_segments=[(s, e) for s, e in merged],
        silence_ratio=silence_ratio,
    )
