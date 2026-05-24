"""Tests for the aspect_ratio → crop-target resolver (Phase 6.1).

The resolver is what bridges the frontend `aspect_ratio` field (string like
"9:16") to the ffmpeg crop filter. Legacy `platform` callers still work.
"""
import unittest

from src.core.editor import _resolve_target_aspect


class AspectResolverTests(unittest.TestCase):
    def test_explicit_aspect_ratio_takes_priority_over_platform(self) -> None:
        # User picked 1:1 but the request also has platform=reels (9:16).
        # Aspect ratio should win.
        self.assertEqual(_resolve_target_aspect("1:1", "reels"), (1, 1))

    def test_16x9_means_no_crop(self) -> None:
        # 16:9 is the default — source is assumed landscape, so we skip crop.
        self.assertIsNone(_resolve_target_aspect("16:9", "reels"))

    def test_original_means_no_crop(self) -> None:
        self.assertIsNone(_resolve_target_aspect("original", "youtube"))

    def test_unknown_aspect_ratio_falls_back_to_platform(self) -> None:
        # Garbage AR → legacy platform mapping
        self.assertEqual(_resolve_target_aspect("garbage", "reels"), (9, 16))

    def test_no_aspect_ratio_uses_platform(self) -> None:
        self.assertEqual(_resolve_target_aspect(None, "tiktok"), (9, 16))
        self.assertEqual(_resolve_target_aspect(None, "shorts"), (9, 16))
        self.assertIsNone(_resolve_target_aspect(None, "youtube"))

    def test_all_known_aspect_ratios_parse(self) -> None:
        for ar in ("9:16", "1:1", "4:5", "5:4", "4:3", "3:4"):
            with self.subTest(ar=ar):
                result = _resolve_target_aspect(ar, "youtube")
                self.assertIsNotNone(result)
                w, h = result  # type: ignore[misc]
                self.assertGreater(w, 0)
                self.assertGreater(h, 0)


if __name__ == "__main__":
    unittest.main()
