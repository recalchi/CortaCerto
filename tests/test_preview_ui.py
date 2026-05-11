import unittest

from PIL import Image

from src.ui.app import (
    _clip_edges,
    _compact_clip_ranges,
    _compact_display_to_source_time,
    _compact_source_to_display_time,
    _fit_preview_image,
    _playback_delay_ms,
    _playback_effective_fps,
    _playback_target_frame,
    _removed_ranges_from_segments,
    _snap_time_to_edges,
    _time_to_frame,
    _timeline_time_to_x,
    _timeline_track_bounds,
    _timeline_x_to_time,
)
from src.core.timeline_model import TimelineClip


class PreviewUiTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
