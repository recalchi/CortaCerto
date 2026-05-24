"""Tests for build_timeline_model — verifies that audio↔video clips are created
as PAIRS sharing the same start/end times, which is what the frontend's link
detection relies on (getLinkedClipIds matches by source_path + time range).
"""
import unittest

from src.core.timeline_model import build_timeline_model


class TimelineModelTests(unittest.TestCase):
    def test_paired_audio_video_clips_have_matching_times(self) -> None:
        model = build_timeline_model(
            duration_s=30.0,
            speech_segments=[(0.0, 5.0), (10.0, 20.0)],
        )
        self.assertEqual(len(model.video_track.clips), 2)
        self.assertEqual(len(model.audio_track.clips), 2)
        for v, a in zip(model.video_track.clips, model.audio_track.clips):
            # Frontend pairs clips by matching start/end (tolerance 0.05s).
            # If these ever drift apart, audio/video link breaks silently.
            self.assertAlmostEqual(v.start_s, a.start_s, places=3)
            self.assertAlmostEqual(v.end_s,   a.end_s,   places=3)

    def test_no_clips_when_no_segments(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[])
        self.assertEqual(model.video_track.clips, [])
        self.assertEqual(model.audio_track.clips, [])

    def test_removed_ranges_fill_gaps_around_segments(self) -> None:
        model = build_timeline_model(
            duration_s=30.0,
            speech_segments=[(5.0, 10.0), (15.0, 20.0)],
        )
        # Expected silence ranges: 0-5, 10-15, 20-30
        self.assertEqual(model.removed_ranges, [(0.0, 5.0), (10.0, 15.0), (20.0, 30.0)])

    def test_saved_time_equals_total_silence_duration(self) -> None:
        model = build_timeline_model(
            duration_s=30.0,
            speech_segments=[(5.0, 10.0), (15.0, 20.0)],
        )
        # 5s + 5s + 10s = 20s of removed silence
        self.assertAlmostEqual(model.saved_time_s, 20.0, places=3)


class MultiTrackTests(unittest.TestCase):
    """Phase 2b: extra parallel video/audio tracks for layered content."""

    def test_new_model_starts_with_no_extra_tracks(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[(0.0, 5.0)])
        self.assertEqual(model.extra_video_tracks, [])
        self.assertEqual(model.extra_audio_tracks, [])

    def test_add_video_track_appends_to_extras(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[(0.0, 5.0)])
        track = model.add_video_track()
        self.assertEqual(len(model.extra_video_tracks), 1)
        self.assertIs(model.extra_video_tracks[0], track)
        self.assertEqual(track.clips, [])

    def test_add_video_track_default_names_are_sequential(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[])
        model.add_video_track()
        model.add_video_track()
        # Names: base track is "Vídeo 1" (implicit), so extras start at 2
        self.assertEqual(model.extra_video_tracks[0].name, "Vídeo 2")
        self.assertEqual(model.extra_video_tracks[1].name, "Vídeo 3")

    def test_add_audio_track_appends_to_extras(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[])
        model.add_audio_track()
        self.assertEqual(len(model.extra_audio_tracks), 1)

    def test_remove_video_track_by_index(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[])
        model.add_video_track()
        model.add_video_track()
        self.assertTrue(model.remove_video_track(0))
        self.assertEqual(len(model.extra_video_tracks), 1)

    def test_remove_video_track_out_of_range_returns_false(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[])
        self.assertFalse(model.remove_video_track(0))   # no extras to remove
        self.assertFalse(model.remove_video_track(-1))

    def test_all_video_tracks_returns_main_plus_extras_in_order(self) -> None:
        model = build_timeline_model(duration_s=10.0, speech_segments=[(0.0, 5.0)])
        extra = model.add_video_track()
        all_tracks = model.all_video_tracks()
        self.assertEqual(len(all_tracks), 2)
        self.assertIs(all_tracks[0], model.video_track)
        self.assertIs(all_tracks[1], extra)


if __name__ == "__main__":
    unittest.main()
