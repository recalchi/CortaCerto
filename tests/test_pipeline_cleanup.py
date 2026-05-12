import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import ProcessingConfig
from src.pipeline import (
    _build_clip_volume_filter,
    _clip_options_with_output_ranges,
    _clip_volume_adjustments,
    _has_clip_audio_adjustments,
    _has_clip_source_replacements,
    _clip_option_plan,
    _cleanup_intermediate_exports,
    _export_output_plan,
    _finalize_project_output,
    _normalize_manual_segments,
    run_pipeline,
)


class PipelineCleanupTests(unittest.TestCase):
    def test_default_export_outputs_only_final_video(self) -> None:
        config = ProcessingConfig()

        self.assertFalse(config.generate_thumbnail)
        self.assertFalse(config.generate_vertical)
        self.assertEqual(_export_output_plan(config), "vídeo final")

    def test_export_output_plan_lists_explicit_extras(self) -> None:
        config = ProcessingConfig(generate_thumbnail=True, generate_vertical=True, thumbnail_count=5)

        self.assertEqual(_export_output_plan(config), "vídeo final, versão vertical, 5 thumbnails")

    def test_clip_option_plan_summarizes_editor_adjustments(self) -> None:
        plan = _clip_option_plan([
            {"scale_pct": 125.0, "volume_pct": 80.0, "transition": "Fade", "text_overlay": "Intro", "chroma_enabled": True},
            {"scale_pct": 100.0, "volume_pct": 100.0, "transition": "Corte", "text_overlay": ""},
        ])

        self.assertIn("escala em 1 clipe(s)", plan)
        self.assertIn("volume em 1 clipe(s)", plan)
        self.assertIn("texto em 1 clipe(s)", plan)
        self.assertIn("chroma em 1 clipe(s)", plan)

    def test_clip_options_with_output_ranges_compacts_manual_timeline(self) -> None:
        options = _clip_options_with_output_ranges(
            [
                {"start_s": 1.0, "end_s": 3.0, "source_path": "broll-a.mp4"},
                {"start_s": 6.0, "end_s": 9.0, "source_path": ""},
            ],
            [(1.0, 3.0), (6.0, 9.0)],
        )

        self.assertEqual(options[0]["output_start_s"], 0.0)
        self.assertEqual(options[0]["output_end_s"], 2.0)
        self.assertEqual(options[1]["output_start_s"], 2.0)
        self.assertEqual(options[1]["output_end_s"], 5.0)
        self.assertTrue(_has_clip_source_replacements(options))

    def test_has_clip_source_replacements_ignores_empty_or_invalid_ranges(self) -> None:
        self.assertFalse(_has_clip_source_replacements([
            {"source_path": "", "output_start_s": 0.0, "output_end_s": 1.0},
            {"source_path": "x.mp4", "output_start_s": 2.0, "output_end_s": 2.0},
        ]))

    def test_clip_volume_filter_targets_adjusted_output_ranges(self) -> None:
        options = [
            {"volume_pct": 80.0, "output_start_s": 0.0, "output_end_s": 2.5},
            {"volume_pct": 100.0, "output_start_s": 2.5, "output_end_s": 5.0},
            {"volume_pct": 130.0, "output_start_s": 5.0, "output_end_s": 8.0},
        ]

        self.assertTrue(_has_clip_audio_adjustments(options))
        self.assertEqual(_clip_volume_adjustments(options), [(0.0, 2.5, 80.0), (5.0, 8.0, 130.0)])
        self.assertEqual(
            _build_clip_volume_filter(options),
            "volume=0.8000:enable='between(t,0.000,2.500)',volume=1.3000:enable='between(t,5.000,8.000)'",
        )

    def test_clip_volume_filter_returns_anull_without_adjustments(self) -> None:
        self.assertFalse(_has_clip_audio_adjustments([{"volume_pct": 100.0, "output_start_s": 0.0, "output_end_s": 1.0}]))
        self.assertEqual(_build_clip_volume_filter([]), "anull")

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

    def test_finalize_project_output_copies_original_instead_of_moving_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original.mp4"
            final = Path(tmp) / "out" / "original_editado.mp4"
            original.write_bytes(b"video")

            output, moved = _finalize_project_output(str(original), str(final), str(original))

            self.assertEqual(output, str(final))
            self.assertFalse(moved)
            self.assertTrue(original.exists())
            self.assertEqual(final.read_bytes(), b"video")

    def test_finalize_project_output_moves_intermediate_to_final_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original.mp4"
            intermediate = Path(tmp) / "effects.mp4"
            final = Path(tmp) / "out" / "original_editado.mp4"
            original.write_bytes(b"original")
            intermediate.write_bytes(b"rendered")

            output, moved = _finalize_project_output(str(intermediate), str(final), str(original))

            self.assertEqual(output, str(final))
            self.assertTrue(moved)
            self.assertFalse(intermediate.exists())
            self.assertEqual(final.read_bytes(), b"rendered")

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
            input_path = Path(tmp) / "input.mp4"
            input_path.write_bytes(b"video")

            result = run_pipeline(str(input_path), tmp, config)

        analyze.assert_not_called()
        self.assertTrue(result.success)
        self.assertEqual(result.analysis.speech_segments, [(0.0, 12.0)])
        self.assertTrue(result.main_video.endswith("_editado.mp4"))

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
