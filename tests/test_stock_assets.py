import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core import stock_assets


class StockAssetsTests(unittest.TestCase):
    def test_stock_settings_masks_configured_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('PEXELS_API_KEY="pexels-secret-123456"\n', encoding="utf-8")

            settings = stock_assets.stock_settings(env_path)

        self.assertTrue(settings["keys"]["PEXELS_API_KEY"]["configured"])
        self.assertNotIn("pexels-secret-123456", json.dumps(settings))
        self.assertTrue(any(p["id"] == "pexels" and p["configured"] for p in settings["providers"]))

    def test_update_stock_settings_upserts_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('PEXELS_API_KEY="old"\nKEEP=value\n', encoding="utf-8")

            stock_assets.update_stock_settings({"PEXELS_API_KEY": "new-secret-value"}, env_path)
            text = env_path.read_text(encoding="utf-8")

        self.assertIn('PEXELS_API_KEY="new-secret-value"', text)
        self.assertIn("KEEP=value", text)

    def test_download_stock_asset_writes_file_and_metadata(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return b"image-bytes"

        with tempfile.TemporaryDirectory() as tmp:
            local_app_data = Path(tmp) / "localapp"
            asset = {
                "id": "asset-1",
                "provider": "pexels",
                "type": "image",
                "title": "Demo Asset",
                "author": "Author",
                "license": "License",
                "download_url": "https://example.test/source.jpg",
            }

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}):
                with patch("urllib.request.urlopen", return_value=FakeResponse()):
                    result = stock_assets.download_stock_asset(asset)

            local_path = Path(result["local_path"])
            metadata_path = Path(result["metadata_path"])

            self.assertEqual(local_path.read_bytes(), b"image-bytes")
            self.assertTrue(metadata_path.is_file())
            self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["provider"], "pexels")


if __name__ == "__main__":
    unittest.main()
