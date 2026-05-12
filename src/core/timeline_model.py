from __future__ import annotations

from dataclasses import dataclass, field

from .audio_waveform import WaveformData


@dataclass
class TimelineClip:
    start_s: float
    end_s: float
    clip_type: str
    label: str = ""
    source_path: str = ""
    scale_pct: float = 100.0
    volume_pct: float = 100.0
    transition: str = "Corte"
    text_overlay: str = ""
    text_position_x_pct: float = 0.0
    text_position_y_pct: float = 72.0
    text_size_pct: float = 100.0
    chroma_enabled: bool = False
    chroma_color: str = "#00ff00"
    chroma_tolerance: float = 45.0
    position_x_pct: float = 0.0
    position_y_pct: float = 0.0


@dataclass
class TimelineTrack:
    name: str
    clips: list[TimelineClip] = field(default_factory=list)


@dataclass
class TimelineModel:
    duration_s: float
    video_track: TimelineTrack
    audio_track: TimelineTrack
    removed_ranges: list[tuple[float, float]]
    waveform: list[float]
    saved_time_s: float
    text_track: TimelineTrack = field(default_factory=lambda: TimelineTrack(name="Texto"))


def build_timeline_model(
    duration_s: float,
    speech_segments: list[tuple[float, float]],
    waveform: WaveformData | None = None,
) -> TimelineModel:
    video_track = TimelineTrack(name="Video")
    audio_track = TimelineTrack(name="Audio")

    for idx, (start_s, end_s) in enumerate(speech_segments, start=1):
        label = f"Clip {idx}"
        video_track.clips.append(TimelineClip(start_s, end_s, "speech", label))
        audio_track.clips.append(TimelineClip(start_s, end_s, "speech", label))

    removed_ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for start_s, end_s in speech_segments:
        if start_s > cursor:
            removed_ranges.append((cursor, start_s))
        cursor = max(cursor, end_s)
    if cursor < duration_s:
        removed_ranges.append((cursor, duration_s))

    saved = sum(max(0.0, end_s - start_s) for start_s, end_s in removed_ranges)
    waveform_values = waveform.samples if waveform else []
    return TimelineModel(
        duration_s=duration_s,
        video_track=video_track,
        audio_track=audio_track,
        removed_ranges=removed_ranges,
        waveform=waveform_values,
        saved_time_s=saved,
        text_track=TimelineTrack(name="Texto"),
    )
