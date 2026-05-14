from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping


APP_NAME = "CortaCerto"
ERROR_LOG_FILENAME = "errors.jsonl"
MAX_LOG_BYTES = 512 * 1024
MAX_TEXT_CHARS = 4000
SENSITIVE_KEY_PARTS = ("api", "key", "token", "secret", "password", "senha", "env")

_previous_sys_excepthook = sys.excepthook
_previous_threading_excepthook = getattr(threading, "excepthook", None)
_hooks_installed = False


def default_error_log_dir(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    override = str(source.get("CORTACERTO_ERROR_LOG_DIR", "") or "").strip()
    if override:
        return Path(override)
    appdata = str(source.get("LOCALAPPDATA", "") or source.get("APPDATA", "") or "").strip()
    if appdata:
        return Path(appdata) / APP_NAME / "logs"
    return Path.home() / ".cortacerto" / "logs"


def error_log_path(log_dir: str | Path | None = None) -> Path:
    root = Path(log_dir) if log_dir is not None else default_error_log_dir()
    return root / ERROR_LOG_FILENAME


def record_error(
    exc: BaseException,
    *,
    where: str,
    context: Mapping[str, Any] | None = None,
    log_dir: str | Path | None = None,
) -> Path:
    event = {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "severity": "error",
        "where": _safe_text(where),
        "type": type(exc).__name__,
        "message": _safe_text(str(exc)),
        "traceback": _safe_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))),
        "context": _sanitize_context(context or {}),
    }
    return append_error_event(event, log_dir=log_dir)


def record_error_message(
    message: str,
    *,
    where: str,
    context: Mapping[str, Any] | None = None,
    log_dir: str | Path | None = None,
) -> Path:
    event = {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "severity": "error",
        "where": _safe_text(where),
        "type": "Message",
        "message": _safe_text(message),
        "context": _sanitize_context(context or {}),
    }
    return append_error_event(event, log_dir=log_dir)


def append_error_event(event: Mapping[str, Any], *, log_dir: str | Path | None = None) -> Path:
    path = error_log_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(event), ensure_ascii=True, sort_keys=True))
        handle.write("\n")
    return path


def install_error_hooks(
    *,
    root: Any | None = None,
    context_fn: Callable[[], Mapping[str, Any]] | None = None,
    log_dir: str | Path | None = None,
    show_callback_error: Callable[[str, str], None] | None = None,
) -> Path:
    global _hooks_installed
    path = error_log_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    def context() -> Mapping[str, Any]:
        if context_fn is None:
            return {}
        try:
            return context_fn()
        except Exception as exc:
            return {"context_error": str(exc)}

    if root is not None:
        def tk_report_callback_exception(exc_type, exc, tb) -> None:
            if not isinstance(exc, BaseException):
                exc = RuntimeError(str(exc))
            exc.__traceback__ = tb
            record_error(exc, where="tk_callback", context=context(), log_dir=log_dir)
            if show_callback_error is not None:
                show_callback_error("Erro no CortaCerto", "O erro foi registrado para diagnostico.")

        root.report_callback_exception = tk_report_callback_exception

    if not _hooks_installed:
        def sys_hook(exc_type, exc, tb) -> None:
            if isinstance(exc, BaseException):
                exc.__traceback__ = tb
                record_error(exc, where="unhandled_exception", context=context(), log_dir=log_dir)
            if _previous_sys_excepthook is not None:
                _previous_sys_excepthook(exc_type, exc, tb)

        def thread_hook(args) -> None:
            exc = args.exc_value
            if isinstance(exc, BaseException):
                exc.__traceback__ = args.exc_traceback
                record_error(exc, where=f"thread:{getattr(args.thread, 'name', '')}", context=context(), log_dir=log_dir)
            if _previous_threading_excepthook is not None:
                _previous_threading_excepthook(args)

        sys.excepthook = sys_hook
        if hasattr(threading, "excepthook"):
            threading.excepthook = thread_hook
        _hooks_installed = True

    return path


def _rotate_if_needed(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size <= MAX_LOG_BYTES:
            return
        rotated = path.with_suffix(path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        path.replace(rotated)
    except OSError:
        return


def _sanitize_context(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, raw in value.items():
            key_text = _safe_text(str(key), limit=120)
            if _is_sensitive_key(key_text):
                clean[key_text] = "[redacted]"
            else:
                clean[key_text] = _sanitize_context(raw)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize_context(item) for item in value[:40]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _safe_text(value) if isinstance(value, str) else value
    return _safe_text(repr(value))


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _safe_text(value: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"
