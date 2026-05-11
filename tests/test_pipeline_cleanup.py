import tempfile
import unittest
from pathlib import Path

from src.pipeline import _cleanup_intermediate_exports, _normalize_manual_segments


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


if __name__ == "__main__":
    unittest.main()
