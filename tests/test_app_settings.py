import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core import app_settings


class AppSettingsTests(unittest.TestCase):
    def test_general_settings_defaults_keep_gpu_rendering_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"

            settings = app_settings.general_settings(env_path)

        self.assertFalse(settings["auto_updates"])
        self.assertTrue(settings["update_notifications"])
        self.assertFalse(settings["ui_gpu_rendering"])
        self.assertEqual(settings["startup_layout"], "last")
        self.assertIn("cache", settings)

    def test_general_settings_reads_save_dir_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                'CORTACERTO_AUTO_UPDATES="true"\n'
                'CORTACERTO_UPDATE_NOTIFICATIONS="false"\n'
                'CORTACERTO_UI_GPU_RENDERING="true"\n'
                'CORTACERTO_STARTUP_LAYOUT="capcut"\n'
                f'CORTACERTO_DEFAULT_SAVE_DIR="{tmp}"\n',
                encoding="utf-8",
            )

            settings = app_settings.general_settings(env_path)

        self.assertTrue(settings["auto_updates"])
        self.assertFalse(settings["update_notifications"])
        self.assertTrue(settings["ui_gpu_rendering"])
        self.assertEqual(settings["startup_layout"], "capcut")
        self.assertEqual(settings["default_save_dir"], tmp)

    def test_clear_cache_removes_stock_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local_app = Path(tmp) / "localapp"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app)}):
                stock_file = app_settings.stock_cache_root() / "pexels" / "image" / "demo.bin"
                stock_file.parent.mkdir(parents=True, exist_ok=True)
                stock_file.write_bytes(b"cache")

                before = app_settings.cache_info()
                after = app_settings.clear_cache()

        self.assertGreater(before["total_bytes"], 0)
        self.assertEqual(after["stock_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
