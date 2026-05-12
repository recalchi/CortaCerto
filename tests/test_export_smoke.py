import tempfile
import unittest
import os
from pathlib import Path

from src.config import ProcessingConfig
from src.ffmpeg_env import ffmpeg
from src.pipeline import run_pipeline


class ExportSmokeTests(unittest.TestCase):
    def test_export_with_assigned_clip_media_and_effects(self) -> None:
        if os.environ.get("CORTACERTO_EXPORT_SMOKE") != "1":
            self.skipTest("Defina CORTACERTO_EXPORT_SMOKE=1 ou use --include-export-smoke.")
        try:
            ffmpeg()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_video = root / "main.mp4"
            broll_video = root / "broll.mp4"
            _make_synthetic_video(main_video, "blue", 440)
            _make_synthetic_video(broll_video, "green", 660)

            config = ProcessingConfig(
                remove_silence=True,
                noise_reduction=False,
                generate_thumbnail=False,
                generate_vertical=False,
                manual_segments=[(0.0, 1.0)],
                clip_options=[
                    {
                        "start_s": 0.0,
                        "end_s": 1.0,
                        "source_path": str(broll_video),
                        "scale_pct": 90.0,
                        "volume_pct": 70.0,
                        "text_overlay": "Smoke",
                        "chroma_enabled": True,
                        "chroma_color": "#00ff00",
                        "chroma_tolerance": 80.0,
                    }
                ],
            )
            config.color_grade.enabled = False

            result = run_pipeline(str(main_video), str(root / "out"), config)

            self.assertTrue(result.success, result.error)
            self.assertTrue(result.main_video)
            self.assertTrue(Path(result.main_video).exists())
            self.assertGreater(Path(result.main_video).stat().st_size, 0)


def _make_synthetic_video(path: Path, color: str, tone_hz: int) -> None:
    import subprocess

    subprocess.run(
        [
            ffmpeg(), "-y",
            "-f", "lavfi",
            "-i", f"color=c={color}:s=160x90:r=12:d=1",
            "-f", "lavfi",
            "-i", f"sine=frequency={tone_hz}:duration=1",
            "-shortest",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
