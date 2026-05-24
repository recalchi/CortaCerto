from __future__ import annotations

import sys
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _latest_mtime(paths: list[Path]) -> float:
    latest = 0.0
    for path in paths:
        if not path.exists():
            continue
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
            continue
        for child in path.rglob("*"):
            if child.is_file() and "node_modules" not in child.parts and "dist" not in child.parts:
                latest = max(latest, child.stat().st_mtime)
    return latest


def _ensure_web_build_current() -> None:
    """Build React when source files are newer than web/dist."""
    root = _project_root()
    web_dir = root / "web"
    dist_index = web_dir / "dist" / "index.html"
    if not (web_dir / "package.json").exists():
        return

    source_inputs = [
        web_dir / "src",
        web_dir / "public",
        web_dir / "index.html",
        web_dir / "package.json",
        web_dir / "package-lock.json",
        web_dir / "vite.config.ts",
        web_dir / "tailwind.config.js",
        web_dir / "postcss.config.js",
        web_dir / "tsconfig.json",
        web_dir / "tsconfig.app.json",
        web_dir / "tsconfig.node.json",
    ]
    source_mtime = _latest_mtime(source_inputs)
    dist_mtime = dist_index.stat().st_mtime if dist_index.exists() else 0.0
    if dist_mtime >= source_mtime:
        return

    npm = "npm.cmd" if os.name == "nt" else "npm"
    print("[CortaCerto] Build web desatualizado; executando npm run build...")
    try:
        subprocess.run([npm, "run", "build"], cwd=str(web_dir), check=True)
    except Exception as exc:
        print(f"[CortaCerto] AVISO: nao foi possivel atualizar web/dist automaticamente: {exc}")


def launch(dev_mode: bool = False):
    """Start the FastAPI server and open pywebview pointing to the React app."""
    # Register SIGINT (Ctrl+C) handler so the terminal can kill the process cleanly
    signal.signal(signal.SIGINT,  _force_exit)
    try:
        signal.signal(signal.SIGTERM, _force_exit)
    except (AttributeError, OSError):
        pass   # SIGTERM not available on all platforms

    if not dev_mode:
        _ensure_web_build_current()

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
        url = f"http://127.0.0.1:7472/?v={int(time.time())}"

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
