"""CortaCerto main entry point."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.bootstrap import ensure_startup_dependencies
from src.ffmpeg_env import ensure_ffmpeg


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Resolve ffmpeg before importing UI modules that may trigger video tooling.
    if not ensure_startup_dependencies(ensure_ffmpeg):
        return 1

    if "--check-startup" in argv:
        print("[STARTUP] CortaCerto pronto para iniciar.")
        return 0

    from src.ui.app import CortaCertoApp
    app = CortaCertoApp()
    try:
        app.run()
    except KeyboardInterrupt:
        print("[STARTUP] CortaCerto encerrado pelo terminal.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
