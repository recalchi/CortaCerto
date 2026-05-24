from __future__ import annotations

import sys
import os
import shutil
import signal
import tempfile
import time
import urllib.request


def _webview_user_data_dir() -> str:
    """Return a stable, controlled directory for WebView2's user data.

    Default behaviour of pywebview is to create a fresh tempfile.mkdtemp()
    every launch, then try to delete it on exit. Windows often refuses with
    `[WinError 32] file in use` because WebView2's child processes haven't
    fully released their handles yet. We use a fixed location under temp so:
      • The same folder is reused across launches (no per-run rm)
      • Stale folders can be cleaned with retry on the NEXT launch
    """
    base = os.path.join(tempfile.gettempdir(), "cortacerto_webview2")
    os.makedirs(base, exist_ok=True)
    return base


def _cleanup_stale_webview_folder() -> None:
    """Best-effort cleanup of stale WebView2 user-data subfolders from previous
    crashed runs. Retries with backoff to handle Windows file locks gracefully.
    Failures are silent — a leftover folder is harmless (WebView2 just reuses it).
    """
    base = _webview_user_data_dir()
    if not os.path.isdir(base):
        return
    # Don't delete the base itself (we WANT it persistent), just truncate stale
    # lock files that prevented WebView2 from starting cleanly last time.
    for name in ("EBWebView", "EdgeWebView"):
        lock_dir = os.path.join(base, name, "Default", "BrowsingTopicsSiteData")
        if not os.path.isdir(lock_dir):
            continue
        for attempt in range(3):
            try:
                shutil.rmtree(lock_dir, ignore_errors=False)
                break
            except (OSError, PermissionError):
                time.sleep(0.3 * (attempt + 1))

def _wait_for_url(url: str, timeout: float = 20.0) -> bool:
    """Poll until the URL responds or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False

def _force_exit(*_):
    """Force-kill the process — used for SIGINT (Ctrl+C) and window-close."""
    try:
        from src.api.server import stop_server
        stop_server()
    except Exception:
        pass
    # Give uvicorn 0.5 s to flush; then hard-exit regardless
    time.sleep(0.5)
    os._exit(0)


def launch(dev_mode: bool = False):
    """Start the FastAPI server and open pywebview pointing to the React app."""
    # Register SIGINT (Ctrl+C) handler so the terminal can kill the process cleanly
    signal.signal(signal.SIGINT,  _force_exit)
    try:
        signal.signal(signal.SIGTERM, _force_exit)
    except (AttributeError, OSError):
        pass   # SIGTERM not available on all platforms

    from src.api.server import run_server

    # Start API server in background thread
    run_server(host="127.0.0.1", port=7472)

    # Wait for the API to be ready before creating the window
    ready = _wait_for_url("http://127.0.0.1:7472/api/health", timeout=20.0)
    if not ready:
        print("[CortaCerto] ERRO: servidor API não respondeu em 20 s. Verifique a porta 7472.")

    try:
        import webview
    except ImportError:
        print("[CortaCerto] pywebview not installed. Opening in browser instead.")
        import webbrowser
        # Always use 127.0.0.1 — avoids localhost→::1 IPv6 resolution on Windows
        url = "http://127.0.0.1:5173" if dev_mode else "http://127.0.0.1:7472"
        webbrowser.open(url)
        input("Press Enter to quit...")
        return

    if dev_mode:
        # In dev mode, wait for Vite to be ready too
        _wait_for_url("http://127.0.0.1:5173", timeout=30.0)
        # Use 127.0.0.1, not localhost — Windows may resolve localhost→::1 (IPv6)
        # while uvicorn/vite only listen on IPv4
        url = "http://127.0.0.1:5173"
    else:
        # Production: serve from the API server (avoids file:// CORS issues)
        # Always 127.0.0.1 — never "localhost" to avoid IPv6 resolution issues
        url = "http://127.0.0.1:7472"

    window = webview.create_window(
        "CortaCerto",
        url=url,
        width=1440,
        height=860,
        min_size=(900, 600),
        background_color="#0d0d0d",
    )

    # Give the API server a reference to the pywebview window so it can open
    # native file dialogs via webview.OPEN_DIALOG / webview.SAVE_DIALOG.
    # This is safe to call before webview.start() — the window object already
    # exists; create_file_dialog will only be called after start() is running.
    try:
        from src.api.server import set_webview_window
        set_webview_window(window)
    except Exception:
        pass

    # Best-effort cleanup of stale lock dirs from previous crashed runs.
    _cleanup_stale_webview_folder()

    # Use a controlled user-data folder so pywebview doesn't try to delete a
    # fresh tempdir on exit (Windows refuses with WinError 32 because WebView2
    # child processes haven't released their handles yet).
    try:
        webview.start(
            debug=dev_mode,
            private_mode=False,                  # persistent storage between launches
            storage_path=_webview_user_data_dir(),
        )
    except TypeError:
        # Older pywebview versions don't accept storage_path → fall back
        webview.start(debug=dev_mode)

    # Window was closed — force-exit so no daemon thread keeps the process alive
    _force_exit()

def _dist_index() -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "web", "dist", "index.html").replace("\\", "/")
