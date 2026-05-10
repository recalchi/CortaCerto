import unittest

from PIL import Image

from src.ui.app import _fit_preview_image, _removed_ranges_from_segments


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


if __name__ == "__main__":
    unittest.main()
