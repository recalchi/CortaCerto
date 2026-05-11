import unittest
import tempfile
import time
from pathlib import Path

from PIL import Image

from src.core.color_grade import ColorGrade
from src.core.preview_engine import PreviewSettings
from src.ui.app import (
    _apply_segments_to_timeline_model,
    _build_project_metadata,
    _clip_edges,
    _cleanup_project_trash,
    _coerce_frame_to_segments,
    _coerce_time_to_segments,
    _compact_clip_ranges,
    _compact_display_to_source_time,
    _compact_source_to_display_time,
    _fit_preview_image,
    _first_video_path_from_drop,
    _is_video_path,
    _move_project_to_trash,
    _playback_delay_ms,
    _playback_effective_fps,
    _playback_crosses_removed_range,
    _playback_target_frame,
    _project_name_from_path,
    _project_segments_from_metadata,
    _project_state_payload,
    _project_trash_dir,
    _read_project_metadata,
    _removed_ranges_from_segments,
    _restore_project_from_trash,
    _safe_project_slug,
    _snap_time_to_edges,
    _snap_time_to_edges_with_flag,
    _split_drop_paths,
    _time_to_frame,
    _timeline_time_to_x,
    _timeline_track_bounds,
    _timeline_x_to_time,
)
from src.core.timeline_model import TimelineClip, build_timeline_model


class PreviewUiTests(unittest.TestCase):
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
        self.assertIn("created_at", metadata)

    def test_read_project_metadata_merges_existing_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canal.ccp"
            path.write_text('{"name":"Canal","video_path":"C:/video.mp4"}', encoding="utf-8")

            metadata = _read_project_metadata(str(path))

        self.assertEqual(metadata["name"], "Canal")
        self.assertEqual(metadata["slug"], "canal")
        self.assertEqual(metadata["video_path"], "C:/video.mp4")

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
        self.assertFalse(_is_video_path("C:/midias/audio.mp3"))
        self.assertEqual(
            _first_video_path_from_drop("{C:/midias/audio.mp3} {C:/midias/video final.mp4}"),
            "C:/midias/video final.mp4",
        )

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
        )

        self.assertEqual(payload["video_path"], "C:/video.mp4")
        self.assertEqual(payload["current_time_s"], 12.5)
        self.assertEqual(payload["timeline_segments"], [
            {"start_s": 1.0, "end_s": 3.0},
            {"start_s": 6.0, "end_s": 8.0},
        ])
        self.assertTrue(payload["timeline_dirty"])

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
