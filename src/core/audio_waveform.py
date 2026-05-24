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

    # Compute the minimum sample rate needed to fill `bins` with ~4 samples each.
    # For a 1-hour video this drops from 16 000 Hz → ~6 Hz, shrinking the PCM buffer
    # from ~115 MB to a few KB and removing the "freezing on long videos" issue.
    target_sr = max(50, int(bins * 4 / max(duration_s, 1)) + 1)

    cmd = [
        ffmpeg(),
        "-v", "error",
        "-i", video_path,
        "-vn",                        # skip video decode
        "-map", "0:a:0?",
        "-ac", "1",
        "-ar", str(target_sr),        # just enough samples for the waveform bins
        "-f", "s16le",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0 or not result.stdout:
        return WaveformData(duration_s=duration_s, samples=[], sample_rate=target_sr)

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
        return WaveformData(duration_s=duration_s, samples=[], sample_rate=target_sr)

    max_peak = max(peaks) or 1.0
    normalized = [min(1.0, peak / max_peak) for peak in peaks]
    return WaveformData(duration_s=duration_s, samples=normalized, sample_rate=target_sr)
