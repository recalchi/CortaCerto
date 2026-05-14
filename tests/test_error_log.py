import json
import tempfile
import unittest
from pathlib import Path

from src.core.error_log import (
    default_error_log_dir,
    error_log_path,
    install_error_hooks,
    record_error,
    record_error_message,
)


class ErrorLogTests(unittest.TestCase):
    def test_default_error_log_dir_uses_override(self) -> None:
        self.assertEqual(
            default_error_log_dir({"CORTACERTO_ERROR_LOG_DIR": "C:/tmp/cortacerto-logs"}),
            Path("C:/tmp/cortacerto-logs"),
        )

    def test_record_error_writes_jsonl_and_redacts_sensitive_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                raise RuntimeError("falha controlada")
            except RuntimeError as exc:
                path = record_error(
                    exc,
                    where="unit_test",
                    context={"project": "Demo", "OPENAI_API_KEY": "sk-test-secret"},
                    log_dir=tmp,
                )

            payload = json.loads(path.read_text(encoding="utf-8").strip())

        self.assertEqual(path, Path(tmp) / "errors.jsonl")
        self.assertEqual(payload["where"], "unit_test")
        self.assertEqual(payload["type"], "RuntimeError")
        self.assertEqual(payload["context"]["project"], "Demo")
        self.assertEqual(payload["context"]["OPENAI_API_KEY"], "[redacted]")
        self.assertIn("falha controlada", payload["traceback"])

    def test_record_error_message_writes_operational_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = record_error_message("export falhou", where="pipeline", context={"clip_count": 2}, log_dir=tmp)
            payload = json.loads(path.read_text(encoding="utf-8").strip())

        self.assertEqual(payload["type"], "Message")
        self.assertEqual(payload["message"], "export falhou")
        self.assertEqual(payload["context"]["clip_count"], 2)

    def test_install_error_hooks_registers_tk_callback_logger(self) -> None:
        class FakeRoot:
            report_callback_exception = None

        shown = []
        with tempfile.TemporaryDirectory() as tmp:
            root = FakeRoot()
            path = install_error_hooks(
                root=root,
                context_fn=lambda: {"screen": "editor"},
                log_dir=tmp,
                show_callback_error=lambda title, message: shown.append((title, message)),
            )
            try:
                raise ValueError("callback quebrou")
            except ValueError as exc:
                root.report_callback_exception(ValueError, exc, exc.__traceback__)
            payload = json.loads(error_log_path(tmp).read_text(encoding="utf-8").strip())

        self.assertEqual(path, Path(tmp) / "errors.jsonl")
        self.assertEqual(payload["where"], "tk_callback")
        self.assertEqual(payload["context"]["screen"], "editor")
        self.assertEqual(shown, [("Erro no CortaCerto", "O erro foi registrado para diagnostico.")])


if __name__ == "__main__":
    unittest.main()
