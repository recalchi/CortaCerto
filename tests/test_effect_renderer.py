import threading
import unittest
from unittest import mock

import numpy as np

from src.core.color_grade import ColorGrade
from src.core.effect_renderer import (
    _apply_chroma_key_bgr,
    _apply_clip_frame_options_bgr,
    _clip_option_for_output_time,
    _clip_option_has_source_replacement,
    _scale_frame_bgr_centered,
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

    def test_clip_option_has_source_replacement_requires_source_and_range(self) -> None:
        self.assertTrue(_clip_option_has_source_replacement({"source_path": "a.mp4", "output_start_s": 0, "output_end_s": 1}))
        self.assertFalse(_clip_option_has_source_replacement({"source_path": "", "output_start_s": 0, "output_end_s": 1}))
        self.assertFalse(_clip_option_has_source_replacement({"source_path": "a.mp4", "output_start_s": 1, "output_end_s": 1}))

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
