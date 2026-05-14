import unittest
from unittest import mock

import main
from src.bootstrap import build_ffmpeg_error_message, ensure_startup_dependencies


class BootstrapTests(unittest.TestCase):
    def test_ffmpeg_error_message_is_actionable(self) -> None:
        message = build_ffmpeg_error_message("ffmpeg não encontrado.")

        self.assertIn("CortaCerto precisa do FFmpeg", message)
        self.assertIn("winget install --id Gyan.FFmpeg", message)
        self.assertIn("https://www.gyan.dev/ffmpeg/builds/", message)

    def test_startup_dependencies_returns_true_when_ffmpeg_is_available(self) -> None:
        calls: list[tuple[str, str]] = []

        ok = ensure_startup_dependencies(
            lambda: "C:/ffmpeg/bin/ffmpeg.exe",
            lambda title, message: calls.append((title, message)),
            lambda message: None,
        )

        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_startup_dependencies_reports_error_when_ffmpeg_is_missing(self) -> None:
        calls: list[tuple[str, str]] = []

        def missing() -> str:
            raise RuntimeError("não achei")

        ok = ensure_startup_dependencies(
            missing,
            lambda title, message: calls.append((title, message)),
            lambda message: None,
        )

        self.assertFalse(ok)
        self.assertEqual(calls[0][0], "FFmpeg não encontrado")
        self.assertIn("não achei", calls[0][1])

    def test_main_check_startup_exits_without_opening_ui(self) -> None:
        with mock.patch("main.ensure_ffmpeg", return_value="C:/ffmpeg/bin/ffmpeg.exe"):
            self.assertEqual(main.main(["--check-startup"]), 0)

    def test_main_handles_keyboard_interrupt_without_traceback(self) -> None:
        class App:
            def run(self) -> None:
                raise KeyboardInterrupt()

        with mock.patch("main.ensure_ffmpeg", return_value="C:/ffmpeg/bin/ffmpeg.exe"), \
             mock.patch("src.ui.app.CortaCertoApp", return_value=App()), \
             mock.patch("builtins.print") as printed:
            self.assertEqual(main.main([]), 130)

        printed.assert_called_with("[STARTUP] CortaCerto encerrado pelo terminal.")


if __name__ == "__main__":
    unittest.main()
