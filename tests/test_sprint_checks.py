import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.run_sprint_checks import check_legacy_root_files, check_text_encoding, safe_console


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

    def test_text_encoding_check_flags_common_mojibake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.py"
            bad.write_text(f'print("v{chr(0x00c3)}deo")', encoding="utf-8")

            with mock.patch("scripts.run_sprint_checks.iter_text_files", return_value=[bad]):
                self.assertEqual(check_text_encoding(print_fn=lambda _: None), 1)

    def test_text_encoding_check_accepts_valid_utf8_accents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.py"
            good.write_text('print("vídeo pronto")', encoding="utf-8")

            with mock.patch("scripts.run_sprint_checks.iter_text_files", return_value=[good]):
                self.assertEqual(check_text_encoding(print_fn=lambda _: None), 0)

    def test_safe_console_escapes_unprintable_unicode(self) -> None:
        self.assertEqual(safe_console(f"bad {chr(0xfffd)}"), "bad \\ufffd")


if __name__ == "__main__":
    unittest.main()
