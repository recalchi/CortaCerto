import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.run_sprint_checks import check_legacy_root_files


class SprintChecksTests(unittest.TestCase):
    def test_legacy_root_check_passes_when_no_legacy_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scripts.run_sprint_checks.LEGACY_ROOT_FILES", ["legacy.py"]), \
                    mock.patch("scripts.run_sprint_checks.Path", side_effect=lambda p: Path(tmp) / p):
                self.assertEqual(check_legacy_root_files(strict=True, print_fn=lambda _: None), 0)

    def test_legacy_root_check_can_fail_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "legacy.py").write_text("print('old')", encoding="utf-8")

            with mock.patch("scripts.run_sprint_checks.LEGACY_ROOT_FILES", ["legacy.py"]), \
                    mock.patch("scripts.run_sprint_checks.Path", side_effect=lambda p: Path(tmp) / p):
                self.assertEqual(check_legacy_root_files(strict=True, print_fn=lambda _: None), 1)


if __name__ == "__main__":
    unittest.main()
