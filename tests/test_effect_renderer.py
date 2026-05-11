import threading
import unittest
from unittest import mock

from src.core.color_grade import ColorGrade
from src.core.effect_renderer import (
    _clip_option_for_output_time,
    _clip_option_has_source_replacement,
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
