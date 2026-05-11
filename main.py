"""CortaCerto main entry point."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.bootstrap import ensure_startup_dependencies
from src.ffmpeg_env import ensure_ffmpeg

# Resolve ffmpeg before importing UI modules that may trigger video tooling.
if not ensure_startup_dependencies(ensure_ffmpeg):
    sys.exit(1)

from src.ui.app import CortaCertoApp


def main() -> None:
    app = CortaCertoApp()
    app.run()


if __name__ == "__main__":
    main()
