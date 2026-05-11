"""Startup checks for the CortaCerto desktop entry point."""
from __future__ import annotations

import sys
from typing import Callable


def build_ffmpeg_error_message(detail: str) -> str:
    return (
        "O CortaCerto precisa do FFmpeg para abrir, pré-visualizar e exportar vídeos.\n\n"
        f"Detalhe técnico:\n{detail}\n\n"
        "Como resolver:\n"
        "1. Instale com: winget install --id Gyan.FFmpeg\n"
        "2. Feche e reabra o terminal ou o app.\n"
        "3. Se preferir, baixe manualmente em: https://www.gyan.dev/ffmpeg/builds/"
    )


def show_startup_error(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"{title}\n\n{message}", file=sys.stderr)


def ensure_startup_dependencies(
    ensure_ffmpeg_fn: Callable[[], str],
    show_error_fn: Callable[[str, str], None] = show_startup_error,
    log_fn: Callable[[str], None] = print,
) -> bool:
    try:
        ffmpeg_path = ensure_ffmpeg_fn()
    except RuntimeError as exc:
        show_error_fn("FFmpeg não encontrado", build_ffmpeg_error_message(str(exc)))
        return False
    log_fn(f"[STARTUP] FFmpeg disponível: {ffmpeg_path}")
    return True
