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
    # ── layer z-order (higher = in front; compositing + Trás/Frente) ──────────
    z_order: int = 0
    # ── Etapa C: audio pan / fades ─────────────────────────────────────────────
    pan_pct: float = 0.0        # L/R pan: -100 = full left, 0 = centre, +100 = full right
    fade_in_s: float = 0.0      # audio/video fade-in duration in seconds
    fade_out_s: float = 0.0     # audio/video fade-out duration in seconds
    # ── Etapa D: transform / blend ─────────────────────────────────────────────
    rotation_deg: float = 0.0   # clockwise rotation in degrees (-180..180)
    blend_mode: str = "Normal"  # Normal | Screen | Multiply | Overlay | Add | Darken | Lighten
    # ── Etapa E: per-clip crop (0..50 % from each edge) ────────────────────────
    crop_top_pct:    float = 0.0
    crop_bottom_pct: float = 0.0
    crop_left_pct:   float = 0.0
    crop_right_pct:  float = 0.0
    # ── Etapa F: per-clip color correction ─────────────────────────────────────
    brightness: float = 0.0   # -100..+100 additive; 0 = no change
    contrast:   float = 0.0   # -100..+100 multiplicative around 128; 0 = no change
    saturation: float = 0.0   # -100..+100; 0 = no change, -100 = greyscale


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
    # Multi-track support (Phase 2b) — extra parallel video and audio tracks
    # for layering content side-by-side at the same project time.
    extra_video_tracks: list[TimelineTrack] = field(default_factory=list)
    extra_audio_tracks: list[TimelineTrack] = field(default_factory=list)

    def all_overlay_tracks(self) -> list[TimelineTrack]:
        """Return the base overlay track plus any extra overlay tracks."""
        return [self.overlay_track] + list(self.extra_overlay_tracks)

    def all_video_tracks(self) -> list[TimelineTrack]:
        """Return the base video track plus any extra video tracks."""
        return [self.video_track] + list(self.extra_video_tracks)

    def all_audio_tracks(self) -> list[TimelineTrack]:
        """Return the base audio track plus any extra audio tracks."""
        return [self.audio_track] + list(self.extra_audio_tracks)

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

    def add_video_track(self, name: str = "") -> TimelineTrack:
        """Add a parallel video track. Use for side-by-side / PiP content."""
        n = name or f"Vídeo {len(self.extra_video_tracks) + 2}"
        track = TimelineTrack(name=n)
        self.extra_video_tracks.append(track)
        return track

    def remove_video_track(self, index: int) -> bool:
        """Remove extra video track at *index* (0-based). The main video_track cannot be removed."""
        if index < 0 or index >= len(self.extra_video_tracks):
            return False
        self.extra_video_tracks.pop(index)
        return True

    def add_audio_track(self, name: str = "") -> TimelineTrack:
        """Add a parallel audio track. Use for layered music / SFX over voice."""
        n = name or f"Áudio {len(self.extra_audio_tracks) + 2}"
        track = TimelineTrack(name=n)
        self.extra_audio_tracks.append(track)
        return track

    def remove_audio_track(self, index: int) -> bool:
        """Remove extra audio track at *index* (0-based). The main audio_track cannot be removed."""
        if index < 0 or index >= len(self.extra_audio_tracks):
            return False
        self.extra_audio_tracks.pop(index)
        return True


def build_timeline_model(
    duration_s: float,
    speech_segments: list[tuple[float, float]],
    waveform: WaveformData | None = None,
    source_path: str = "",
) -> TimelineModel:
    import os as _os
    video_track = TimelineTrack(name="Video")
    audio_track = TimelineTrack(name="Audio")

    base_label = _os.path.splitext(_os.path.basename(source_path))[0] if source_path else ""
    n = len(speech_segments)
    for idx, (start_s, end_s) in enumerate(speech_segments, start=1):
        if base_label:
            label = base_label if n == 1 else f"{base_label} ({idx})"
        else:
            label = f"Clip {idx}"
        video_track.clips.append(TimelineClip(start_s, end_s, "speech", label, source_path=source_path))
        audio_track.clips.append(TimelineClip(start_s, end_s, "speech", label, source_path=source_path))

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
