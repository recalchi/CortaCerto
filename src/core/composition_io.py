"""
Serialization and migration for Composition.

Supports:
  composition_from_timeline_model()  — migrate legacy TimelineModel → Composition
  composition_to_timeline_model()    — backward compat for pipeline (Composition → TimelineModel)
  composition_to_dict()              — JSON-serialisable dict
  composition_from_dict()            — deserialise, handling schema migrations
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .composition import (
    Clip, Composition, Keyframe, Track,
    _default_text_style,
)
from .timeline_model import TimelineClip, TimelineModel, TimelineTrack


# ---------------------------------------------------------------------------
# Migration: TimelineModel → Composition
# ---------------------------------------------------------------------------

def composition_from_timeline_model(
    tm: TimelineModel,
    name: str = "Projeto",
    fps: float = 30.0,
    width: int = 1920,
    height: int = 1080,
) -> Composition:
    """Convert a legacy TimelineModel into the new Composition format."""
    comp = Composition(
        id=str(uuid.uuid4()),
        name=name,
        duration_s=float(tm.duration_s),
        fps=fps, width=width, height=height,
        waveform=list(tm.waveform or []),
    )

    # ── V1: base video track ──────────────────────────────────────────────────
    v1 = comp.add_video_track("Vídeo 1", index=0)
    for tc in tm.video_track.clips:
        v1.clips.append(_tc_to_clip(tc, "video"))

    # ── V2: text overlay track ────────────────────────────────────────────────
    text_clips = getattr(tm, "text_track", None)
    if text_clips and text_clips.clips:
        v2 = comp.add_video_track("Texto", index=1)
        for tc in text_clips.clips:
            cl = _tc_to_clip(tc, "text")
            cl.text_content = str(tc.text_overlay or tc.label or "")
            cl.text_style = _build_text_style(tc)
            v2.clips.append(cl)

    # ── V3+: overlay tracks ───────────────────────────────────────────────────
    all_overlays: list[TimelineTrack] = []
    base_ov = getattr(tm, "overlay_track", None)
    if base_ov:
        all_overlays.append(base_ov)
    extra = getattr(tm, "extra_overlay_tracks", [])
    all_overlays.extend(extra)

    for oi, ov_track in enumerate(all_overlays):
        vtrack = comp.add_video_track(ov_track.name or f"Camada {oi + 2}", index=2 + oi)
        for tc in ov_track.clips:
            ctype = "image" if _is_image_path(tc.source_path) else "video"
            vtrack.clips.append(_tc_to_clip(tc, ctype))

    # ── A1: main audio ────────────────────────────────────────────────────────
    a1 = comp.add_audio_track("Áudio 1", index=0)
    for tc in tm.audio_track.clips:
        a1.clips.append(_tc_to_clip(tc, "audio"))

    return comp


# ---------------------------------------------------------------------------
# Back-compat: Composition → TimelineModel (for existing pipeline)
# ---------------------------------------------------------------------------

def composition_to_timeline_model(comp: Composition) -> TimelineModel:
    """Produce a legacy TimelineModel from a Composition for pipeline use."""
    vts = comp.video_tracks()
    ats = comp.audio_tracks()

    main_video = TimelineTrack(name="Video")
    main_audio = TimelineTrack(name="Audio")
    text_track  = TimelineTrack(name="Texto")
    overlay_track = TimelineTrack(name="Overlay")
    extra_overlays: list[TimelineTrack] = []

    # V index=0 → main video
    if vts:
        main_video.clips = [_clip_to_tc(c) for c in vts[0].sorted_clips()]

    # Find text track (by name or index=1 with type text clips)
    text_vt = next(
        (t for t in vts if t.name == "Texto" or
         all(c.clip_type == "text" for c in t.clips if t.clips)),
        None,
    )
    # Overlay tracks: all video tracks except index=0 and text track
    overlay_vts = [t for t in vts if t.index != 0 and t is not text_vt]

    if text_vt:
        text_track.clips = [_clip_to_tc(c) for c in text_vt.sorted_clips()]

    if overlay_vts:
        overlay_track.clips = [_clip_to_tc(c) for c in overlay_vts[0].sorted_clips()]
        for ov in overlay_vts[1:]:
            tt = TimelineTrack(name=ov.name)
            tt.clips = [_clip_to_tc(c) for c in ov.sorted_clips()]
            extra_overlays.append(tt)

    # A index=0 → main audio
    if ats:
        main_audio.clips = [_clip_to_tc(c) for c in ats[0].sorted_clips()]

    return TimelineModel(
        duration_s=comp.duration_s,
        video_track=main_video,
        audio_track=main_audio,
        removed_ranges=[],
        waveform=list(comp.waveform),
        saved_time_s=0.0,
        text_track=text_track,
        overlay_track=overlay_track,
        extra_overlay_tracks=extra_overlays,
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def composition_to_dict(comp: Composition) -> dict[str, Any]:
    """Convert to a JSON-serialisable dict."""
    return {
        "schema_version": comp.schema_version,
        "id": comp.id,
        "name": comp.name,
        "duration_s": comp.duration_s,
        "fps": comp.fps,
        "width": comp.width,
        "height": comp.height,
        "waveform": comp.waveform,
        "tracks": [_track_to_dict(t) for t in comp.tracks],
    }


def composition_from_dict(data: dict[str, Any]) -> Composition:
    """Deserialise from a dict produced by composition_to_dict()."""
    comp = Composition(
        id=str(data.get("id") or uuid.uuid4()),
        name=str(data.get("name") or "Projeto"),
        duration_s=float(data.get("duration_s") or 0.0),
        fps=float(data.get("fps") or 30.0),
        width=int(data.get("width") or 1920),
        height=int(data.get("height") or 1080),
        waveform=list(data.get("waveform") or []),
        schema_version=int(data.get("schema_version") or 2),
    )
    for td in data.get("tracks") or []:
        comp.tracks.append(_track_from_dict(td))
    return comp


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _tc_to_clip(tc: TimelineClip, clip_type: str) -> Clip:
    """Convert a legacy TimelineClip to the new Clip format."""
    cl = Clip(
        id=str(uuid.uuid4())[:8],
        clip_type=clip_type,
        source_path=str(tc.source_path or ""),
        start_s=float(tc.start_s),
        end_s=float(tc.end_s),
        in_point_s=float(tc.start_s),
        out_point_s=float(tc.end_s),
        label=str(tc.label or ""),
        opacity_pct=float(getattr(tc, "opacity_pct", 100.0)),
        position_x_pct=float(getattr(tc, "position_x_pct", 0.0)),
        position_y_pct=float(getattr(tc, "position_y_pct", 0.0)),
        scale_pct=float(tc.scale_pct),
        volume_pct=float(tc.volume_pct),
        speed_factor=float(getattr(tc, "speed_factor", 1.0)),
        chroma_enabled=bool(tc.chroma_enabled),
        chroma_color=str(tc.chroma_color),
        chroma_tolerance=float(tc.chroma_tolerance),
        person_remove_enabled=bool(getattr(tc, "person_remove_enabled", False)),
        person_remove_strength=float(getattr(tc, "person_remove_strength", 72.0)),
        person_remove_feather=float(getattr(tc, "person_remove_feather", 10.0)),
        transition_in=str(tc.transition or "cut").lower().replace("corte", "cut"),
        transition_in_s=float(tc.transition_duration_s),
    )
    return cl


def _clip_to_tc(cl: Clip) -> TimelineClip:
    """Convert a new Clip back to the legacy TimelineClip format."""
    tc = TimelineClip(
        start_s=cl.start_s,
        end_s=cl.end_s,
        clip_type=cl.clip_type,
        label=cl.label,
        source_path=cl.source_path,
        scale_pct=cl.scale_pct,
        volume_pct=cl.volume_pct,
        transition=cl.transition_in if cl.transition_in != "cut" else "Corte",
        transition_duration_s=cl.transition_in_s,
        opacity_pct=cl.opacity_pct,
        position_x_pct=cl.position_x_pct,
        position_y_pct=cl.position_y_pct,
        speed_factor=cl.speed_factor,
        chroma_enabled=cl.chroma_enabled,
        chroma_color=cl.chroma_color,
        chroma_tolerance=cl.chroma_tolerance,
        person_remove_enabled=cl.person_remove_enabled,
        person_remove_strength=cl.person_remove_strength,
        person_remove_feather=cl.person_remove_feather,
    )
    # Restore text fields if present
    if cl.clip_type == "text":
        style = cl.text_style or {}
        tc.text_overlay = cl.text_content
        tc.text_position_x_pct = float(style.get("position_x_pct", 0.0))
        tc.text_position_y_pct = float(style.get("position_y_pct", 72.0))
        tc.text_size_pct = float(style.get("size_pct", 100.0))
        tc.text_color = str(style.get("color", "#ffffff"))
        tc.text_background_enabled = bool(style.get("background_enabled", True))
        tc.text_background_color = str(style.get("background_color", "#000000"))
        tc.text_font = str(style.get("font", "default"))
        tc.text_bold = bool(style.get("bold", False))
        tc.text_italic = bool(style.get("italic", False))
        tc.text_align = str(style.get("align", "center"))
    return tc


def _build_text_style(tc: TimelineClip) -> dict:
    style = _default_text_style()
    style.update({
        "font": str(getattr(tc, "text_font", "default") or "default"),
        "size_pct": float(getattr(tc, "text_size_pct", 100.0)),
        "color": str(getattr(tc, "text_color", "#ffffff") or "#ffffff"),
        "bold": bool(getattr(tc, "text_bold", False)),
        "italic": bool(getattr(tc, "text_italic", False)),
        "align": str(getattr(tc, "text_align", "center") or "center"),
        "background_enabled": bool(getattr(tc, "text_background_enabled", True)),
        "background_color": str(getattr(tc, "text_background_color", "#000000") or "#000000"),
        "background_alpha": float(getattr(tc, "text_background_alpha", 0.65)),
        "bg_rounded": bool(getattr(tc, "text_bg_rounded", True)),
        "shadow_enabled": bool(getattr(tc, "text_shadow_enabled", False)),
        "shadow_color": str(getattr(tc, "text_shadow_color", "#000000")),
        "shadow_offset_x": int(getattr(tc, "text_shadow_offset_x", 2)),
        "shadow_offset_y": int(getattr(tc, "text_shadow_offset_y", 2)),
        "shadow_blur": int(getattr(tc, "text_shadow_blur", 4)),
        "stroke_enabled": bool(getattr(tc, "text_stroke_enabled", False)),
        "stroke_color": str(getattr(tc, "text_stroke_color", "#000000")),
        "stroke_width": int(getattr(tc, "text_stroke_width", 2)),
        "max_width_pct": float(getattr(tc, "text_max_width_pct", 80.0)),
        "line_spacing": float(getattr(tc, "text_line_spacing", 1.2)),
        "position_x_pct": float(getattr(tc, "text_position_x_pct", 0.0)),
        "position_y_pct": float(getattr(tc, "text_position_y_pct", 72.0)),
    })
    return style


def _is_image_path(path: str) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def _track_to_dict(t: Track) -> dict[str, Any]:
    return {
        "id": t.id,
        "track_type": t.track_type,
        "name": t.name,
        "index": t.index,
        "locked": t.locked,
        "muted": t.muted,
        "solo": t.solo,
        "visible": t.visible,
        "height_px": t.height_px,
        "color": t.color,
        "clips": [_clip_to_dict(c) for c in t.clips],
    }


def _track_from_dict(d: dict[str, Any]) -> Track:
    t = Track(
        id=str(d.get("id") or uuid.uuid4()),
        track_type=str(d.get("track_type") or "video"),
        name=str(d.get("name") or ""),
        index=int(d.get("index") or 0),
        locked=bool(d.get("locked")),
        muted=bool(d.get("muted")),
        solo=bool(d.get("solo")),
        visible=bool(d.get("visible", True)),
        height_px=int(d.get("height_px") or 0),
        color=str(d.get("color") or ""),
    )
    for cd in d.get("clips") or []:
        t.clips.append(_clip_from_dict(cd))
    return t


def _clip_to_dict(c: Clip) -> dict[str, Any]:
    return {
        "id": c.id,
        "clip_type": c.clip_type,
        "source_path": c.source_path,
        "start_s": c.start_s,
        "end_s": c.end_s,
        "in_point_s": c.in_point_s,
        "out_point_s": c.out_point_s,
        "label": c.label,
        "opacity_pct": c.opacity_pct,
        "position_x_pct": c.position_x_pct,
        "position_y_pct": c.position_y_pct,
        "scale_pct": c.scale_pct,
        "rotation_deg": c.rotation_deg,
        "volume_pct": c.volume_pct,
        "speed_factor": c.speed_factor,
        "text_content": c.text_content,
        "text_style": c.text_style,
        "chroma_enabled": c.chroma_enabled,
        "chroma_color": c.chroma_color,
        "chroma_tolerance": c.chroma_tolerance,
        "person_remove_enabled": c.person_remove_enabled,
        "person_remove_strength": c.person_remove_strength,
        "person_remove_feather": c.person_remove_feather,
        "transition_in": c.transition_in,
        "transition_in_s": c.transition_in_s,
        "keyframes": {
            prop: [{"time_s": kf.time_s, "value": kf.value, "interp": kf.interp,
                    "ease_out": kf.ease_out, "ease_in": kf.ease_in}
                   for kf in kfs]
            for prop, kfs in c.keyframes.items()
        },
        "color_grade": c.color_grade,
    }


def _clip_from_dict(d: dict[str, Any]) -> Clip:
    kfs: dict[str, list[Keyframe]] = {}
    for prop, kf_list in (d.get("keyframes") or {}).items():
        kfs[prop] = [
            Keyframe(
                time_s=float(k["time_s"]), value=float(k["value"]),
                interp=str(k.get("interp", "linear")),
                ease_out=float(k.get("ease_out", 0.33)),
                ease_in=float(k.get("ease_in", 0.33)),
            )
            for k in kf_list
        ]
    return Clip(
        id=str(d.get("id") or uuid.uuid4()),
        clip_type=str(d.get("clip_type") or "video"),
        source_path=str(d.get("source_path") or ""),
        start_s=float(d.get("start_s") or 0.0),
        end_s=float(d.get("end_s") or 0.0),
        in_point_s=float(d.get("in_point_s") or 0.0),
        out_point_s=float(d.get("out_point_s") or 0.0),
        label=str(d.get("label") or ""),
        opacity_pct=float(d.get("opacity_pct", 100.0)),
        position_x_pct=float(d.get("position_x_pct", 0.0)),
        position_y_pct=float(d.get("position_y_pct", 0.0)),
        scale_pct=float(d.get("scale_pct", 100.0)),
        rotation_deg=float(d.get("rotation_deg", 0.0)),
        volume_pct=float(d.get("volume_pct", 100.0)),
        speed_factor=float(d.get("speed_factor", 1.0)),
        text_content=str(d.get("text_content") or ""),
        text_style=dict(d.get("text_style") or {}),
        chroma_enabled=bool(d.get("chroma_enabled")),
        chroma_color=str(d.get("chroma_color") or "#00ff00"),
        chroma_tolerance=float(d.get("chroma_tolerance", 45.0)),
        person_remove_enabled=bool(d.get("person_remove_enabled")),
        person_remove_strength=float(d.get("person_remove_strength", 72.0)),
        person_remove_feather=float(d.get("person_remove_feather", 10.0)),
        transition_in=str(d.get("transition_in") or "cut"),
        transition_in_s=float(d.get("transition_in_s", 0.4)),
        keyframes=kfs,
        color_grade=dict(d.get("color_grade") or {}),
    )
