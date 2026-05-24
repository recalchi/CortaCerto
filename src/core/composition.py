"""
Composition — multi-track data model for CortaCerto.

Track types:
  "video"  — clips of any visual content (video, image, text, color, effect)
  "audio"  — clips of audio content (music, voice, SFX)

All visual content (effects, images, text overlays) lives in video tracks
(either in the main track or as an additional video layer).
All audio content lives in audio tracks.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Keyframe
# ---------------------------------------------------------------------------

@dataclass
class Keyframe:
    """One keyframe point for a clip property."""
    time_s: float
    value: float
    interp: str = "linear"       # "linear" | "bezier" | "hold"
    ease_out: float = 0.33       # bezier tangent out (0-1)
    ease_in: float  = 0.33       # bezier tangent in (0-1)


def eval_keyframes(keyframes: list[Keyframe], t: float) -> float:
    """Evaluate the value of a keyframe track at time *t*."""
    if not keyframes:
        return 0.0
    kfs = sorted(keyframes, key=lambda k: k.time_s)
    if t <= kfs[0].time_s:
        return kfs[0].value
    if t >= kfs[-1].time_s:
        return kfs[-1].value
    for i in range(len(kfs) - 1):
        k0, k1 = kfs[i], kfs[i + 1]
        if k0.time_s <= t <= k1.time_s:
            span = k1.time_s - k0.time_s
            alpha = (t - k0.time_s) / max(1e-9, span)
            if k0.interp == "hold":
                return k0.value
            if k0.interp == "bezier":
                alpha = _cubic_bezier_ease(alpha, k0.ease_out, k1.ease_in)
            return k0.value + (k1.value - k0.value) * alpha
    return kfs[-1].value


def _cubic_bezier_ease(t: float, p1: float, p2: float) -> float:
    """Approximate cubic-bezier ease with binary search (CSS timing model)."""
    lo, hi = 0.0, 1.0
    for _ in range(8):
        mid = (lo + hi) / 2.0
        x = 3 * mid * (1 - mid) ** 2 * p1 + 3 * mid ** 2 * (1 - mid) * p2 + mid ** 3
        if x < t:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Clip
# ---------------------------------------------------------------------------

@dataclass
class Clip:
    """A single clip on a track.

    clip_type:
      "video"   — segment from a video source file
      "audio"   — segment from an audio source file
      "image"   — static image
      "text"    — text overlay (no source file)
      "color"   — solid colour plate
      "effect"  — generative or filter-only element
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    clip_type: str = "video"

    # ── source ────────────────────────────────────────────────────────────────
    source_path: str = ""

    # ── timeline placement ────────────────────────────────────────────────────
    start_s: float = 0.0      # position on the timeline
    end_s:   float = 0.0      # position on the timeline

    # ── source trim (in/out points) ───────────────────────────────────────────
    in_point_s:  float = 0.0  # start reading from source at this time
    out_point_s: float = 0.0  # stop reading from source at this time (0 = same as end-start)

    label: str = ""

    # ── transform ─────────────────────────────────────────────────────────────
    opacity_pct:    float = 100.0
    position_x_pct: float = 0.0
    position_y_pct: float = 0.0
    scale_pct:      float = 100.0
    rotation_deg:   float = 0.0

    # ── audio ─────────────────────────────────────────────────────────────────
    volume_pct: float = 100.0

    # ── playback ──────────────────────────────────────────────────────────────
    speed_factor: float = 1.0

    # ── text content (clip_type == "text") ────────────────────────────────────
    text_content: str = ""
    text_style: dict = field(default_factory=dict)   # see _default_text_style()

    # ── chroma key ────────────────────────────────────────────────────────────
    chroma_enabled: bool   = False
    chroma_color:   str    = "#00ff00"
    chroma_tolerance: float = 45.0

    # ── transition INTO this clip ─────────────────────────────────────────────
    transition_in:   str   = "cut"   # "cut"|"dissolve"|"fade"|"wipe"|"slide"
    transition_in_s: float = 0.4

    # ── keyframes: property_name → sorted list[Keyframe] ─────────────────────
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)

    # ── color-grade override per clip ─────────────────────────────────────────
    color_grade: dict = field(default_factory=dict)  # same schema as ColorGrade attrs

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def eval_prop(self, prop: str, t: float, default: float = 0.0) -> float:
        """Evaluate a keyframed property at timeline time *t*."""
        kfs = self.keyframes.get(prop)
        if not kfs:
            return getattr(self, prop, default)
        return eval_keyframes(kfs, t)


def _default_text_style() -> dict:
    return {
        "font": "default", "size_pct": 100, "color": "#ffffff",
        "bold": False, "italic": False, "align": "center",
        "background_enabled": True, "background_color": "#000000",
        "background_alpha": 0.65, "bg_rounded": True,
        "shadow_enabled": False, "shadow_color": "#000000",
        "shadow_offset_x": 2, "shadow_offset_y": 2, "shadow_blur": 4,
        "stroke_enabled": False, "stroke_color": "#000000", "stroke_width": 2,
        "max_width_pct": 80.0, "line_spacing": 1.2,
        "position_x_pct": 0.0, "position_y_pct": 72.0,
    }


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------

# Default lane height in px per track type
_TRACK_HEIGHT: dict[str, int] = {"video": 36, "audio": 32}


@dataclass
class Track:
    """A timeline track that holds clips.

    track_type: "video" | "audio"
    index: ordering within the type group.
        For video: index=0 is the *base* layer (bottom of compositor stack).
                   Higher index = rendered on top.
        For audio: index=0 is the primary mix bus.
    """
    id:         str
    track_type: str        # "video" | "audio"
    name:       str  = ""
    index:      int  = 0

    clips:   list[Clip] = field(default_factory=list)

    locked:  bool = False
    muted:   bool = False
    solo:    bool = False
    visible: bool = True

    height_px: int = 0     # 0 = use default for type
    color:     str = ""    # track accent colour (empty → type default)

    @property
    def effective_height(self) -> int:
        return self.height_px if self.height_px > 0 else _TRACK_HEIGHT.get(self.track_type, 32)

    def clips_at(self, t: float) -> list[Clip]:
        """All clips whose range contains time *t*."""
        return [c for c in self.clips if c.start_s <= t < c.end_s]

    def sorted_clips(self) -> list[Clip]:
        return sorted(self.clips, key=lambda c: c.start_s)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

@dataclass
class Composition:
    """Top-level container for all tracks and composition metadata."""

    id:     str  = field(default_factory=lambda: str(uuid.uuid4()))
    name:   str  = "Sem nome"

    duration_s: float = 0.0
    fps:        float = 30.0
    width:      int   = 1920
    height:     int   = 1080

    tracks:   list[Track] = field(default_factory=list)
    waveform: list[float] = field(default_factory=list)

    schema_version: int = 2

    # ── track accessors ───────────────────────────────────────────────────────

    def video_tracks(self) -> list[Track]:
        """Video tracks sorted by index ascending (index=0 is base layer)."""
        return sorted(
            [t for t in self.tracks if t.track_type == "video"],
            key=lambda t: t.index,
        )

    def audio_tracks(self) -> list[Track]:
        """Audio tracks sorted by index ascending."""
        return sorted(
            [t for t in self.tracks if t.track_type == "audio"],
            key=lambda t: t.index,
        )

    def track_by_id(self, track_id: str) -> Track | None:
        return next((t for t in self.tracks if t.id == track_id), None)

    # ── track mutation ────────────────────────────────────────────────────────

    def add_video_track(self, name: str = "", index: int = -1) -> Track:
        if index < 0:
            vt = self.video_tracks()
            index = (max(t.index for t in vt) + 1) if vt else 0
        tid = f"V{index + 1}"
        # Avoid id collision
        while any(t.id == tid for t in self.tracks):
            tid += "_"
        track = Track(
            id=tid, track_type="video",
            name=name or f"Vídeo {index + 1}", index=index,
        )
        self.tracks.append(track)
        return track

    def add_audio_track(self, name: str = "", index: int = -1) -> Track:
        if index < 0:
            at = self.audio_tracks()
            index = (max(t.index for t in at) + 1) if at else 0
        tid = f"A{index + 1}"
        while any(t.id == tid for t in self.tracks):
            tid += "_"
        track = Track(
            id=tid, track_type="audio",
            name=name or f"Áudio {index + 1}", index=index,
        )
        self.tracks.append(track)
        return track

    def remove_track(self, track_id: str) -> bool:
        before = len(self.tracks)
        self.tracks = [t for t in self.tracks if t.id != track_id]
        return len(self.tracks) < before

    def move_track(self, track_id: str, new_index: int) -> bool:
        track = self.track_by_id(track_id)
        if track is None:
            return False
        siblings = [t for t in self.tracks if t.track_type == track.track_type and t.id != track_id]
        siblings.sort(key=lambda t: t.index)
        # Re-index
        track.index = new_index
        for i, s in enumerate(siblings):
            if i >= new_index:
                s.index = i + 1
            else:
                s.index = i
        return True

    # ── clip accessors ────────────────────────────────────────────────────────

    def all_clips(self) -> list[tuple[Track, Clip]]:
        result: list[tuple[Track, Clip]] = []
        for track in self.tracks:
            for clip in track.clips:
                result.append((track, clip))
        return result

    def find_clip(self, clip_id: str) -> tuple[Track, Clip] | None:
        for track, clip in self.all_clips():
            if clip.id == clip_id:
                return track, clip
        return None

    # ── lane layout (for timeline canvas drawing) ─────────────────────────────

    def lane_layout(self, canvas_height: int, top_margin: int = 8) -> list[dict]:
        """Return a list of lane descriptors for every track, top-to-bottom.

        Each entry:
            {
              "track": Track,
              "y1": int,
              "y2": int,
            }

        Video tracks: drawn top-to-bottom with *highest index first* so that
        the frontmost visual layer appears at the top of the canvas.
        Audio tracks: drawn below all video tracks.
        """
        video = list(reversed(self.video_tracks()))   # highest index at top
        audio = self.audio_tracks()

        lanes: list[dict] = []
        y = top_margin
        for track in video + audio:
            h = track.effective_height
            lanes.append({"track": track, "y1": y, "y2": y + h})
            y += h + 4  # 4px gap between lanes
        return lanes

    def required_canvas_height(self, top_margin: int = 8, min_h: int = 190) -> int:
        lanes = self.lane_layout(0, top_margin)
        if not lanes:
            return min_h
        bottom = lanes[-1]["y2"] + top_margin
        return max(min_h, bottom)
