import tempfile
import unittest
from pathlib import Path

from src.api_settings import api_credentials_summary, load_api_credentials, load_env_file, mask_secret


class ApiSettingsTests(unittest.TestCase):
    def test_load_env_file_parses_quoted_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('OPENAI_API_KEY="test-key-123456"\n# comment\nSECOND=value\n', encoding="utf-8")

            values = load_env_file(env_path)

        self.assertEqual(values["OPENAI_API_KEY"], "test-key-123456")
        self.assertEqual(values["SECOND"], "value")

    def test_load_api_credentials_masks_values_and_prefers_environ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('OPENAI_API_KEY="from-file-secret"\n', encoding="utf-8")

            credentials = load_api_credentials(
                ["OPENAI_API_KEY", "GEMINI_API_KEY"],
                env_file=env_path,
                environ={"OPENAI_API_KEY": "from-env-secret", "GEMINI_API_KEY": "gemini-secret"},
            )

        self.assertEqual([credential.name for credential in credentials], ["OPENAI_API_KEY", "GEMINI_API_KEY"])
        self.assertEqual(credentials[0].source, "env")
        self.assertNotIn("from-env-secret", api_credentials_summary(credentials))

    def test_mask_secret_never_returns_full_value(self) -> None:
        self.assertEqual(mask_secret("short"), "***")
        self.assertEqual(mask_secret("sample-secret-abcdef123456"), "sample...3456")

    def test_api_credentials_summary_handles_empty_state(self) -> None:
        self.assertEqual(api_credentials_summary([]), "Nenhuma API configurada.")


if __name__ == "__main__":
    unittest.main()
