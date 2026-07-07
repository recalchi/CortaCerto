from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


def _webview_user_data_dir() -> str:
    """Return a stable directory for WebView2 user data."""
    base = os.path.join(tempfile.gettempdir(), "cortacerto_webview2")
    os.makedirs(base, exist_ok=True)
    return base


def _cleanup_stale_webview_folder() -> None:
    """Best-effort cleanup of stale WebView2 lock directories."""
    base = _webview_user_data_dir()
    if not os.path.isdir(base):
        return
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
    """Poll until URL responds or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _force_exit(*_) -> None:
    """Force-kill process, used for Ctrl+C and window close."""
    try:
        from src.api.server import stop_server
        stop_server()
    except Exception:
        pass
    time.sleep(0.5)
    os._exit(0)


def _reopen_manager_process() -> None:
    """Open a fresh project-manager flow after the editor window is closed."""
    try:
        root = _project_root()
        subprocess.Popen(
            [sys.executable, str(root / "main.py"), "--web"],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception:
        pass


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
    """Build React when web sources are newer than web/dist."""
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


def _pick_startup_target() -> dict[str, str] | None:
    """Open project manager first and return selection for web bootstrap."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        from .project_manager import ProjectManagerScreen, register_recent_project
    except Exception:
        # Fallback: if manager UI cannot be created, continue with normal editor boot.
        return {"kind": "none", "path": "", "name": ""}

    selection: dict[str, str] = {}
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

    root = tk.Tk()
    root.title("CortaCerto")
    root.geometry("1280x780")
    root.minsize(1000, 660)
    root.configure(bg="#0c0b0f")

    def _finish(kind: str, path: str, name: str = "") -> None:
        if not path:
            return
        selection["kind"] = kind
        selection["path"] = path
        if name:
            selection["name"] = name
        try:
            root.quit()
        except Exception:
            pass

    def _on_open(path: str) -> None:
        ext = Path(path).suffix.lower()
        kind = "video" if ext in video_exts else "project"
        try:
            register_recent_project(path)
        except Exception:
            pass
        _finish(kind, path)

    def _on_create(path: str, name: str, category: str, template: str) -> None:
        try:
            if path:
                register_recent_project(path, name=name, category=category, status="draft")
        except Exception:
            pass
        selection["kind"] = "new"
        selection["path"] = path or ""
        selection["name"] = name or "Projeto sem nome"
        selection["category"] = category or "youtube"
        selection["template"] = template or "blank"
        try:
            root.quit()
        except Exception:
            pass

    def _on_quick() -> None:
        path = filedialog.askopenfilename(
            title="Abrir video",
            filetypes=[("Videos", "*.mp4;*.mov;*.avi;*.mkv;*.webm;*.m4v"), ("Todos", "*.*")],
        )
        if path:
            _finish("video", path)

    def _on_restore() -> None:
        path = filedialog.askopenfilename(
            title="Abrir projeto",
            filetypes=[("Projeto CortaCerto", "*.ccproj;*.ccp;*.json"), ("Todos", "*.*")],
        )
        if path:
            _finish("project", path)

    ProjectManagerScreen(
        root,
        on_open=_on_open,
        on_create=_on_create,
        on_quick=_on_quick,
        on_restore=_on_restore,
    )
    root.protocol("WM_DELETE_WINDOW", root.quit)
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass
    if not selection:
        return None
    return selection


def launch(dev_mode: bool = False) -> None:
    """Start FastAPI and open pywebview with manager -> editor flow."""
    signal.signal(signal.SIGINT, _force_exit)
    try:
        signal.signal(signal.SIGTERM, _force_exit)
    except (AttributeError, OSError):
        pass

    try:
        import webview
    except ImportError:
        print("[CortaCerto] pywebview nao esta instalado neste Python.")
        print("[CortaCerto] Para abrir na interface normal, instale/ative pywebview no venv do projeto.")
        return

    startup_target = _pick_startup_target()
    if startup_target is None:
        return

    if not dev_mode:
        _ensure_web_build_current()

    from src.api.server import run_server

    run_server(host="127.0.0.1", port=7472)
    ready = _wait_for_url("http://127.0.0.1:7472/api/health", timeout=20.0)
    if not ready:
        print("[CortaCerto] ERRO: servidor API nao respondeu em 20s. Verifique porta 7472.")

    startup_qs = urllib.parse.urlencode({
        "boot_kind": startup_target.get("kind", ""),
        "boot_path": startup_target.get("path", ""),
        "boot_name": startup_target.get("name", ""),
    })

    if dev_mode:
        _wait_for_url("http://127.0.0.1:5173", timeout=30.0)
        app_url = f"http://127.0.0.1:5173/?{startup_qs}"
    else:
        app_url = f"http://127.0.0.1:7472/?v={int(time.time())}&{startup_qs}"

    window = webview.create_window(
        "CortaCerto",
        url=app_url,
        width=1440,
        height=860,
        min_size=(900, 600),
        background_color="#0d0d0d",
    )
    reopen_on_close = {"value": True}

    def _on_editor_started() -> None:
        try:
            window.maximize()
        except Exception:
            pass

    try:
        from src.api.server import set_webview_window
        set_webview_window(window)
    except Exception:
        pass

    _cleanup_stale_webview_folder()

    try:
        webview.start(
            _on_editor_started,
            debug=dev_mode,
            private_mode=False,
            storage_path=_webview_user_data_dir(),
        )
    except TypeError:
        webview.start(_on_editor_started, debug=dev_mode)

    if reopen_on_close["value"]:
        _reopen_manager_process()
    _force_exit()


def _dist_index() -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "web", "dist", "index.html").replace("\\", "/")
