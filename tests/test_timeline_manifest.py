import unittest

from src.core.timeline_manifest import build_timeline_manifest
from src.core.timeline_model import TimelineClip, TimelineModel, TimelineTrack


class TimelineManifestTests(unittest.TestCase):
    def test_manifest_uses_external_media_refs_and_compact_output_time(self) -> None:
        model = TimelineModel(
            duration_s=10.0,
            video_track=TimelineTrack(
                "Video",
                [
                    TimelineClip(1.0, 3.0, "speech", "Intro"),
                    TimelineClip(6.0, 9.0, "speech", "B-roll", source_path="C:/media/broll.mp4"),
                    TimelineClip(9.0, 10.0, "image", "Capa", source_path="C:/media/capa.png"),
                ],
            ),
            audio_track=TimelineTrack("Audio"),
            removed_ranges=[(0.0, 1.0), (3.0, 6.0), (9.0, 10.0)],
            waveform=[],
            saved_time_s=5.0,
        )

        manifest = build_timeline_manifest(model, "Canal", "C:/media/main.mp4")

        self.assertEqual(manifest["schema"], "cortacerto.timeline.v1")
        self.assertEqual([media["target_url"] for media in manifest["media"]], ["C:/media/main.mp4", "C:/media/broll.mp4", "C:/media/capa.png"])
        self.assertEqual([media["kind"] for media in manifest["media"]], ["video", "video", "image"])
        video_clips = manifest["tracks"][0]["clips"]
        self.assertEqual(video_clips[0]["output_start_s"], 0.0)
        self.assertEqual(video_clips[0]["output_end_s"], 2.0)
        self.assertEqual(video_clips[1]["output_start_s"], 2.0)
        self.assertEqual(video_clips[1]["output_end_s"], 5.0)
        self.assertEqual(video_clips[1]["media_id"], "media-0002")
        self.assertEqual(video_clips[2]["kind"], "image")

    def test_manifest_exports_clip_and_audio_effect_scopes(self) -> None:
        clip = TimelineClip(
            0.0,
            4.0,
            "speech",
            "Abertura",
            scale_pct=125.0,
            volume_pct=70.0,
            transition="Fade",
            text_overlay="Titulo",
            text_position_x_pct=10.0,
            text_position_y_pct=40.0,
            text_size_pct=120.0,
            text_color="#ffee11",
            text_background_enabled=False,
            text_background_color="#112233",
            chroma_enabled=True,
            chroma_color="#00ff00",
            chroma_tolerance=55.0,
            position_x_pct=12.0,
            position_y_pct=-8.0,
        )
        model = TimelineModel(
            duration_s=4.0,
            video_track=TimelineTrack("Video", [clip]),
            audio_track=TimelineTrack("Audio"),
            removed_ranges=[],
            waveform=[],
            saved_time_s=0.0,
            text_track=TimelineTrack(
                "Texto",
                [
                    TimelineClip(
                        0.0,
                        4.0,
                        "text",
                        "Titulo",
                        text_overlay="Titulo",
                        text_position_x_pct=10.0,
                        text_position_y_pct=40.0,
                        text_size_pct=120.0,
                        text_color="#ffee11",
                        text_background_enabled=False,
                        text_background_color="#112233",
                    )
                ],
            ),
        )

        manifest = build_timeline_manifest(model, "Canal", "C:/media/main.mp4")

        video_effects = manifest["tracks"][0]["clips"][0]["effects"]
        audio_effects = manifest["tracks"][1]["clips"][0]["effects"]
        text_effects = manifest["tracks"][2]["clips"][0]["effects"]
        self.assertEqual([effect["type"] for effect in video_effects], ["transform", "text", "chroma_key", "transition"])
        self.assertEqual(video_effects[0]["position_x_pct"], 12.0)
        self.assertEqual(video_effects[0]["position_y_pct"], -8.0)
        self.assertEqual(video_effects[1]["position_x_pct"], 10.0)
        self.assertEqual(video_effects[1]["position_y_pct"], 40.0)
        self.assertEqual(video_effects[1]["size_pct"], 120.0)
        self.assertEqual(video_effects[1]["color"], "#ffee11")
        self.assertFalse(video_effects[1]["background_enabled"])
        self.assertEqual(video_effects[1]["background_color"], "#112233")
        self.assertEqual(audio_effects, [{"type": "volume", "volume_pct": 70.0}])
        self.assertEqual(manifest["tracks"][2]["kind"], "text")
        self.assertEqual(text_effects[0]["text"], "Titulo")
        self.assertEqual(text_effects[0]["color"], "#ffee11")
        self.assertFalse(text_effects[0]["background_enabled"])


if __name__ == "__main__":
    unittest.main()
