from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home()) / "CortaCerto"
    base.mkdir(parents=True, exist_ok=True)
    return base


def usage_path() -> Path:
    return _app_data_dir() / "project_usage.json"


def _project_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def load_usage() -> dict[str, Any]:
    try:
        data = json.loads(usage_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("projects", {})
            data.setdefault("total_seconds", 0.0)
            return data
    except Exception:
        pass
    return {"projects": {}, "total_seconds": 0.0}


def save_usage(data: dict[str, Any]) -> None:
    try:
        usage_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def record_project_ping(project_path: str, project_name: str = "", max_delta_s: float = 45.0) -> dict[str, Any]:
    path = os.path.normpath(project_path)
    key = _project_key(path)
    now = time.time()
    data = load_usage()
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(key, {
        "path": path,
        "name": project_name or Path(path).stem,
        "total_seconds": 0.0,
        "last_ping_at": now,
        "updated_at": now,
    })

    last_ping = float(entry.get("last_ping_at") or now)
    delta = max(0.0, min(float(max_delta_s), now - last_ping))
    entry["path"] = path
    entry["name"] = project_name or entry.get("name") or Path(path).stem
    entry["total_seconds"] = float(entry.get("total_seconds") or 0.0) + delta
    entry["last_ping_at"] = now
    entry["updated_at"] = now

    data["total_seconds"] = sum(float(p.get("total_seconds") or 0.0) for p in projects.values())
    save_usage(data)
    return {
        "ok": True,
        "path": path,
        "project_seconds": entry["total_seconds"],
        "total_seconds": data["total_seconds"],
    }


def usage_summary() -> dict[str, Any]:
    data = load_usage()
    projects = list((data.get("projects") or {}).values())
    latest = max(projects, key=lambda p: float(p.get("updated_at") or 0.0), default=None)
    return {
        "total_seconds": float(data.get("total_seconds") or 0.0),
        "latest_project": latest,
        "projects": projects,
    }
