from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

from ..ffmpeg_env import ffmpeg


@dataclass
class WaveformData:
    duration_s: float
    samples: list[float]
    sample_rate: int


def extract_waveform(
    video_path: str,
    duration_s: float,
    bins: int = 320,
    sample_rate: int = 16000,
) -> WaveformData:
    if duration_s <= 0 or bins <= 0:
        return WaveformData(duration_s=duration_s, samples=[], sample_rate=sample_rate)

    cmd = [
        ffmpeg(),
        "-v", "error",
        "-i", video_path,
        "-map", "0:a:0?",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return WaveformData(duration_s=duration_s, samples=[], sample_rate=sample_rate)

    pcm = np.frombuffer(result.stdout, dtype=np.int16)
    if pcm.size == 0:
        return WaveformData(duration_s=duration_s, samples=[], sample_rate=sample_rate)

    samples_per_bin = max(1, pcm.size // bins)
    peaks: list[float] = []
    for start in range(0, pcm.size, samples_per_bin):
        chunk = pcm[start:start + samples_per_bin]
        if chunk.size == 0:
            continue
        peaks.append(float(np.abs(chunk).max()) / 32768.0)

    if not peaks:
        return WaveformData(duration_s=duration_s, samples=[], sample_rate=sample_rate)

    max_peak = max(peaks) or 1.0
    normalized = [min(1.0, peak / max_peak) for peak in peaks]
    return WaveformData(duration_s=duration_s, samples=normalized, sample_rate=sample_rate)
