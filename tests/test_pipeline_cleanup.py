import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import ProcessingConfig
from src.pipeline import _cleanup_intermediate_exports, _normalize_manual_segments, run_pipeline


class PipelineCleanupTests(unittest.TestCase):
    def test_cleanup_intermediate_exports_removes_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "intermediate.mp4"
            path.write_bytes(b"tmp")

            _cleanup_intermediate_exports({str(path)})

            self.assertFalse(path.exists())

    def test_normalize_manual_segments_clamps_and_drops_invalid_ranges(self) -> None:
        segments = [(-1.0, 2.0), (4.0, 3.0), (8.0, 20.0)]

        normalized = _normalize_manual_segments(segments, duration_s=10.0)

        self.assertEqual(normalized, [(0.0, 2.0), (8.0, 10.0)])

    def test_pipeline_skips_audio_analysis_when_silence_cut_is_disabled(self) -> None:
        config = ProcessingConfig(
            remove_silence=False,
            generate_thumbnail=False,
            generate_vertical=False,
            noise_reduction=False,
        )
        config.color_grade.enabled = False

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("src.pipeline.get_video_duration", return_value=12.0), \
                mock.patch("src.pipeline.detect_video_encoder", return_value=("libx264", [])), \
                mock.patch("src.pipeline.analyze_video") as analyze:
            result = run_pipeline("input.mp4", tmp, config)

        analyze.assert_not_called()
        self.assertTrue(result.success)
        self.assertEqual(result.analysis.speech_segments, [(0.0, 12.0)])

    def test_pipeline_skips_audio_analysis_when_manual_timeline_is_available(self) -> None:
        config = ProcessingConfig(
            remove_silence=True,
            generate_thumbnail=False,
            generate_vertical=False,
            noise_reduction=False,
            manual_segments=[(1.0, 3.0)],
        )
        config.color_grade.enabled = False

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("src.pipeline.get_video_duration", return_value=10.0), \
                mock.patch("src.pipeline.detect_video_encoder", return_value=("libx264", [])), \
                mock.patch("src.pipeline.analyze_video") as analyze, \
                mock.patch("src.pipeline.cut_silence") as cut:
            cut.return_value.encoder_used = "libx264"
            result = run_pipeline("input.mp4", tmp, config)

        analyze.assert_not_called()
        cut.assert_called_once()
        self.assertTrue(result.success)
        self.assertEqual(result.analysis.speech_segments, [(1.0, 3.0)])


if __name__ == "__main__":
    unittest.main()
