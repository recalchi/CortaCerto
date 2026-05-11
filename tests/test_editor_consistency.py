import unittest

from src.core.timeline_model import TimelineClip
from src.ui.app import (
    _active_timeline_handle_edge,
    _coerce_frame_to_segments,
    _coerce_time_to_segments,
    _compact_clip_ranges,
    _compact_display_to_source_time,
    _compact_source_to_display_time,
    _playback_target_frame,
    _timeline_time_to_x,
    _timeline_track_bounds,
    _timeline_x_to_time,
    _timeline_handle_edge_at,
    _timeline_handle_y_in_range,
    _trim_bounds_changed,
    _trim_clip_bounds,
    _trim_edge_label,
    _waveform_indices_for_time_range,
)


class EditorConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.duration_s = 12.0
        self.fps = 30.0
        self.total_frames = 360
        self.clips = [
            TimelineClip(0.0, 2.0, "speech", "Clip 1"),
            TimelineClip(5.0, 7.0, "speech", "Clip 2"),
            TimelineClip(9.0, 11.0, "speech", "Clip 3"),
        ]
        self.segments = [(clip.start_s, clip.end_s) for clip in self.clips]

    def test_playback_target_never_lands_inside_deleted_gap(self) -> None:
        raw_target = _playback_target_frame(50, 2.0, self.fps, self.total_frames)

        coerced = _coerce_frame_to_segments(
            raw_target,
            self.fps,
            self.total_frames,
            self.segments,
            self.duration_s,
        )

        self.assertEqual(raw_target, 111)
        self.assertEqual(coerced, 150)
        self.assertEqual(coerced / self.fps, 5.0)

    def test_deleted_clip_reanchors_playhead_to_next_kept_segment(self) -> None:
        self.assertEqual(_coerce_time_to_segments(3.5, self.segments, self.duration_s), 5.0)
        self.assertEqual(_coerce_time_to_segments(8.0, self.segments, self.duration_s), 9.0)
        self.assertEqual(_coerce_time_to_segments(11.5, self.segments, self.duration_s), 11.0)

    def test_compact_timeline_roundtrip_stays_on_source_time(self) -> None:
        ranges = _compact_clip_ranges(self.clips)

        for source_time in (0.0, 1.5, 5.5, 6.9, 9.25, 10.8):
            display_time = _compact_source_to_display_time(source_time, ranges)
            mapped_source = _compact_display_to_source_time(display_time, ranges)
            self.assertAlmostEqual(mapped_source, source_time, places=6)

    def test_compact_click_mapping_does_not_point_to_removed_gap(self) -> None:
        ranges = _compact_clip_ranges(self.clips)
        x1, x2 = _timeline_track_bounds(1000)
        compact_duration = ranges[-1][3]

        gapless_mid_clip_x = _timeline_time_to_x(2.5, compact_duration, x1, x2)
        display_time = _timeline_x_to_time(gapless_mid_clip_x, compact_duration, x1, x2)
        source_time = _compact_display_to_source_time(display_time, ranges)

        self.assertGreaterEqual(source_time, 5.0)
        self.assertLessEqual(source_time, 7.0)

    def test_trim_start_respects_previous_clip_and_min_duration(self) -> None:
        start, end = _trim_clip_bounds(self.clips, 1, "start", 1.0, self.duration_s, 0.15)
        self.assertEqual((start, end), (2.0, 7.0))

        start, end = _trim_clip_bounds(self.clips, 1, "start", 6.95, self.duration_s, 0.15)
        self.assertEqual((start, end), (6.85, 7.0))

    def test_trim_end_respects_next_clip_and_min_duration(self) -> None:
        start, end = _trim_clip_bounds(self.clips, 1, "end", 10.0, self.duration_s, 0.15)
        self.assertEqual((start, end), (5.0, 9.0))

        start, end = _trim_clip_bounds(self.clips, 1, "end", 5.01, self.duration_s, 0.15)
        self.assertEqual((start, end), (5.0, 5.15))

    def test_timeline_handle_detection_prefers_visible_edges(self) -> None:
        self.assertEqual(_timeline_handle_edge_at(102, 100, 200, 8), "start")
        self.assertEqual(_timeline_handle_edge_at(196, 100, 200, 8), "end")
        self.assertIsNone(_timeline_handle_edge_at(150, 100, 200, 8))

    def test_timeline_handle_y_range_covers_video_and_audio_tracks(self) -> None:
        self.assertTrue(_timeline_handle_y_in_range(24, 20, 48, 64, 104))
        self.assertTrue(_timeline_handle_y_in_range(82, 20, 48, 64, 104))
        self.assertFalse(_timeline_handle_y_in_range(56, 20, 48, 70, 104))
        self.assertFalse(_timeline_handle_y_in_range(116, 20, 48, 64, 104))

    def test_active_timeline_handle_prefers_drag_then_hover_then_selection(self) -> None:
        self.assertEqual(_active_timeline_handle_edge(1, 1, None, None), "both")
        self.assertEqual(_active_timeline_handle_edge(1, 1, None, (1, "start")), "start")
        self.assertEqual(_active_timeline_handle_edge(1, 1, (1, "end"), (1, "start")), "end")
        self.assertIsNone(_active_timeline_handle_edge(2, 1, None, (1, "start")))

    def test_trim_edge_label_is_user_facing(self) -> None:
        self.assertEqual(_trim_edge_label("start"), "borda inicial")
        self.assertEqual(_trim_edge_label("end"), "borda final")

    def test_waveform_indices_follow_source_clip_time(self) -> None:
        self.assertEqual(_waveform_indices_for_time_range(100, 10.0, 2.0, 5.0), (20, 50))
        self.assertEqual(_waveform_indices_for_time_range(100, 10.0, -1.0, 1.0), (0, 10))
        self.assertEqual(_waveform_indices_for_time_range(100, 10.0, 9.8, 20.0), (98, 100))
        self.assertEqual(_waveform_indices_for_time_range(100, 10.0, 5.0, 5.0), (0, 0))

    def test_trim_undo_is_needed_only_when_bounds_change(self) -> None:
        self.assertFalse(_trim_bounds_changed(5.0, 7.0, 5.0, 7.0))
        self.assertFalse(_trim_bounds_changed(5.0, 7.0, 5.0 + 1e-8, 7.0))
        self.assertTrue(_trim_bounds_changed(5.0, 7.0, 5.02, 7.0))
        self.assertTrue(_trim_bounds_changed(5.0, 7.0, 5.0, 6.98))


if __name__ == "__main__":
    unittest.main()
