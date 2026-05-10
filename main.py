"""ContentForge main entry point."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# Resolve ffmpeg antes de qualquer import que use subprocess
from src.ffmpeg_env import ensure_ffmpeg
try:
    ensure_ffmpeg()
except RuntimeError as e:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("ffmpeg não encontrado", str(e))
    except Exception:
        print(f"ffmpeg não encontrado:\n{e}", file=sys.stderr)
    sys.exit(1)

from src.ui.app import ContentForgeApp


def main() -> None:
    app = ContentForgeApp()
    app.run()


if __name__ == "__main__":
    main()
