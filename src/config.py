from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .core.color_grade import ColorGrade, PRESET_CAPCUT


class Platform(Enum):
    YOUTUBE = "youtube"
    REELS   = "reels"
    TIKTOK  = "tiktok"
    SHORTS  = "shorts"


class SilenceStyle(Enum):
    AGGRESSIVE = "aggressive"   # 600 ms  — tight cuts
    NATURAL    = "natural"      # 900 ms  — keeps inter-sentence pauses
    LIGHT      = "light"        # 1400 ms — only long silences


_SILENCE_MS: dict[SilenceStyle, int] = {
    SilenceStyle.AGGRESSIVE: 600,
    SilenceStyle.NATURAL:    900,
    SilenceStyle.LIGHT:      1400,
}


@dataclass
class PlatformPreset:
    label: str
    width: int
    height: int
    fps: int
    max_duration_s: Optional[int]


PRESETS: dict[Platform, PlatformPreset] = {
    Platform.YOUTUBE: PlatformPreset("YouTube (16:9)",          1920, 1080, 30, None),
    Platform.REELS:   PlatformPreset("Instagram Reels (9:16)",  1080, 1920, 30, 90),
    Platform.TIKTOK:  PlatformPreset("TikTok (9:16)",           1080, 1920, 30, 180),
    Platform.SHORTS:  PlatformPreset("YouTube Shorts (9:16)",   1080, 1920, 60, 60),
}


@dataclass
class ProcessingConfig:
    # Silence detection
    silence_threshold_db: float = -40.0
    silence_style:        SilenceStyle = SilenceStyle.NATURAL
    audio_padding_ms:     int = 150
    min_segment_s:        float = 0.3

    # Output targets
    platform:           Platform = Platform.YOUTUBE
    remove_silence:     bool = True
    generate_thumbnail: bool = False
    generate_vertical:  bool = False
    manual_segments:    list[tuple[float, float]] | None = None
    clip_options:       list[dict[str, object]] = field(default_factory=list)
    track_options:      dict[str, object] = field(default_factory=dict)

    # Render quality (used for CPU fallback; GPU auto-selects optimal settings)
    video_crf:    int = 18
    video_preset: str = "fast"

    # Color grading
    color_grade: ColorGrade = field(default_factory=lambda: ColorGrade(**vars(PRESET_CAPCUT)))

    # Effects
    apply_zoom_effects: bool = True
    apply_transitions:  bool = True

    # Audio enhancement
    noise_reduction: bool = False
    audio_normalization: bool = True
    audio_voice_filter: bool = False
    audio_compressor: bool = False

    # Background blur / depth-of-field (0.0 = off, 0.3 = subtle, 1.0 = heavy)
    bokeh_intensity: float = 0.0

    # Detected person position (set automatically by pipeline before render)
    face_x:    float = 0.50
    face_y:    float = 0.38
    face_size: float = 0.22

    # Thumbnail
    thumbnail_title:    str = ""
    thumbnail_subtitle: str = ""
    thumbnail_theme:    str = "dark"
    thumbnail_count:    int = 5    # how many variant thumbs to generate

    # Music
    music_path: Optional[str] = None

    @property
    def min_silence_ms(self) -> int:
        return _SILENCE_MS[self.silence_style]
