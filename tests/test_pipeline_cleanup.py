import tempfile
import unittest
from pathlib import Path

from src.pipeline import _cleanup_intermediate_exports


class PipelineCleanupTests(unittest.TestCase):
    def test_cleanup_intermediate_exports_removes_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "intermediate.mp4"
            path.write_bytes(b"tmp")

            _cleanup_intermediate_exports({str(path)})

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
