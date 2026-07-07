from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from src.api_settings import load_env_file, update_env_file
from src.core.stock_assets import stock_cache_root
from src.core.error_log import default_error_log_dir, error_log_path


GENERAL_SETTING_ENV_NAMES = [
    "CORTACERTO_AUTO_UPDATES",
    "CORTACERTO_UPDATE_NOTIFICATIONS",
    "CORTACERTO_UI_GPU_RENDERING",
    "CORTACERTO_DEFAULT_SAVE_DIR",
    "CORTACERTO_STARTUP_LAYOUT",
    "CORTACERTO_UI_THEME",
]


def remember_default_save_dir(path_or_dir: str, env_file: Path = Path(".env")) -> str:
    """Persist the directory used by the latest project/export save action."""
    raw = str(path_or_dir or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    candidate = Path(os.path.normpath(raw))
    directory = candidate if candidate.is_dir() else candidate.parent
    if not str(directory):
        return ""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        update_env_file({"CORTACERTO_DEFAULT_SAVE_DIR": str(directory)}, env_file)
        return str(directory)
    except Exception:
        return ""


def general_settings(env_file: Path = Path(".env")) -> dict[str, Any]:
    values = load_env_file(env_file)
    return {
        "auto_updates": _bool_value(_setting_value("CORTACERTO_AUTO_UPDATES", values), False),
        "update_notifications": _bool_value(_setting_value("CORTACERTO_UPDATE_NOTIFICATIONS", values), True),
        "ui_gpu_rendering": _bool_value(_setting_value("CORTACERTO_UI_GPU_RENDERING", values), False),
        "default_save_dir": _setting_value("CORTACERTO_DEFAULT_SAVE_DIR", values),
        "startup_layout": _layout_value(_setting_value("CORTACERTO_STARTUP_LAYOUT", values)),
        "ui_theme": _theme_value(_setting_value("CORTACERTO_UI_THEME", values)),
        "gpu": detect_gpu_info(),
        "cache": cache_info(),
        "logs": {
            "dir": str(default_error_log_dir()),
            "file": str(error_log_path()),
        },
    }


def cache_info() -> dict[str, Any]:
    stock_root = stock_cache_root()
    proxy_files = [Path(path) for path in glob.glob(str(Path(tempfile.gettempdir()) / "cc_proxy_*.mp4"))]
    stock_bytes = _dir_size(stock_root)
    proxy_bytes = sum(_safe_file_size(path) for path in proxy_files)
    total = stock_bytes + proxy_bytes
    return {
        "total_bytes": total,
        "total_mb": round(total / (1024 * 1024), 2),
        "stock_bytes": stock_bytes,
        "proxy_bytes": proxy_bytes,
        "stock_root": str(stock_root),
        "proxy_count": len(proxy_files),
    }


def clear_cache() -> dict[str, Any]:
    root = stock_cache_root()
    if root.exists():
        shutil.rmtree(root)
    for path in glob.glob(str(Path(tempfile.gettempdir()) / "cc_proxy_*.mp4")):
        try:
            Path(path).unlink()
        except OSError:
            pass
    return cache_info()


def detect_gpu_info() -> dict[str, Any]:
    names: list[str] = []
    if os.name == "nt":
        names = _detect_windows_gpus()
    return {
        "platform": platform.platform(),
        "detected": bool(names),
        "names": names,
        "label": ", ".join(names) if names else "GPU nao identificada",
    }


def _detect_windows_gpus() -> list[str]:
    commands = [
        ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
        ["wmic", "path", "win32_VideoController", "get", "name"],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=4)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        names = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and line.strip().lower() != "name"
        ]
        if names:
            return names[:4]
    return []


def _setting_value(name: str, values: dict[str, str]) -> str:
    return str(os.environ.get(name) or values.get(name) or "").strip()


def _bool_value(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _layout_value(value: str) -> str:
    clean = value.strip().lower()
    return clean if clean in {"last", "default", "capcut"} else "last"


def _theme_value(value: str) -> str:
    clean = value.strip().lower()
    return clean if clean in {"violet", "graphite", "midnight", "emerald"} else "violet"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        total += _safe_file_size(child)
    return total


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0
