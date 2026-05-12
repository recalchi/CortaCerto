from __future__ import annotations

from pathlib import Path
from typing import Any

from .timeline_model import TimelineClip, TimelineModel


def build_timeline_manifest(
    timeline_model: TimelineModel | None,
    project_name: str,
    primary_media: str,
) -> dict[str, Any]:
    """Build an OTIO-inspired edit manifest without embedding media."""
    clips = timeline_model.video_track.clips if timeline_model else []
    media_refs = _media_references(primary_media, clips)
    return {
        "schema": "cortacerto.timeline.v1",
        "project": project_name,
        "duration_s": float(timeline_model.duration_s) if timeline_model else 0.0,
        "media": media_refs,
        "tracks": [
            {
                "name": "Video",
                "kind": "video",
                "clips": _manifest_clips(clips, primary_media),
            },
            {
                "name": "Audio",
                "kind": "audio",
                "clips": _manifest_audio_clips(clips, primary_media),
            },
        ],
    }


def _media_references(primary_media: str, clips: list[TimelineClip]) -> list[dict[str, Any]]:
    paths: list[str] = []
    for path in [primary_media, *[clip.source_path for clip in clips]]:
        clean = str(path or "").strip()
        if clean and clean not in paths:
            paths.append(clean)
    return [
        {
            "id": _media_id(idx),
            "target_url": path,
            "name": Path(path).name,
        }
        for idx, path in enumerate(paths)
    ]


def _manifest_clips(clips: list[TimelineClip], primary_media: str) -> list[dict[str, Any]]:
    output_cursor = 0.0
    result: list[dict[str, Any]] = []
    for idx, clip in enumerate(clips, start=1):
        duration_s = max(0.0, float(clip.end_s) - float(clip.start_s))
        result.append(
            {
                "id": f"clip-{idx:04d}",
                "name": clip.label or f"Clip {idx}",
                "media_id": _media_id_for_path(clip.source_path or primary_media, primary_media, clips),
                "source_start_s": float(clip.start_s),
                "source_end_s": float(clip.end_s),
                "output_start_s": output_cursor,
                "output_end_s": output_cursor + duration_s,
                "effects": _clip_effects(clip),
            }
        )
        output_cursor += duration_s
    return result


def _manifest_audio_clips(clips: list[TimelineClip], primary_media: str) -> list[dict[str, Any]]:
    output_cursor = 0.0
    result: list[dict[str, Any]] = []
    for idx, clip in enumerate(clips, start=1):
        duration_s = max(0.0, float(clip.end_s) - float(clip.start_s))
        result.append(
            {
                "id": f"audio-{idx:04d}",
                "name": clip.label or f"Clip {idx}",
                "media_id": _media_id_for_path(primary_media, primary_media, clips),
                "source_start_s": float(clip.start_s),
                "source_end_s": float(clip.end_s),
                "output_start_s": output_cursor,
                "output_end_s": output_cursor + duration_s,
                "effects": _audio_effects(clip),
            }
        )
        output_cursor += duration_s
    return result


def _clip_effects(clip: TimelineClip) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    pos_x = float(getattr(clip, "position_x_pct", 0.0))
    pos_y = float(getattr(clip, "position_y_pct", 0.0))
    if abs(float(clip.scale_pct) - 100.0) > 0.01 or abs(pos_x) > 0.01 or abs(pos_y) > 0.01:
        effects.append(
            {
                "type": "transform",
                "scale_pct": float(clip.scale_pct),
                "position_x_pct": pos_x,
                "position_y_pct": pos_y,
            }
        )
    if str(clip.text_overlay or "").strip():
        effects.append({"type": "text", "text": str(clip.text_overlay).strip()})
    if bool(clip.chroma_enabled):
        effects.append(
            {
                "type": "chroma_key",
                "color": clip.chroma_color,
                "tolerance": float(clip.chroma_tolerance),
            }
        )
    if clip.transition and clip.transition != "Corte":
        effects.append({"type": "transition", "name": clip.transition})
    return effects


def _audio_effects(clip: TimelineClip) -> list[dict[str, Any]]:
    if abs(float(clip.volume_pct) - 100.0) <= 0.01:
        return []
    return [{"type": "volume", "volume_pct": float(clip.volume_pct)}]


def _media_id_for_path(path: str, primary_media: str, clips: list[TimelineClip]) -> str:
    paths = [item["target_url"] for item in _media_references(primary_media, clips)]
    try:
        return _media_id(paths.index(path))
    except ValueError:
        return _media_id(0)


def _media_id(index: int) -> str:
    return f"media-{index + 1:04d}"
