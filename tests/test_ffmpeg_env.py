import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.ffmpeg_env import _find_in_winget_packages, _winget_package_roots


class FFmpegEnvTests(unittest.TestCase):
    def test_find_in_winget_packages_returns_nested_ffmpeg_bin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ffmpeg_bin = (
                Path(tmp)
                / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
                / "ffmpeg-8.1.1-full_build"
                / "bin"
            )
            ffmpeg_bin.mkdir(parents=True)
            (ffmpeg_bin / "ffmpeg.exe").write_bytes(b"")

            self.assertEqual(_find_in_winget_packages(Path(tmp)), str(ffmpeg_bin))

    def test_winget_package_roots_include_user_and_machine_locations_once(self) -> None:
        env = {
            "LOCALAPPDATA": "C:/Users/test/AppData/Local",
            "PROGRAMFILES": "C:/Program Files",
            "ProgramW6432": "C:/Program Files",
            "PROGRAMFILES(X86)": "C:/Program Files (x86)",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            roots = _winget_package_roots()

        root_strings = [str(root).replace("\\", "/") for root in roots]
        self.assertIn("C:/Users/test/AppData/Local/Microsoft/WinGet/Packages", root_strings)
        self.assertIn("C:/Program Files/WinGet/Packages", root_strings)
        self.assertIn("C:/Program Files (x86)/WinGet/Packages", root_strings)
        self.assertEqual(len(root_strings), len({item.lower() for item in root_strings}))


if __name__ == "__main__":
    unittest.main()
