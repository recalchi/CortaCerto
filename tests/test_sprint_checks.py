import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.run_sprint_checks import (
    build_check_plan,
    check_legacy_root_files,
    check_secret_leaks,
    check_test_inventory,
    check_text_encoding,
    format_check_plan,
    safe_console,
)


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

    def test_test_inventory_reports_declared_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tests_dir = Path(tmp) / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_demo.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
            messages: list[str] = []

            with mock.patch("scripts.run_sprint_checks.Path", side_effect=lambda p: Path(tmp) / p):
                self.assertEqual(check_test_inventory(print_fn=messages.append), 0)

        self.assertIn("1 arquivos, 1 casos", "\n".join(messages))

    def test_secret_leak_check_flags_openai_key_outside_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.py"
            bad.write_text('OPENAI_API_KEY="' + "sk-proj-" + 'abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8")

            with mock.patch("scripts.run_sprint_checks.iter_text_files", return_value=[bad]):
                self.assertEqual(check_secret_leaks(print_fn=lambda _: None), 1)

    def test_secret_leak_check_ignores_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('OPENAI_API_KEY="' + "sk-proj-" + 'abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8")

            with mock.patch("scripts.run_sprint_checks.iter_text_files", return_value=[env_file]):
                self.assertEqual(check_secret_leaks(print_fn=lambda _: None), 0)

    def test_check_plan_lists_execution_order(self) -> None:
        plan = build_check_plan(include_startup=True, include_export_smoke=True)
        titles = [title for title, _cmd in plan]

        self.assertEqual(titles[0], "Compilacao dos modulos principais")
        self.assertEqual(titles[1], "Testes unitarios e invariantes do editor")
        self.assertEqual(titles[2], "Startup real com FFmpeg")
        self.assertEqual(titles[3], "Export real sintetico")

        lines = format_check_plan(plan, strict_legacy=True)
        self.assertIn("1. Compilacao dos modulos principais", lines)
        self.assertIn("5. Checagem de arquivos legados conhecidos (strict)", lines)
        self.assertIn("8. Inventario de testes declarados", lines)


if __name__ == "__main__":
    unittest.main()
