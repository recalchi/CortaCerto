import threading
import unittest
from unittest import mock

import numpy as np

from src.core.color_grade import ColorGrade
from src.core.effect_renderer import (
    _apply_chroma_key_bgr,
    _apply_clip_frame_options_bgr,
    _clip_option_for_output_time,
    _clip_overlay_options_for_output_time,
    _compose_overlay_frame_bgr,
    _clip_option_has_source_replacement,
    _fit_image_frame_bgr,
    _is_image_path,
    _scale_frame_bgr_centered,
    _text_overlay_lines,
    render_clip_source_pass,
    render_effects_pass,
)


class EffectRendererTests(unittest.TestCase):
    def test_no_effects_returns_input_without_opencv_or_ffmpeg(self) -> None:
        with mock.patch("src.core.effect_renderer._render_color_grade_ffmpeg") as ffmpeg_grade, \
                mock.patch("src.core.effect_renderer.cv2.VideoCapture") as video_capture:
            result = render_effects_pass(
                "input.mp4",
                "output.mp4",
                color_grade=ColorGrade(enabled=False),
                bokeh_intensity=0.0,
            )

        self.assertEqual(result, "input.mp4")
        ffmpeg_grade.assert_not_called()
        video_capture.assert_not_called()

    def test_clip_source_pass_returns_input_without_replacements(self) -> None:
        with mock.patch("src.core.effect_renderer.cv2.VideoCapture") as video_capture:
            result = render_clip_source_pass("input.mp4", "output.mp4", [])

        self.assertEqual(result, "input.mp4")
        video_capture.assert_not_called()

    def test_clip_option_for_output_time_uses_output_range(self) -> None:
        options = [
            {"source_path": "a.mp4", "output_start_s": 0.0, "output_end_s": 2.0},
            {"source_path": "b.mp4", "output_start_s": 2.0, "output_end_s": 4.0},
        ]

        self.assertEqual(_clip_option_for_output_time(options, 2.5)["source_path"], "b.mp4")
        self.assertIsNone(_clip_option_for_output_time(options, 4.5))

    def test_clip_overlay_options_for_output_time_returns_all_active_layers(self) -> None:
        options = [
            {"layer": "overlay", "source_path": "a.png", "output_start_s": 0.0, "output_end_s": 3.0},
            {"layer": "overlay", "source_path": "b.png", "output_start_s": 1.0, "output_end_s": 2.0},
        ]

        active = _clip_overlay_options_for_output_time(options, 1.5)

        self.assertEqual([item["source_path"] for item in active], ["a.png", "b.png"])

    def test_clip_option_has_source_replacement_requires_source_and_range(self) -> None:
        self.assertTrue(_clip_option_has_source_replacement({"source_path": "a.mp4", "output_start_s": 0, "output_end_s": 1}))
        self.assertFalse(_clip_option_has_source_replacement({"source_path": "", "output_start_s": 0, "output_end_s": 1}))
        self.assertFalse(_clip_option_has_source_replacement({"source_path": "a.mp4", "output_start_s": 1, "output_end_s": 1}))

    def test_image_source_helpers_fit_static_images_for_clip_replacement(self) -> None:
        self.assertTrue(_is_image_path("capa.PNG"))
        frame = np.zeros((20, 40, 3), dtype=np.uint8)
        frame[:, :] = [0, 0, 255]

        fitted = _fit_image_frame_bgr(frame, 100, 100)

        self.assertEqual(fitted.shape, (100, 100, 3))
        self.assertTrue((fitted[50, 50] == [0, 0, 255]).all())
        self.assertTrue((fitted[0, 0] == [0, 0, 0]).all())

    def test_scale_frame_bgr_centered_preserves_frame_shape(self) -> None:
        frame = np.full((20, 30, 3), 255, dtype=np.uint8)

        zoomed = _scale_frame_bgr_centered(frame, 150.0)
        shrunk = _scale_frame_bgr_centered(frame, 50.0)

        self.assertEqual(zoomed.shape, frame.shape)
        self.assertEqual(shrunk.shape, frame.shape)
        self.assertTrue((shrunk[0, 0] == [0, 0, 0]).all())

    def test_clip_frame_options_apply_positioned_scale(self) -> None:
        frame = np.full((10, 10, 3), 255, dtype=np.uint8)

        rendered = _apply_clip_frame_options_bgr(
            frame,
            {"scale_pct": 50.0, "position_x_pct": 100.0, "position_y_pct": 0.0},
        )

        self.assertEqual(rendered.shape, frame.shape)
        self.assertTrue((rendered[5, 0] == [0, 0, 0]).all())
        self.assertTrue((rendered[5, 9] == [255, 255, 255]).all())

    def test_compose_overlay_frame_keeps_base_visible_when_overlay_is_scaled(self) -> None:
        base = np.zeros((10, 10, 3), dtype=np.uint8)
        base[:, :] = [0, 0, 255]
        overlay = np.zeros((10, 10, 3), dtype=np.uint8)
        overlay[:, :] = [255, 0, 0]

        rendered = _compose_overlay_frame_bgr(base, overlay, {"layer": "overlay", "scale_pct": 50.0})

        self.assertTrue((rendered[0, 0] == [0, 0, 255]).all())
        self.assertTrue((rendered[5, 5] == [255, 0, 0]).all())

    def test_compose_overlay_frame_chroma_reveals_base_background(self) -> None:
        base = np.zeros((8, 8, 3), dtype=np.uint8)
        base[:, :] = [10, 20, 30]
        overlay = np.zeros((8, 8, 3), dtype=np.uint8)
        overlay[:, :] = [0, 255, 0]

        rendered = _compose_overlay_frame_bgr(
            base,
            overlay,
            {"layer": "overlay", "chroma_enabled": True, "chroma_color": "#00ff00", "chroma_tolerance": 5.0},
        )

        self.assertTrue((rendered[0, 0] == [10, 20, 30]).all())

    def test_compose_overlay_frame_respects_opacity(self) -> None:
        base = np.zeros((8, 8, 3), dtype=np.uint8)
        base[:, :] = [0, 0, 255]
        overlay = np.zeros((8, 8, 3), dtype=np.uint8)
        overlay[:, :] = [255, 0, 0]

        rendered = _compose_overlay_frame_bgr(base, overlay, {"layer": "overlay", "opacity_pct": 50.0})

        self.assertTrue((rendered[0, 0] == [128, 0, 128]).all())

    def test_compose_overlay_frame_can_stack_layers_in_order(self) -> None:
        base = np.zeros((12, 12, 3), dtype=np.uint8)
        base[:, :] = [0, 0, 255]
        lower = np.zeros((12, 12, 3), dtype=np.uint8)
        lower[:, :] = [255, 0, 0]
        upper = np.zeros((12, 12, 3), dtype=np.uint8)
        upper[:, :] = [0, 255, 0]

        rendered = _compose_overlay_frame_bgr(base, lower, {"layer": "overlay", "scale_pct": 50.0})
        rendered = _compose_overlay_frame_bgr(rendered, upper, {"layer": "overlay", "scale_pct": 25.0})

        self.assertTrue((rendered[0, 0] == [0, 0, 255]).all())
        self.assertTrue((rendered[3, 3] == [255, 0, 0]).all())
        self.assertTrue((rendered[6, 6] == [0, 255, 0]).all())

    def test_apply_chroma_key_bgr_marks_target_color(self) -> None:
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        frame[:, :] = [0, 255, 0]

        keyed = _apply_chroma_key_bgr(frame, "#00ff00", 5.0)

        self.assertFalse((keyed[0, 0] == [0, 255, 0]).all())

    def test_clip_frame_options_apply_chroma_scale_and_text(self) -> None:
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        frame[:, :] = [0, 255, 0]

        rendered = _apply_clip_frame_options_bgr(
            frame,
            {
                "chroma_enabled": True,
                "chroma_color": "#00ff00",
                "chroma_tolerance": 5.0,
                "scale_pct": 50.0,
                "text_overlay": "Titulo",
            },
        )

        self.assertEqual(rendered.shape, frame.shape)
        self.assertFalse((rendered == frame).all())

    def test_clip_frame_options_can_disable_text_background(self) -> None:
        frame = np.zeros((80, 100, 3), dtype=np.uint8)

        no_bg = _apply_clip_frame_options_bgr(
            frame,
            {"text_overlay": "Titulo", "text_background_enabled": False},
        )
        with_bg = _apply_clip_frame_options_bgr(
            frame,
            {"text_overlay": "Titulo", "text_background_enabled": True, "text_background_color": "#112233"},
        )
        red_text = _apply_clip_frame_options_bgr(
            frame,
            {"text_overlay": "Titulo", "text_background_enabled": False, "text_color": "#ff0000"},
        )

        self.assertFalse((no_bg == frame).all())
        self.assertFalse((with_bg == no_bg).all())
        self.assertFalse((red_text == no_bg).all())

    def test_text_overlay_lines_support_multiline_content(self) -> None:
        self.assertEqual(_text_overlay_lines("Linha 1\nLinha 2"), ["Linha 1", "Linha 2"])
        self.assertEqual(len(_text_overlay_lines("1\n2\n3\n4\n5")), 4)

    def test_color_grade_without_bokeh_uses_ffmpeg_fast_path(self) -> None:
        cancel = threading.Event()
        grade = ColorGrade(enabled=True, saturation=10, contrast=8)

        with mock.patch("src.core.effect_renderer._render_color_grade_ffmpeg", return_value="output.mp4") as ffmpeg_grade, \
                mock.patch("src.core.effect_renderer.cv2.VideoCapture") as video_capture:
            result = render_effects_pass(
                "input.mp4",
                "output.mp4",
                color_grade=grade,
                bokeh_intensity=0.0,
                cancel=cancel,
            )

        self.assertEqual(result, "output.mp4")
        ffmpeg_grade.assert_called_once_with(
            "input.mp4",
            "output.mp4",
            grade,
            cancel=cancel,
            on_progress=None,
        )
        video_capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
