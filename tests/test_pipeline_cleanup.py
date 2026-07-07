import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core import editor
from src.config import ProcessingConfig
from src.pipeline import (
    _audio_postprocess_label,
    _build_audio_postprocess_filters,
    _build_clip_volume_filter,
    _build_per_clip_data,
    _clip_options_for_track_options,
    _clip_options_with_output_ranges,
    _clip_volume_adjustments,
    _has_clip_audio_adjustments,
    _has_audio_postprocess,
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

    def test_render_segment_falls_back_to_libx264_when_hardware_encoder_fails(self) -> None:
        class DummyProcessManager:
            def __init__(self) -> None:
                self.calls: list[tuple[list[str], str]] = []

            def run_checked(self, cmd: list[str], context: str, timeout_s: float) -> None:
                self.calls.append((cmd, context))
                if len(self.calls) == 1:
                    raise RuntimeError("segmento seg_00001: ffmpeg exit 4294967256 Function not implemented")

        pm = DummyProcessManager()

        with mock.patch("src.core.editor._has_audio_stream", return_value=False), \
                mock.patch("src.core.editor.ffmpeg", return_value="ffmpeg"):
            editor._render_segment(
                "input.mp4",
                0.0,
                1.0,
                "out.ts",
                editor.SegmentEffect(),
                "",
                "h264_qsv",
                ["-global_quality", "19"],
                pm,
                30.0,
            )

        self.assertEqual(len(pm.calls), 2)
        self.assertIn("h264_qsv", pm.calls[0][0])
        self.assertIn("libx264", pm.calls[1][0])
        self.assertNotIn("-hwaccel", pm.calls[1][0])
        self.assertIn("fallback libx264", pm.calls[1][1])

    def test_render_segment_fits_source_into_project_canvas_before_user_transform(self) -> None:
        class DummyProcessManager:
            def __init__(self) -> None:
                self.cmd: list[str] = []

            def run_checked(self, cmd: list[str], context: str, timeout_s: float) -> None:
                self.cmd = cmd

        pm = DummyProcessManager()
        fx = editor.SegmentEffect(scale_pct=150.0, position_x=42.0, position_y=-18.0)

        with mock.patch("src.core.editor._has_audio_stream", return_value=False), \
                mock.patch("src.core.editor.ffmpeg", return_value="ffmpeg"):
            editor._render_segment(
                "input.mp4",
                0.0,
                1.0,
                "out.ts",
                fx,
                "",
                "libx264",
                ["-crf", "18"],
                pm,
                30.0,
                target_size=(1080, 1920),
            )

        vf = pm.cmd[pm.cmd.index("-vf") + 1]
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease", vf)
        self.assertIn("pad=1080:1920", vf)
        self.assertIn("crop=1080:1920", vf)
        self.assertIn("-42.000", vf)
        self.assertIn("+18.000", vf)

    def test_drawtext_font_size_scales_with_export_height(self) -> None:
        self.assertGreater(editor._drawtext_font_size(100, 1920), editor._drawtext_font_size(100, 1080))
        self.assertGreaterEqual(editor._drawtext_font_size(80, 1920), 60)

    def test_export_text_style_uses_preview_margin_and_position(self) -> None:
        style = editor._text_style_from_export_clip(
            {
                "text_overlay": "Legenda com quebra automatica",
                "text_position_x_pct": 10.0,
                "text_position_y_pct": 84.0,
                "text_side_margin_pct": 12.0,
                "text_line_spacing": 1.35,
                "text_size_pct": 90.0,
                "text_background_enabled": False,
            },
            frame_height=1920,
        )

        self.assertEqual(style.pos_x_pct, 60.0)
        self.assertEqual(style.pos_y_pct, 84.0)
        self.assertEqual(style.max_width_pct, 76.0)
        self.assertEqual(style.line_spacing, 1.35)
        self.assertFalse(style.bg_enabled)
        self.assertTrue(style.shadow_enabled)

    def test_timeline_audio_mix_filter_respects_clip_timing_and_source_offset(self) -> None:
        fc = editor._build_timeline_audio_mix_filter([
            {
                "source_path": "voice.wav",
                "start_s": 5.0,
                "end_s": 7.5,
                "source_offset_s": 5.0,
                "volume_pct": 80.0,
                "fade_in_s": 0.2,
                "fade_out_s": 0.3,
            }
        ])

        self.assertIn("[0:a]anull[a0]", fc)
        self.assertIn("atrim=start=0.000:duration=2.500", fc)
        self.assertIn("volume=0.8000", fc)
        self.assertIn("adelay=5000|5000", fc)
        self.assertIn("amix=inputs=2:duration=first", fc)

    def test_audio_postprocess_filters_are_independent_controls(self) -> None:
        clean = ProcessingConfig(audio_normalization=False)
        self.assertEqual(_build_audio_postprocess_filters(clean), [])
        self.assertFalse(_has_audio_postprocess(clean))

        config = ProcessingConfig(
            noise_reduction=True,
            audio_normalization=True,
            audio_voice_filter=True,
            audio_compressor=True,
        )

        self.assertEqual(
            _build_audio_postprocess_filters(config),
            [
                "afftdn=nf=-18",
                "highpass=f=80",
                "lowpass=f=12000",
                "acompressor=threshold=-18dB:ratio=2.5:attack=8:release=120",
                "loudnorm=I=-16:TP=-1.5:LRA=11",
            ],
        )
        self.assertTrue(_has_audio_postprocess(config))
        self.assertIn("reducao de ruido leve", _audio_postprocess_label(config))

    def test_clip_option_plan_summarizes_editor_adjustments(self) -> None:
        plan = _clip_option_plan([
            {
                "scale_pct": 125.0,
                "volume_pct": 80.0,
                "transition": "Fade",
                "text_overlay": "Intro",
                "chroma_enabled": True,
                "blur_type": "gaussian",
                "blur_intensity": 35.0,
            },
            {"layer": "overlay", "source_path": "logo.png", "scale_pct": 100.0, "volume_pct": 100.0, "transition": "Corte"},
            {"scale_pct": 100.0, "volume_pct": 100.0, "transition": "Corte", "text_overlay": ""},
        ])

        self.assertIn("escala em 1 clipe(s)", plan)
        self.assertIn("volume em 1 clipe(s)", plan)
        self.assertIn("texto em 1 clipe(s)", plan)
        self.assertIn("chroma em 1 clipe(s)", plan)
        self.assertIn("desfoque em 1 clipe(s)", plan)
        self.assertIn("1 overlay(s) visual(is)", plan)

    def test_per_clip_data_carries_blur_controls_to_renderer(self) -> None:
        data = _build_per_clip_data([
            {"speed_factor": 1.25, "transition": "Fade", "blur_type": "pixelate", "blur_intensity": 55.0, "blur_direction": "both"},
            {"layer": "overlay", "blur_type": "box", "blur_intensity": 100.0},
        ])

        self.assertEqual(data, [
            {
                "speed_factor": 1.25,
                "transition": "Fade",
                "transition_duration_s": 0.4,
                "blur_type": "pixelate",
                "blur_intensity": 55.0,
                "blur_direction": "both",
            }
        ])

    def test_clip_options_for_track_options_applies_export_layer_state(self) -> None:
        options = _clip_options_for_track_options(
            [
                {
                    "source_path": "broll.mp4",
                    "scale_pct": 125.0,
                    "position_x_pct": 20.0,
                    "position_y_pct": -10.0,
                    "volume_pct": 80.0,
                    "text_overlay": "Intro",
                    "chroma_enabled": True,
                }
            ],
            {"visual_visible": False, "text_visible": False, "audio_muted": True},
        )

        self.assertEqual(options[0]["source_path"], "")
        self.assertEqual(options[0]["scale_pct"], 100.0)
        self.assertEqual(options[0]["position_x_pct"], 0.0)
        self.assertEqual(options[0]["position_y_pct"], 0.0)
        self.assertEqual(options[0]["volume_pct"], 0.0)
        self.assertEqual(options[0]["text_overlay"], "")
        self.assertFalse(options[0]["chroma_enabled"])

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

    def test_overlay_clip_options_project_across_manual_timeline_gaps(self) -> None:
        options = _clip_options_with_output_ranges(
            [
                {"start_s": 0.0, "end_s": 3.0, "source_path": ""},
                {"start_s": 4.0, "end_s": 8.0, "source_path": ""},
                {"layer": "overlay", "start_s": 2.0, "end_s": 5.0, "source_path": "logo.png"},
            ],
            [(0.0, 3.0), (4.0, 8.0)],
        )

        overlay_ranges = [
            (item["output_start_s"], item["output_end_s"])
            for item in options
            if item.get("layer") == "overlay"
        ]
        self.assertEqual(overlay_ranges, [(2.0, 3.0), (3.0, 4.0)])

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

    def test_muted_track_options_create_zero_volume_adjustment(self) -> None:
        options = _clip_options_with_output_ranges(
            _clip_options_for_track_options(
                [{"volume_pct": 100.0, "start_s": 0.0, "end_s": 2.0}],
                {"audio_muted": True},
            ),
            None,
        )

        self.assertEqual(_clip_volume_adjustments(options), [(0.0, 2.0, 0.0)])

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
            audio_normalization=False,
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
            audio_normalization=False,
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
