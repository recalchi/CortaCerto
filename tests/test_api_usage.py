import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core import api_usage


class ApiUsageTests(unittest.TestCase):
    def test_openai_usage_summary_uses_local_log_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                'OPENAI_MONTHLY_BUDGET_USD="10"\n'
                'OPENAI_WHISPER_USD_PER_MIN="0.006"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": str(Path(tmp) / "localapp")}):
                with patch("src.core.api_usage.openai_usage_settings") as mocked_settings:
                    mocked_settings.return_value = {
                        "keys": {
                            "OPENAI_MONTHLY_BUDGET_USD": {"value": "10"},
                            "OPENAI_GPT_INPUT_USD_PER_1K": {"value": "0"},
                            "OPENAI_GPT_OUTPUT_USD_PER_1K": {"value": "0"},
                            "OPENAI_WHISPER_USD_PER_MIN": {"value": "0.006"},
                        }
                    }
                    api_usage.record_openai_usage(
                        feature="transcricao",
                        model="whisper-1",
                        audio_seconds=120,
                    )
                    summary = api_usage.openai_usage_summary()

        self.assertEqual(summary["calls"], 1)
        self.assertGreater(summary["estimated_cost_usd"], 0)
        self.assertEqual(summary["monthly_budget_usd"], 10.0)

    def test_usage_log_does_not_store_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(Path(tmp) / "localapp")}):
                event = api_usage.record_openai_usage(
                    feature="gpt",
                    model="gpt-test",
                    input_tokens=100,
                    output_tokens=50,
                )
                text = api_usage.usage_log_path().read_text(encoding="utf-8")

        self.assertNotIn("OPENAI_API_KEY", text)
        self.assertEqual(json.loads(text)["feature"], event["feature"])


if __name__ == "__main__":
    unittest.main()
