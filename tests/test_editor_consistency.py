import unittest

from src.core.timeline_model import TimelineClip
from src.ui.app import (
    _coerce_frame_to_segments,
    _coerce_time_to_segments,
    _compact_clip_ranges,
    _compact_display_to_source_time,
    _compact_source_to_display_time,
    _playback_target_frame,
    _timeline_time_to_x,
    _timeline_track_bounds,
    _timeline_x_to_time,
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


if __name__ == "__main__":
    unittest.main()
