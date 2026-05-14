import unittest
import tempfile
import time
import queue
from pathlib import Path

from PIL import Image

from src.core.color_grade import ColorGrade
from src.core.preview_engine import PreviewSettings
from src.ui.app import (
    _apply_clip_options_to_timeline_model,
    _apply_chroma_key_preview,
    _apply_clip_preview_options,
    _apply_text_clip_preview_options,
    _compose_visual_overlay_preview,
    _apply_segments_to_timeline_model,
    _build_project_metadata,
    _clip_options_from_timeline_model,
    _clip_inspector_rows_for_mode,
    _clip_edges,
    _clip_for_time,
    _overlay_clip_for_time,
    _overlay_clips_for_time,
    _clip_insert_index,
    _text_options_from_timeline_model,
    _apply_text_options_to_timeline_model,
    _upsert_text_overlay_clip,
    _insert_media_clip_replacing_range,
    _clip_source_frame_index,
    _can_clipboard_timeline_clip,
    _cycle_active_clip_index,
    _cycle_index_in_order,
    _hex_to_rgb,
    _clone_timeline_clip,
    _cleanup_project_trash,
    _coerce_frame_to_segments,
    _coerce_time_to_segments,
    _compact_clip_ranges,
    _compact_display_to_source_time,
    _compact_source_to_display_time,
    _drain_runtime_queue,
    _fit_preview_image,
    _first_video_path_from_drop,
    _first_existing_video_path,
    _image_source_preview_image,
    _merge_media_paths,
    _media_display_name,
    _normalize_hex_color,
    _project_media_counts,
    _is_image_path,
    _is_project_media_path,
    _is_video_path,
    _move_project_to_trash,
    _playback_delay_ms,
    _playback_effective_fps,
    _playback_crosses_removed_range,
    _playback_target_frame,
    _paste_clip_at_time,
    _project_name_from_path,
    _project_metadata_with_launcher_media,
    _project_segments_from_metadata,
    _project_state_payload,
    _project_trash_dir,
    _read_project_metadata,
    _remove_media_path,
    _removed_ranges_from_segments,
    _register_drop_target,
    _relative_seek_frame,
    _restore_project_from_trash,
    _safe_project_slug,
    _should_double_click_insert_media,
    _media_path_used_in_timeline,
    _same_media_path,
    _preview_base_image_for_timeline,
    _preview_render_frame_index,
    _preview_control_hit,
    _preview_control_handles,
    _preview_visual_display_bounds,
    _point_inside_rect,
    _preview_text_clip_hit,
    _preview_text_display_bounds,
    _preview_text_anchor,
    _preview_text_lines,
    _sample_preview_hex_color,
    _snap_time_to_edges,
    _snap_time_to_edges_with_flag,
    _split_clip_at_time,
    _split_drop_paths,
    _media_paths_from_drop,
    _move_clip_bounds_with_snap,
    _move_track_item_layer,
    _snap_insert_start_for_duration,
    _set_clip_duration_bounds,
    _video_paths_from_drop,
    _time_to_frame,
    _timeline_time_to_x,
    _timeline_track_bounds,
    _timeline_handle_edge_at,
    _timeline_x_to_time,
    _timeline_view_time_to_x,
    _timeline_x_to_view_time,
    _timeline_pan_center,
    _timeline_zoom_step,
    _timeline_zoom_window,
    _track_control_status,
    _track_options_from_metadata,
    _timeline_clip_fill,
    _text_clip_for_time,
    _sync_text_clip_to_video_overlay,
    _clear_video_text_overlay_for_text_clip,
)
from src.core.timeline_model import TimelineClip, build_timeline_model


class PreviewUiTests(unittest.TestCase):
    def test_drain_runtime_queue_removes_stale_project_callbacks(self) -> None:
        runtime_queue = queue.Queue()
        runtime_queue.put(("__PREVIEW__", object()))
        runtime_queue.put(("__TIMELINE_READY__", object()))

        self.assertEqual(_drain_runtime_queue(runtime_queue), 2)
        self.assertTrue(runtime_queue.empty())

    def test_relative_seek_frame_supports_frame_and_second_steps(self) -> None:
        self.assertEqual(_relative_seek_frame(10, 1, 30.0, 100), 11)
        self.assertEqual(_relative_seek_frame(10, -1, 30.0, 100), 9)
        self.assertEqual(_relative_seek_frame(10, 1, 30.0, 100, large_step=True), 40)
        self.assertEqual(_relative_seek_frame(10, -1, 30.0, 100, large_step=True), 0)
        self.assertEqual(_relative_seek_frame(95, 1, 30.0, 100, large_step=True), 99)

    def test_preview_render_frame_uses_incoming_frame_during_playback(self) -> None:
        self.assertEqual(_preview_render_frame_index(10, 12, True), 12)
        self.assertEqual(_preview_render_frame_index(10, 12, False), 10)

    def test_register_drop_target_prefers_widget_dnd_methods(self) -> None:
        calls = []

        class Widget:
            def drop_target_register(self, target: str) -> None:
                calls.append(("register", target))

            def dnd_bind(self, event: str, callback) -> None:
                calls.append(("bind", event, callback))

        callback = lambda _event: None

        self.assertTrue(_register_drop_target(Widget(), callback))
        self.assertEqual(calls[0][0], "register")
        self.assertEqual(calls[1], ("bind", "<<Drop>>", callback))

    def test_project_name_from_cortacerto_file(self) -> None:
        self.assertEqual(_project_name_from_path("C:/videos/meu-corte.ccp"), "meu-corte")
        self.assertEqual(_project_name_from_path("C:/videos/legado.cortacerto.json"), "legado")
        self.assertEqual(_project_name_from_path(None), "Projeto rápido")

    def test_project_slug_is_filesystem_friendly(self) -> None:
        self.assertEqual(_safe_project_slug("Meu Projeto 01!"), "Meu-Projeto-01")
        self.assertEqual(_safe_project_slug("   "), "projeto")

    def test_project_metadata_has_expected_schema(self) -> None:
        metadata = _build_project_metadata("C:/videos/meu-corte.ccp")

        self.assertEqual(metadata["app"], "CortaCerto")
        self.assertEqual(metadata["version"], 1)
        self.assertEqual(metadata["name"], "meu-corte")
        self.assertEqual(metadata["slug"], "meu-corte")
        self.assertEqual(metadata["media_paths"], [])
        self.assertIn("created_at", metadata)

    def test_launcher_image_adds_media_without_video_path(self) -> None:
        metadata = _project_metadata_with_launcher_media(
            _build_project_metadata("C:/videos/meu-corte.ccp"),
            "C:/assets/capa.png",
        )

        self.assertIsNone(metadata["video_path"])
        self.assertEqual(metadata["media_paths"], ["C:/assets/capa.png"])

    def test_launcher_video_sets_video_path_and_media(self) -> None:
        metadata = _project_metadata_with_launcher_media(
            _build_project_metadata("C:/videos/meu-corte.ccp"),
            "C:/assets/base.mp4",
        )

        self.assertEqual(metadata["video_path"], "C:/assets/base.mp4")
        self.assertEqual(metadata["media_paths"], ["C:/assets/base.mp4"])

    def test_first_existing_video_path_ignores_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "capa.png"
            video_path = Path(tmp) / "base.mp4"
            image_path.write_bytes(b"fake")
            video_path.write_bytes(b"fake")

            selected = _first_existing_video_path([str(image_path), str(video_path)])

        self.assertEqual(selected, str(video_path))

    def test_read_project_metadata_merges_existing_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canal.ccp"
            path.write_text('{"name":"Canal","video_path":"C:/video.mp4","media_paths":["C:/extra.mov"]}', encoding="utf-8")

            metadata = _read_project_metadata(str(path))

        self.assertEqual(metadata["name"], "Canal")
        self.assertEqual(metadata["slug"], "canal")
        self.assertEqual(metadata["video_path"], "C:/video.mp4")
        self.assertEqual(metadata["media_paths"], ["C:/extra.mov", "C:/video.mp4"])

    def test_read_project_metadata_recovers_from_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quebrado.ccp"
            path.write_text("{", encoding="utf-8")

            metadata = _read_project_metadata(str(path))

        self.assertEqual(metadata["name"], "quebrado")
        self.assertIsNone(metadata["video_path"])

    def test_project_trash_dir_uses_cortacerto_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_project_trash_dir(Path(tmp)), Path(tmp) / "Lixeira")

    def test_cleanup_project_trash_removes_items_older_than_30_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trash = Path(tmp) / "Lixeira"
            trash.mkdir()
            old_file = trash / "antigo.ccp"
            fresh_file = trash / "novo.ccp"
            old_file.write_text("old", encoding="utf-8")
            fresh_file.write_text("fresh", encoding="utf-8")
            now = time.time()
            old_time = now - (31 * 24 * 60 * 60)
            fresh_time = now - (2 * 24 * 60 * 60)
            old_file.touch()
            fresh_file.touch()
            import os
            os.utime(old_file, (old_time, old_time))
            os.utime(fresh_file, (fresh_time, fresh_time))

            removed = _cleanup_project_trash(trash, now_s=now, days=30)

            self.assertEqual(removed, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(fresh_file.exists())

    def test_move_project_to_trash_uses_unique_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "canal.ccp"
            project.write_text("project", encoding="utf-8")
            trash = root / "Lixeira"
            trash.mkdir()
            (trash / "canal.ccp").write_text("old", encoding="utf-8")

            moved_to = _move_project_to_trash(str(project), trash)

            self.assertEqual(moved_to, trash / "canal-1.ccp")
            self.assertFalse(project.exists())
            self.assertEqual(moved_to.read_text(encoding="utf-8"), "project")

    def test_restore_project_from_trash_uses_unique_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trash = root / "Lixeira"
            trash.mkdir()
            trashed = trash / "canal.ccp"
            trashed.write_text("restored", encoding="utf-8")
            destination = root / "Projetos"
            destination.mkdir()
            (destination / "canal.ccp").write_text("current", encoding="utf-8")

            restored_to = _restore_project_from_trash(str(trashed), destination)

            self.assertEqual(restored_to, destination / "canal-1.ccp")
            self.assertFalse(trashed.exists())
            self.assertEqual(restored_to.read_text(encoding="utf-8"), "restored")

    def test_drop_path_helpers_pick_first_video_file(self) -> None:
        self.assertTrue(_is_video_path("C:/midias/corte.MP4"))
        self.assertTrue(_is_image_path("C:/midias/capa.PNG"))
        self.assertTrue(_is_project_media_path("C:/midias/capa.PNG"))
        self.assertFalse(_is_video_path("C:/midias/audio.mp3"))
        self.assertEqual(
            _first_video_path_from_drop("{C:/midias/audio.mp3} {C:/midias/video final.mp4}"),
            "C:/midias/video final.mp4",
        )
        self.assertEqual(
            _video_paths_from_drop("{C:/midias/a.mp4} {C:/midias/b.mov} C:/midias/audio.mp3"),
            ["C:/midias/a.mp4", "C:/midias/b.mov"],
        )
        self.assertEqual(
            _media_paths_from_drop("{C:/midias/a.png} {C:/midias/b.mov} C:/midias/audio.mp3"),
            ["C:/midias/a.png", "C:/midias/b.mov"],
        )

    def test_media_double_click_inserts_only_when_timeline_is_ready(self) -> None:
        self.assertFalse(_should_double_click_insert_media("C:/midias/capa.png", None, False))
        self.assertTrue(_should_double_click_insert_media("C:/midias/capa.png", "C:/midias/main.mp4", True))
        self.assertTrue(_should_double_click_insert_media("C:/midias/broll.mp4", "C:/midias/main.mp4", True))
        self.assertFalse(_should_double_click_insert_media("C:/midias/main.mp4", "C:/midias/main.mp4", True))

    def test_clip_inspector_rows_are_separated_by_selection_mode(self) -> None:
        base = 40

        text_rows = _clip_inspector_rows_for_mode(base, "text")
        visual_rows = _clip_inspector_rows_for_mode(base, "visual")
        speech_rows = _clip_inspector_rows_for_mode(base, "speech")

        self.assertIn(base + 6, text_rows)
        self.assertIn(base + 10, text_rows)
        self.assertIn(base + 16, text_rows)
        self.assertNotIn(base + 2, text_rows)
        self.assertIn(base + 2, visual_rows)
        self.assertIn(base + 14, visual_rows)
        self.assertIn(base + 16, visual_rows)
        self.assertIn(base + 17, visual_rows)
        self.assertNotIn(base + 6, visual_rows)
        self.assertIn(base + 5, speech_rows)
        self.assertIn(base + 11, speech_rows)
        self.assertNotIn(base + 14, speech_rows)
        self.assertNotIn(base + 16, speech_rows)
        self.assertNotIn(base + 17, speech_rows)

    def test_set_clip_duration_bounds_keeps_item_inside_timeline(self) -> None:
        self.assertEqual(_set_clip_duration_bounds(2.0, 5.0, 10.0, 0.5), (2.0, 7.0))
        self.assertEqual(_set_clip_duration_bounds(9.9, 5.0, 10.0, 0.5), (9.5, 10.0))
        self.assertEqual(_set_clip_duration_bounds(1.0, 0.1, 10.0, 0.5), (1.0, 1.5))

    def test_move_track_item_layer_reorders_same_track_items(self) -> None:
        items = ["base", "middle", "top"]

        moved, index = _move_track_item_layer(items, 1, 1)

        self.assertTrue(moved)
        self.assertEqual(index, 2)
        self.assertEqual(items, ["base", "top", "middle"])

        moved, index = _move_track_item_layer(items, 2, 1)

        self.assertFalse(moved)
        self.assertEqual(index, 2)
        self.assertEqual(items, ["base", "top", "middle"])

    def test_merge_media_paths_keeps_supported_unique_order(self) -> None:
        self.assertEqual(
            _merge_media_paths(["C:/midias/a.mp4", "C:/midias/capa.PNG", "C:/midias/audio.mp3"], ["C:/midias/a.mp4", "C:/midias/b.MOV"]),
            ["C:/midias/a.mp4", "C:/midias/capa.PNG", "C:/midias/b.MOV"],
        )

    def test_media_display_helpers_separate_videos_and_images(self) -> None:
        paths = ["C:/midias/a.mp4", "C:/midias/capa.PNG", "C:/midias/b.webm"]

        self.assertEqual(_media_display_name(paths[0]), "[VID] a.mp4")
        self.assertEqual(_media_display_name(paths[1]), "[IMG] capa.PNG")
        self.assertEqual(_project_media_counts(paths), (2, 1))

    def test_remove_media_path_is_case_insensitive_and_keeps_others(self) -> None:
        paths = ["C:/midias/a.mp4", "C:/midias/capa.PNG", "C:/midias/b.webm"]

        self.assertTrue(_same_media_path("C:/midias/CAPA.png", "C:/midias/capa.PNG"))
        self.assertEqual(_remove_media_path(paths, "C:/midias/capa.png"), ["C:/midias/a.mp4", "C:/midias/b.webm"])

    def test_media_path_used_in_timeline_detects_source_references(self) -> None:
        timeline = build_timeline_model(8.0, [(0.0, 2.0), (4.0, 6.0)])
        timeline.video_track.clips[1].source_path = "C:/midias/broll.mp4"

        self.assertTrue(_media_path_used_in_timeline("C:/midias/BROLL.mp4", timeline))
        self.assertFalse(_media_path_used_in_timeline("C:/midias/capa.png", timeline))

    def test_split_drop_paths_handles_braced_paths(self) -> None:
        paths = _split_drop_paths("{C:/midias/video final.mp4} C:/midias/outro.mov")

        self.assertEqual(paths, ["C:/midias/video final.mp4", "C:/midias/outro.mov"])

    def test_project_state_payload_stores_resume_data(self) -> None:
        payload = _project_state_payload(
            project_name="Canal",
            video_path="C:/video.mp4",
            current_time_s=12.5,
            timeline_segments=[(1.0, 3.0), (5.0, 5.0), (6.0, 8.0)],
            timeline_dirty=True,
            media_paths=["C:/extra.mov"],
            title="Titulo",
            subtitle="Sub",
            description="Descricao",
            clip_options=[{"label": "Intro", "scale_pct": 125.0}],
            track_options={"visual_visible": False, "text_visible": True, "audio_muted": True},
        )

        self.assertEqual(payload["video_path"], "C:/video.mp4")
        self.assertEqual(payload["media_paths"], ["C:/extra.mov", "C:/video.mp4"])
        self.assertEqual(payload["current_time_s"], 12.5)
        self.assertEqual(payload["timeline_segments"], [
            {"start_s": 1.0, "end_s": 3.0},
            {"start_s": 6.0, "end_s": 8.0},
        ])
        self.assertEqual(payload["clip_options"], [{"label": "Intro", "scale_pct": 125.0}])
        self.assertEqual(payload["track_options"], {"visual_visible": False, "text_visible": True, "audio_muted": True})
        self.assertEqual(payload["publish"], {"title": "Titulo", "subtitle": "Sub", "description": "Descricao"})
        self.assertTrue(payload["timeline_dirty"])

    def test_track_options_metadata_defaults_and_normalizes(self) -> None:
        self.assertEqual(
            _track_options_from_metadata({}),
            {"visual_visible": True, "text_visible": True, "audio_muted": False},
        )
        self.assertEqual(
            _track_options_from_metadata({"track_options": {"visual_visible": False, "text_visible": False, "audio_muted": True}}),
            {"visual_visible": False, "text_visible": False, "audio_muted": True},
        )

    def test_project_segments_from_metadata_clamps_invalid_ranges(self) -> None:
        metadata = {
            "timeline_segments": [
                {"start_s": -1, "end_s": 2},
                {"start_s": 4, "end_s": 3},
                {"start_s": 8, "end_s": 20},
            ]
        }

        self.assertEqual(_project_segments_from_metadata(metadata, 10.0), [(0.0, 2.0), (8.0, 10.0)])

    def test_apply_segments_to_timeline_model_rebuilds_tracks_and_removed_ranges(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 10.0)])

        _apply_segments_to_timeline_model(model, 10.0, [(1.0, 3.0), (6.0, 8.0)])

        self.assertEqual([(c.start_s, c.end_s, c.label) for c in model.video_track.clips], [
            (1.0, 3.0, "Clip 1"),
            (6.0, 8.0, "Clip 2"),
        ])
        self.assertEqual([(c.start_s, c.end_s) for c in model.audio_track.clips], [(1.0, 3.0), (6.0, 8.0)])
        self.assertEqual(model.removed_ranges, [(0.0, 1.0), (3.0, 6.0), (8.0, 10.0)])

    def test_clip_options_round_trip_editor_metadata(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 4.0)])
        clip = model.video_track.clips[0]
        clip.label = "Intro"
        clip.scale_pct = 135.0
        clip.volume_pct = 80.0
        clip.transition = "Fade"
        clip.text_overlay = "Abertura"
        clip.chroma_enabled = True
        clip.chroma_color = "#00ff00"
        clip.chroma_tolerance = 70.0
        clip.position_x_pct = 25.0
        clip.position_y_pct = -15.0
        clip.text_position_x_pct = -20.0
        clip.text_position_y_pct = 40.0
        clip.text_size_pct = 130.0
        clip.text_color = "#ffee11"
        clip.text_background_enabled = False
        clip.text_background_color = "#112233"
        clip.opacity_pct = 55.0

        options = _clip_options_from_timeline_model(model)
        restored = build_timeline_model(10.0, [(0.0, 4.0)])
        _apply_clip_options_to_timeline_model(restored, options)

        restored_clip = restored.video_track.clips[0]
        self.assertEqual(restored_clip.clip_type, "speech")
        self.assertEqual(restored_clip.label, "Intro")
        self.assertEqual(restored_clip.scale_pct, 135.0)
        self.assertEqual(restored_clip.volume_pct, 80.0)
        self.assertEqual(restored_clip.transition, "Fade")
        self.assertEqual(restored_clip.text_overlay, "Abertura")
        self.assertTrue(restored_clip.chroma_enabled)
        self.assertEqual(restored_clip.chroma_color, "#00ff00")
        self.assertEqual(restored_clip.chroma_tolerance, 70.0)
        self.assertEqual(restored_clip.position_x_pct, 25.0)
        self.assertEqual(restored_clip.position_y_pct, -15.0)
        self.assertEqual(restored_clip.text_position_x_pct, -20.0)
        self.assertEqual(restored_clip.text_position_y_pct, 40.0)
        self.assertEqual(restored_clip.text_size_pct, 130.0)
        self.assertEqual(restored_clip.text_color, "#ffee11")
        self.assertFalse(restored_clip.text_background_enabled)
        self.assertEqual(restored_clip.text_background_color, "#112233")
        self.assertEqual(restored_clip.opacity_pct, 55.0)
        self.assertEqual(restored.audio_track.clips[0].scale_pct, 135.0)
        self.assertEqual(restored.text_track.clips[0].text_overlay, "Abertura")

    def test_text_options_round_trip_independent_track(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 4.0)])
        clip = model.video_track.clips[0]
        clip.text_overlay = "Titulo"
        clip.text_position_x_pct = 10.0
        clip.text_position_y_pct = 60.0
        clip.text_size_pct = 120.0
        clip.text_color = "#ffee11"
        clip.text_background_enabled = False
        clip.text_background_color = "#223344"
        _upsert_text_overlay_clip(model, clip)

        options = _text_options_from_timeline_model(model)
        restored = build_timeline_model(10.0, [(0.0, 4.0)])
        _apply_text_options_to_timeline_model(restored, options)

        self.assertEqual(len(restored.text_track.clips), 1)
        text_clip = restored.text_track.clips[0]
        self.assertEqual(text_clip.clip_type, "text")
        self.assertEqual(text_clip.text_overlay, "Titulo")
        self.assertEqual(text_clip.text_position_x_pct, 10.0)
        self.assertEqual(text_clip.text_position_y_pct, 60.0)
        self.assertEqual(text_clip.text_size_pct, 120.0)
        self.assertEqual(text_clip.text_color, "#ffee11")
        self.assertFalse(text_clip.text_background_enabled)
        self.assertEqual(text_clip.text_background_color, "#223344")

    def test_text_clip_for_time_uses_topmost_text_track_item(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 10.0)])
        model.text_track.clips = [
            TimelineClip(0.0, 8.0, "text", "Base", text_overlay="Base"),
            TimelineClip(2.0, 6.0, "text", "Topo", text_overlay="Topo"),
        ]

        self.assertEqual(_text_clip_for_time(model, 3.0).text_overlay, "Topo")
        self.assertEqual(_text_clip_for_time(model, 9.0), None)

    def test_text_clip_edits_sync_to_legacy_video_overlay(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 4.0)])
        text_clip = TimelineClip(
            0.0,
            4.0,
            "text",
            "Titulo",
            text_overlay="Titulo",
            text_position_x_pct=20.0,
            text_position_y_pct=65.0,
            text_size_pct=140.0,
        )

        _sync_text_clip_to_video_overlay(model, text_clip)

        video_clip = model.video_track.clips[0]
        self.assertEqual(video_clip.text_overlay, "Titulo")
        self.assertEqual(video_clip.text_position_x_pct, 20.0)
        self.assertEqual(video_clip.text_position_y_pct, 65.0)
        self.assertEqual(video_clip.text_size_pct, 140.0)

        _clear_video_text_overlay_for_text_clip(model, text_clip)
        self.assertEqual(video_clip.text_overlay, "")

    def test_clone_timeline_clip_preserves_editor_options(self) -> None:
        clip = TimelineClip(
            1.0,
            2.0,
            "speech",
            "Corte 1",
            scale_pct=150.0,
            volume_pct=70.0,
            transition="Dissolver",
            chroma_enabled=True,
            chroma_color="#112233",
            position_x_pct=20.0,
            position_y_pct=-10.0,
            text_position_x_pct=10.0,
            text_position_y_pct=60.0,
            text_size_pct=120.0,
        )

        cloned = _clone_timeline_clip(clip)

        self.assertEqual(cloned.scale_pct, 150.0)
        self.assertEqual(cloned.volume_pct, 70.0)
        self.assertEqual(cloned.transition, "Dissolver")
        self.assertTrue(cloned.chroma_enabled)
        self.assertEqual(cloned.chroma_color, "#112233")
        self.assertEqual(cloned.position_x_pct, 20.0)
        self.assertEqual(cloned.position_y_pct, -10.0)
        self.assertEqual(cloned.text_position_x_pct, 10.0)
        self.assertEqual(cloned.text_position_y_pct, 60.0)
        self.assertEqual(cloned.text_size_pct, 120.0)

    def test_can_clipboard_timeline_clip_allows_text_and_external_media_only(self) -> None:
        self.assertTrue(_can_clipboard_timeline_clip(TimelineClip(0.0, 1.0, "text", "Titulo")))
        self.assertTrue(_can_clipboard_timeline_clip(TimelineClip(0.0, 1.0, "image", "Logo", source_path="C:/m/logo.png")))
        self.assertFalse(_can_clipboard_timeline_clip(TimelineClip(0.0, 1.0, "speech", "Base")))
        self.assertFalse(_can_clipboard_timeline_clip(None))

    def test_clip_for_time_returns_active_clip(self) -> None:
        model = build_timeline_model(10.0, [(1.0, 3.0), (5.0, 7.0)])

        self.assertEqual(_clip_for_time(model, 5.5).label, "Clip 2")
        self.assertIsNone(_clip_for_time(model, 4.0))

    def test_overlay_clip_for_time_uses_topmost_overlay_without_replacing_base(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 10.0)])
        model.overlay_track.clips.append(TimelineClip(2.0, 8.0, "image", "Logo"))
        model.overlay_track.clips.append(TimelineClip(4.0, 6.0, "media", "Broll"))

        self.assertEqual(_clip_for_time(model, 5.0).label, "Clip 1")
        self.assertEqual([clip.label for clip in _overlay_clips_for_time(model, 5.0)], ["Logo", "Broll"])
        self.assertEqual(_overlay_clip_for_time(model, 5.0).label, "Broll")
        self.assertIsNone(_overlay_clip_for_time(model, 9.0))

    def test_cycle_active_clip_index_rotates_overlapping_items(self) -> None:
        clips = [
            TimelineClip(0.0, 6.0, "image", "Baixo"),
            TimelineClip(2.0, 6.0, "media", "Meio"),
            TimelineClip(3.0, 5.0, "image", "Topo"),
        ]

        self.assertEqual(_cycle_active_clip_index(clips, 4.0, None), 2)
        self.assertEqual(_cycle_active_clip_index(clips, 4.0, 2), 1)
        self.assertEqual(_cycle_active_clip_index(clips, 4.0, 1), 0)
        self.assertEqual(_cycle_active_clip_index(clips, 4.0, 0), 2)
        self.assertIsNone(_cycle_active_clip_index(clips, 8.0, None))

    def test_cycle_index_in_order_rotates_body_hit_results(self) -> None:
        self.assertEqual(_cycle_index_in_order([0, 1, 2], None), 2)
        self.assertEqual(_cycle_index_in_order([0, 1, 2], 2), 1)
        self.assertEqual(_cycle_index_in_order([0, 1, 2], 1), 0)
        self.assertEqual(_cycle_index_in_order([0, 1, 2], 0), 2)
        self.assertIsNone(_cycle_index_in_order([], None))

    def test_clip_source_frame_index_uses_clip_relative_time(self) -> None:
        clip = TimelineClip(10.0, 20.0, "speech", "B-roll")

        self.assertEqual(_clip_source_frame_index(clip, 12.0, fps=30.0, total_frames=1000), 60)
        self.assertEqual(_clip_source_frame_index(clip, 9.0, fps=30.0, total_frames=1000), 0)
        self.assertEqual(_clip_source_frame_index(clip, 60.0, fps=30.0, total_frames=100), 99)

    def test_apply_clip_preview_options_scales_and_draws_text(self) -> None:
        image = Image.new("RGB", (100, 80), "white")
        clip = TimelineClip(0.0, 1.0, "speech", "Intro", scale_pct=50.0, text_overlay="Titulo")

        rendered = _apply_clip_preview_options(image, clip)

        self.assertEqual(rendered.size, image.size)
        self.assertNotEqual(rendered.getpixel((0, 0)), image.getpixel((0, 0)))

    def test_apply_clip_preview_options_can_skip_mirrored_text_overlay(self) -> None:
        image = Image.new("RGB", (100, 80), "white")
        clip = TimelineClip(0.0, 1.0, "speech", "Intro", text_overlay="Titulo")

        rendered = _apply_clip_preview_options(image, clip, include_text=False)

        self.assertEqual(rendered.tobytes(), image.tobytes())

    def test_apply_text_clip_preview_options_draws_independent_text(self) -> None:
        image = Image.new("RGB", (100, 80), "white")
        clip = TimelineClip(0.0, 1.0, "text", "Titulo", text_overlay="Titulo")

        rendered = _apply_text_clip_preview_options(image, clip)

        self.assertEqual(rendered.size, image.size)
        self.assertNotEqual(rendered.tobytes(), image.tobytes())

    def test_preview_text_background_can_be_disabled_or_recolored(self) -> None:
        image = Image.new("RGB", (100, 80), "white")
        plain = TimelineClip(0.0, 1.0, "text", "Titulo", text_overlay="Titulo", text_background_enabled=False)
        colored = TimelineClip(
            0.0,
            1.0,
            "text",
            "Titulo",
            text_overlay="Titulo",
            text_position_x_pct=-100.0,
            text_position_y_pct=0.0,
            text_background_enabled=True,
            text_background_color="#112233",
        )

        plain_rendered = _apply_text_clip_preview_options(image, plain)
        colored_rendered = _apply_text_clip_preview_options(image, colored)

        self.assertNotEqual(plain_rendered.tobytes(), colored_rendered.tobytes())
        self.assertEqual(colored_rendered.getpixel((0, 0)), (17, 34, 51))

    def test_preview_text_color_can_be_recolored(self) -> None:
        image = Image.new("RGB", (100, 80), "black")
        white = TimelineClip(0.0, 1.0, "text", "Titulo", text_overlay="Titulo", text_background_enabled=False)
        red = TimelineClip(0.0, 1.0, "text", "Titulo", text_overlay="Titulo", text_color="#ff0000", text_background_enabled=False)

        white_rendered = _apply_text_clip_preview_options(image, white)
        red_rendered = _apply_text_clip_preview_options(image, red)

        self.assertNotEqual(white_rendered.tobytes(), red_rendered.tobytes())

    def test_preview_text_lines_support_multiline_content(self) -> None:
        self.assertEqual(_preview_text_lines("Linha 1\nLinha 2"), ["Linha 1", "Linha 2"])
        self.assertEqual(len(_preview_text_lines("1\n2\n3\n4\n5")), 4)

    def test_image_source_preview_letterboxes_to_target_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capa.png"
            Image.new("RGB", (40, 20), "red").save(path)

            rendered = _image_source_preview_image(str(path), (100, 100))

        self.assertEqual(rendered.size, (100, 100))
        self.assertEqual(rendered.getpixel((50, 50)), (255, 0, 0))
        self.assertEqual(rendered.getpixel((0, 0)), (0, 0, 0))

    def test_apply_clip_preview_options_positions_scaled_clip(self) -> None:
        image = Image.new("RGB", (10, 10), "white")
        clip = TimelineClip(0.0, 1.0, "speech", "Intro", scale_pct=50.0, position_x_pct=100.0)

        rendered = _apply_clip_preview_options(image, clip)

        self.assertEqual(rendered.getpixel((0, 5)), (0, 0, 0))
        self.assertEqual(rendered.getpixel((9, 5)), (255, 255, 255))

    def test_compose_visual_overlay_preview_keeps_base_visible_when_overlay_is_scaled(self) -> None:
        base = Image.new("RGB", (10, 10), "red")
        overlay = Image.new("RGB", (10, 10), "blue")
        clip = TimelineClip(0.0, 1.0, "image", "Logo", scale_pct=50.0)

        rendered = _compose_visual_overlay_preview(base, overlay, clip)

        self.assertEqual(rendered.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(rendered.getpixel((5, 5)), (0, 0, 255))

    def test_compose_visual_overlay_preview_respects_opacity(self) -> None:
        base = Image.new("RGB", (10, 10), "red")
        overlay = Image.new("RGB", (10, 10), "blue")
        clip = TimelineClip(0.0, 1.0, "image", "Logo", opacity_pct=50.0)

        rendered = _compose_visual_overlay_preview(base, overlay, clip)

        self.assertEqual(rendered.getpixel((5, 5)), (127, 0, 127))

    def test_compose_visual_overlay_preview_can_stack_layers_in_order(self) -> None:
        base = Image.new("RGB", (12, 12), "red")
        lower = Image.new("RGB", (12, 12), "blue")
        upper = Image.new("RGB", (12, 12), "green")

        rendered = _compose_visual_overlay_preview(
            base,
            lower,
            TimelineClip(0.0, 1.0, "image", "Lower", scale_pct=50.0),
        )
        rendered = _compose_visual_overlay_preview(
            rendered,
            upper,
            TimelineClip(0.0, 1.0, "image", "Upper", scale_pct=25.0),
        )

        self.assertEqual(rendered.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(rendered.getpixel((3, 3)), (0, 0, 255))
        self.assertEqual(rendered.getpixel((6, 6)), (0, 128, 0))

    def test_chroma_key_preview_replaces_target_color(self) -> None:
        image = Image.new("RGB", (8, 8), "#00ff00")

        rendered = _apply_chroma_key_preview(image, "#00ff00", 5.0)

        self.assertNotEqual(rendered.getpixel((0, 0)), (0, 255, 0))

    def test_chroma_key_preview_can_composite_over_background(self) -> None:
        image = Image.new("RGB", (8, 8), "#00ff00")
        background = Image.new("RGB", (8, 8), "#112233")

        rendered = _apply_chroma_key_preview(image, "#00ff00", 5.0, background=background)

        self.assertEqual(rendered.getpixel((0, 0)), (17, 34, 51))

    def test_sample_preview_hex_color_uses_display_box(self) -> None:
        image = Image.new("RGB", (4, 4), "#112233")

        self.assertEqual(_sample_preview_hex_color(image, (10, 20, 4, 4), 11, 21), "#112233")
        self.assertIsNone(_sample_preview_hex_color(image, (10, 20, 4, 4), 2, 2))

    def test_preview_control_hit_detects_scale_handle(self) -> None:
        display_box = (10, 20, 100, 50)

        self.assertEqual(_preview_control_handles(display_box)["scale"], (110, 70))
        self.assertEqual(_preview_control_hit(display_box, 108, 68), "scale")
        self.assertIsNone(_preview_control_hit(display_box, 20, 30))
        self.assertIsNone(_preview_control_hit(display_box, 150, 90))

    def test_preview_control_hit_detects_text_handle(self) -> None:
        clip = TimelineClip(0.0, 1.0, "speech", "Intro", text_overlay="Titulo")
        clip.text_position_x_pct = 0.0
        clip.text_position_y_pct = 50.0

        self.assertEqual(_preview_text_anchor(100, 50, 0.0, 50.0), (50, 24))
        self.assertEqual(_preview_control_handles((10, 20, 100, 50), clip)["text"], (60, 44))
        self.assertEqual(_preview_control_hit((10, 20, 100, 50), 60, 44, clip), "text")

    def test_preview_control_hit_detects_text_scale_handle(self) -> None:
        clip = TimelineClip(0.0, 1.0, "text", "Titulo", text_overlay="Titulo")
        display_box = (10, 20, 100, 50)
        _left, _top, right, bottom = _preview_text_display_bounds(display_box, clip)

        handles = _preview_control_handles(display_box, clip)

        self.assertNotIn("scale", handles)
        self.assertEqual(handles["text_scale"], (right, bottom))
        self.assertEqual(_preview_control_hit(display_box, right, bottom, clip), "text_scale")

    def test_preview_visual_display_bounds_follow_scaled_media(self) -> None:
        clip = TimelineClip(
            0.0,
            1.0,
            "image",
            "Logo",
            source_path="C:/media/logo.png",
            scale_pct=50.0,
            position_x_pct=100.0,
            position_y_pct=-100.0,
        )
        display_box = (10, 20, 100, 80)

        self.assertEqual(_preview_visual_display_bounds(display_box, clip), (60, 20, 110, 60))
        self.assertEqual(_preview_control_handles(display_box, clip)["scale"], (110, 60))

    def test_track_control_status_summarizes_preview_layers(self) -> None:
        self.assertEqual(
            _track_control_status(False, True, True),
            "Tracks: visual oculto | texto ativo | audio mutado.",
        )

    def test_preview_text_hit_selects_active_text_bounds(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 10.0)])
        clip = TimelineClip(1.0, 5.0, "text", "Titulo", text_overlay="Titulo")
        model.text_track.clips = [clip]
        left, top, right, bottom = _preview_text_display_bounds((10, 20, 100, 50), clip)

        self.assertEqual(_preview_text_clip_hit(model, 2.0, (10, 20, 100, 50), left + 1, top + 1), 0)
        self.assertIsNone(_preview_text_clip_hit(model, 6.0, (10, 20, 100, 50), left + 1, top + 1))
        self.assertIsNone(_preview_text_clip_hit(model, 2.0, (10, 20, 100, 50), right + 20, bottom + 20))

    def test_preview_base_image_is_black_in_removed_timeline_gap(self) -> None:
        image = Image.new("RGB", (4, 4), "white")
        model = build_timeline_model(10.0, [(1.0, 3.0)])

        rendered = _preview_base_image_for_timeline(image, model, None)

        self.assertEqual(rendered.getpixel((0, 0)), (0, 0, 0))
        self.assertIs(_preview_base_image_for_timeline(image, model, model.video_track.clips[0]), image)

    def test_hex_color_helpers_normalize_invalid_values(self) -> None:
        self.assertEqual(_normalize_hex_color("00FF00"), "#00ff00")
        self.assertEqual(_normalize_hex_color("oops"), "#00ff00")
        self.assertEqual(_hex_to_rgb("#112233"), (17, 34, 51))

    def test_preview_settings_request_token_separates_stale_callbacks(self) -> None:
        first = PreviewSettings(ColorGrade(enabled=False), 0.0, request_token=("playback", 1, 10))
        second = PreviewSettings(ColorGrade(enabled=False), 0.0, request_token=("playback", 2, 10))

        self.assertNotEqual(first.cache_key(), second.cache_key())

    def test_fit_preview_image_keeps_aspect_ratio(self) -> None:
        image = Image.new("RGB", (1920, 1080), "black")

        resized = _fit_preview_image(image, 800, 600)

        self.assertEqual(resized.size, (800, 450))

    def test_fit_preview_image_never_returns_zero_dimensions(self) -> None:
        image = Image.new("RGB", (100, 100), "black")

        resized = _fit_preview_image(image, 1, 1)

        self.assertEqual(resized.size, (1, 1))

    def test_removed_ranges_from_segments(self) -> None:
        removed = _removed_ranges_from_segments(10.0, [(1.0, 3.0), (5.0, 8.0)])

        self.assertEqual(removed, [(0.0, 1.0), (3.0, 5.0), (8.0, 10.0)])

    def test_playback_delay_never_goes_below_one_ms(self) -> None:
        self.assertEqual(_playback_delay_ms(30.0, 100.0), 1)
        self.assertGreaterEqual(_playback_delay_ms(30.0, 5.0), 1)

    def test_playback_target_skips_frames_when_late(self) -> None:
        self.assertEqual(_playback_target_frame(10, 0.50, 30.0, 1000), 26)
        self.assertEqual(_playback_target_frame(995, 1.0, 30.0, 1000), 999)

    def test_playback_effective_fps_is_stable_for_zero_elapsed(self) -> None:
        self.assertEqual(_playback_effective_fps(10, 30, 0), 0.0)
        self.assertAlmostEqual(_playback_effective_fps(10, 40, 1.5), 20.0)

    def test_playback_detects_cut_jump_for_audio_resync(self) -> None:
        segments = [(1.0, 3.0), (6.0, 9.0)]

        self.assertTrue(_playback_crosses_removed_range(89, 180, 30.0, segments, 10.0))
        self.assertFalse(_playback_crosses_removed_range(45, 60, 30.0, segments, 10.0))
        self.assertFalse(_playback_crosses_removed_range(180, 181, 30.0, segments, 10.0))

    def test_timeline_click_math_uses_track_area_not_label_area(self) -> None:
        x1, x2 = _timeline_track_bounds(1000)

        self.assertEqual((x1, x2), (74, 992))
        self.assertEqual(_timeline_x_to_time(0, 60.0, x1, x2), 0.0)
        self.assertAlmostEqual(_timeline_x_to_time((x1 + x2) // 2, 60.0, x1, x2), 30.0, delta=0.1)
        self.assertEqual(_timeline_time_to_x(60.0, 60.0, x1, x2), x2)

    def test_timeline_handle_hitbox_is_easy_to_grab_near_clip_edges(self) -> None:
        self.assertEqual(_timeline_handle_edge_at(102, 100, 220, 14), "start")
        self.assertEqual(_timeline_handle_edge_at(214, 100, 220, 14), "end")
        self.assertEqual(_timeline_handle_edge_at(126, 100, 220, 14), "start")
        self.assertEqual(_timeline_handle_edge_at(194, 100, 220, 14), "end")
        self.assertIsNone(_timeline_handle_edge_at(160, 100, 220, 14))

    def test_point_inside_rect_supports_media_drag_drop_hit_testing(self) -> None:
        self.assertTrue(_point_inside_rect(10, 10, 0, 0, 20, 20))
        self.assertFalse(_point_inside_rect(20, 10, 0, 0, 20, 20))

    def test_timeline_zoom_window_keeps_margin_around_playhead(self) -> None:
        start, end = _timeline_zoom_window(120.0, 4.0, 60.0)

        self.assertLess(start, 60.0)
        self.assertGreater(end, 60.0)
        self.assertLess(end - start, 120.0)
        self.assertGreater(end - start, 30.0)

    def test_timeline_zoom_window_can_zoom_out_with_empty_margins(self) -> None:
        start, end = _timeline_zoom_window(120.0, 0.5, 60.0)

        self.assertEqual((start, end), (-60.0, 180.0))
        self.assertEqual(_timeline_view_time_to_x(0.0, start, end, 100, 700), 250)
        self.assertEqual(_timeline_view_time_to_x(120.0, start, end, 100, 700), 550)

    def test_timeline_zoom_step_clamps_to_editor_limits(self) -> None:
        self.assertEqual(_timeline_zoom_step(1.0, 0.25), 1.25)
        self.assertEqual(_timeline_zoom_step(0.5, -2.0), 0.5)
        self.assertEqual(_timeline_zoom_step(8.0, 2.0), 8.0)

    def test_timeline_pan_center_moves_by_visible_window_and_clamps(self) -> None:
        self.assertAlmostEqual(_timeline_pan_center(60.0, 120.0, 4.0, 1), 70.5)
        self.assertEqual(_timeline_pan_center(2.0, 120.0, 4.0, -1), 0.0)
        self.assertEqual(_timeline_pan_center(118.0, 120.0, 4.0, 1), 120.0)

    def test_timeline_view_coordinate_mapping_is_reversible(self) -> None:
        x = _timeline_view_time_to_x(45.0, 30.0, 60.0, 100, 700)

        self.assertEqual(x, 400)
        self.assertAlmostEqual(_timeline_x_to_view_time(x, 30.0, 60.0, 100, 700), 45.0)

    def test_time_to_frame_clamps_to_video_range(self) -> None:
        self.assertEqual(_time_to_frame(1.0, 30.0, 100), 30)
        self.assertEqual(_time_to_frame(99.0, 30.0, 100), 99)

    def test_compact_timeline_maps_kept_clips_without_gaps(self) -> None:
        clips = [
            TimelineClip(1.0, 3.0, "speech", "Clip 1"),
            TimelineClip(6.0, 9.0, "speech", "Clip 2"),
        ]

        ranges = _compact_clip_ranges(clips)

        self.assertEqual(ranges, [(1.0, 3.0, 0.0, 2.0), (6.0, 9.0, 2.0, 5.0)])
        self.assertEqual(_compact_display_to_source_time(2.5, ranges), 6.5)
        self.assertEqual(_compact_source_to_display_time(7.0, ranges), 3.0)

    def test_clip_insert_index_places_media_clip_after_current_range(self) -> None:
        clips = [
            TimelineClip(1.0, 3.0, "speech", "Clip 1"),
            TimelineClip(6.0, 9.0, "speech", "Clip 2"),
        ]

        self.assertEqual(_clip_insert_index(clips, 0.5), 0)
        self.assertEqual(_clip_insert_index(clips, 2.0), 1)
        self.assertEqual(_clip_insert_index(clips, 5.0), 1)
        self.assertEqual(_clip_insert_index(clips, 10.0), 2)

    def test_insert_media_clip_replaces_range_and_preserves_edges(self) -> None:
        clips = [TimelineClip(0.0, 10.0, "speech", "Principal")]

        updated, selected = _insert_media_clip_replacing_range(
            clips,
            "C:/media/broll.mp4",
            start_s=4.0,
            duration_s=10.0,
            clip_duration_s=3.0,
            min_duration_s=0.15,
        )

        self.assertEqual(selected, 1)
        self.assertEqual([(c.start_s, c.end_s, c.source_path) for c in updated], [
            (0.0, 4.0, ""),
            (4.0, 7.0, "C:/media/broll.mp4"),
            (7.0, 10.0, ""),
        ])
        self.assertEqual(updated[1].clip_type, "media")

    def test_insert_media_clip_uses_configured_duration(self) -> None:
        clips = [TimelineClip(0.0, 12.0, "speech", "Principal")]

        updated, selected = _insert_media_clip_replacing_range(
            clips,
            "C:/media/broll.mp4",
            start_s=2.0,
            duration_s=12.0,
            clip_duration_s=6.0,
            min_duration_s=0.15,
        )

        self.assertEqual(selected, 1)
        self.assertEqual((updated[1].start_s, updated[1].end_s), (2.0, 8.0))

    def test_insert_image_clip_uses_image_type_and_timeline_fill(self) -> None:
        clips = [TimelineClip(0.0, 8.0, "speech", "Principal")]

        updated, selected = _insert_media_clip_replacing_range(
            clips,
            "C:/media/capa.png",
            start_s=1.0,
            duration_s=8.0,
            clip_duration_s=2.0,
            min_duration_s=0.15,
        )

        self.assertEqual(selected, 1)
        self.assertEqual(updated[1].clip_type, "image")
        self.assertEqual(_timeline_clip_fill(updated[1]), "#b77a2d")

        model = build_timeline_model(8.0, [(0.0, 8.0)])
        model.video_track.clips = updated
        options = _clip_options_from_timeline_model(model)
        restored = build_timeline_model(8.0, [(0.0, 1.0), (1.0, 3.0), (3.0, 8.0)])
        _apply_clip_options_to_timeline_model(restored, options)
        self.assertEqual(restored.video_track.clips[1].clip_type, "image")

    def test_overlay_track_round_trips_without_replacing_base_video(self) -> None:
        model = build_timeline_model(10.0, [(0.0, 10.0)])
        model.overlay_track.clips.append(
            TimelineClip(2.0, 5.0, "image", "Logo", source_path="C:/media/logo.png")
        )

        options = _clip_options_from_timeline_model(model)
        restored = build_timeline_model(10.0, [(0.0, 10.0)])
        _apply_clip_options_to_timeline_model(restored, options)

        self.assertEqual(options[0]["layer"], "base")
        self.assertEqual(options[1]["layer"], "overlay")
        self.assertEqual(len(restored.video_track.clips), 1)
        self.assertEqual(restored.video_track.clips[0].clip_type, "speech")
        self.assertEqual(len(restored.overlay_track.clips), 1)
        self.assertEqual(restored.overlay_track.clips[0].source_path, "C:/media/logo.png")

    def test_insert_media_clip_can_add_same_source_multiple_times(self) -> None:
        clips = [TimelineClip(0.0, 10.0, "speech", "Principal")]

        first, _selected = _insert_media_clip_replacing_range(clips, "C:/media/broll.mp4", 2.0, 10.0)
        second, selected = _insert_media_clip_replacing_range(first, "C:/media/broll.mp4", 7.0, 10.0)

        self.assertIsNotNone(selected)
        self.assertEqual(sum(1 for clip in second if clip.source_path == "C:/media/broll.mp4"), 2)

    def test_paste_clip_at_time_preserves_duration_and_clamps_to_timeline(self) -> None:
        clip = TimelineClip(2.0, 5.0, "text", "Titulo", text_overlay="Titulo")

        pasted = _paste_clip_at_time(clip, 4.0, 10.0, 0.15)

        self.assertIsNotNone(pasted)
        self.assertEqual((pasted.start_s, pasted.end_s), (4.0, 7.0))
        self.assertEqual(pasted.text_overlay, "Titulo")
        self.assertEqual(pasted.label, "Titulo copia")

        pasted = _paste_clip_at_time(clip, 9.5, 10.0, 0.15)

        self.assertIsNotNone(pasted)
        self.assertEqual((pasted.start_s, pasted.end_s), (7.0, 10.0))

    def test_split_clip_at_time_preserves_properties(self) -> None:
        clip = TimelineClip(
            2.0,
            8.0,
            "image",
            "Logo",
            source_path="C:/media/logo.png",
            scale_pct=75.0,
            opacity_pct=50.0,
        )

        pieces = _split_clip_at_time(clip, 5.0, 0.15)

        self.assertIsNotNone(pieces)
        left, right = pieces
        self.assertEqual((left.start_s, left.end_s), (2.0, 5.0))
        self.assertEqual((right.start_s, right.end_s), (5.0, 8.0))
        self.assertEqual(right.source_path, "C:/media/logo.png")
        self.assertEqual(right.scale_pct, 75.0)
        self.assertEqual(right.opacity_pct, 50.0)
        self.assertEqual(right.label, "Logo 2")
        self.assertIsNone(_split_clip_at_time(clip, 2.05, 0.15))

    def test_snap_time_to_edges_uses_threshold(self) -> None:
        clips = [
            TimelineClip(1.0, 3.0, "speech", "Clip 1"),
            TimelineClip(6.0, 9.0, "speech", "Clip 2"),
        ]

        edges = _clip_edges(clips)

        self.assertEqual(_snap_time_to_edges(2.96, edges, 0.08), 3.0)
        self.assertEqual(_snap_time_to_edges(3.20, edges, 0.08), 3.20)

    def test_snap_time_to_edges_reports_when_it_changed_time(self) -> None:
        snapped_time, snapped = _snap_time_to_edges_with_flag(2.96, [1.0, 3.0], 0.08)
        self.assertEqual(snapped_time, 3.0)
        self.assertTrue(snapped)

        unsnapped_time, snapped = _snap_time_to_edges_with_flag(2.80, [1.0, 3.0], 0.08)
        self.assertEqual(unsnapped_time, 2.80)
        self.assertFalse(snapped)

    def test_move_clip_bounds_with_snap_aligns_start_or_end_to_edges(self) -> None:
        (start_s, end_s), snapped = _move_clip_bounds_with_snap(
            0.0,
            2.0,
            2.96,
            10.0,
            [3.0, 6.0],
            0.08,
        )

        self.assertTrue(snapped)
        self.assertEqual((start_s, end_s), (3.0, 5.0))

        (start_s, end_s), snapped = _move_clip_bounds_with_snap(
            0.0,
            2.0,
            3.96,
            10.0,
            [3.0, 6.0],
            0.08,
        )

        self.assertTrue(snapped)
        self.assertEqual((start_s, end_s), (4.0, 6.0))

    def test_snap_insert_start_for_duration_aligns_overlay_start_or_end(self) -> None:
        start_s, snapped = _snap_insert_start_for_duration(2.96, 2.0, 10.0, [3.0, 6.0], 0.08)

        self.assertTrue(snapped)
        self.assertEqual(start_s, 3.0)

        start_s, snapped = _snap_insert_start_for_duration(3.96, 2.0, 10.0, [3.0, 6.0], 0.08)

        self.assertTrue(snapped)
        self.assertEqual(start_s, 4.0)

    def test_coerce_time_to_segments_skips_removed_gaps(self) -> None:
        segments = [(1.0, 3.0), (6.0, 9.0)]

        self.assertEqual(_coerce_time_to_segments(2.0, segments, 10.0), 2.0)
        self.assertEqual(_coerce_time_to_segments(4.0, segments, 10.0), 6.0)
        self.assertEqual(_coerce_time_to_segments(9.5, segments, 10.0), 9.0)
        self.assertEqual(_coerce_time_to_segments(0.2, segments, 10.0), 1.0)

    def test_coerce_frame_to_segments_skips_removed_gap(self) -> None:
        segments = [(1.0, 3.0), (6.0, 9.0)]

        self.assertEqual(_coerce_frame_to_segments(120, 30.0, 300, segments, 10.0), 180)


if __name__ == "__main__":
    unittest.main()
