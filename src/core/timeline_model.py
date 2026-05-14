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
    transition_duration_s: float = 0.4
    # ── legacy / basic text ────────────────────────────────────────────────────
    text_overlay: str = ""
    text_position_x_pct: float = 0.0
    text_position_y_pct: float = 72.0
    text_size_pct: float = 100.0
    text_color: str = "#ffffff"
    text_background_enabled: bool = True
    text_background_color: str = "#000000"
    # ── extended text style ────────────────────────────────────────────────────
    text_font: str = "default"
    text_bold: bool = False
    text_italic: bool = False
    text_align: str = "center"
    text_background_alpha: float = 0.65
    text_bg_rounded: bool = True
    text_shadow_enabled: bool = False
    text_shadow_color: str = "#000000"
    text_shadow_offset_x: int = 2
    text_shadow_offset_y: int = 2
    text_shadow_blur: int = 4
    text_stroke_enabled: bool = False
    text_stroke_color: str = "#000000"
    text_stroke_width: int = 2
    text_max_width_pct: float = 80.0
    text_line_spacing: float = 1.2
    # ── chroma key ─────────────────────────────────────────────────────────────
    chroma_enabled: bool = False
    chroma_color: str = "#00ff00"
    chroma_tolerance: float = 45.0
    # ── overlay position / opacity ─────────────────────────────────────────────
    position_x_pct: float = 0.0
    position_y_pct: float = 0.0
    opacity_pct: float = 100.0
    # ── speed ──────────────────────────────────────────────────────────────────
    speed_factor: float = 1.0


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
    overlay_track: TimelineTrack = field(default_factory=lambda: TimelineTrack(name="Overlay"))
    extra_overlay_tracks: list[TimelineTrack] = field(default_factory=list)

    def all_overlay_tracks(self) -> list[TimelineTrack]:
        """Return the base overlay track plus any extra overlay tracks."""
        return [self.overlay_track] + list(self.extra_overlay_tracks)

    def add_overlay_track(self, name: str = "") -> TimelineTrack:
        """Add a new overlay track and return it."""
        n = name or f"Overlay {len(self.extra_overlay_tracks) + 2}"
        track = TimelineTrack(name=n)
        self.extra_overlay_tracks.append(track)
        return track

    def remove_overlay_track(self, index: int) -> bool:
        """Remove extra overlay track at *index* (0-based into extra list). Returns False if out of range."""
        if index < 0 or index >= len(self.extra_overlay_tracks):
            return False
        self.extra_overlay_tracks.pop(index)
        return True


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
        overlay_track=TimelineTrack(name="Overlay"),
    )
