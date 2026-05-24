from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from src.api_settings import load_env_file


OPENAI_USAGE_ENV_NAMES = [
    "OPENAI_API_KEY",
    "OPENAI_MONTHLY_BUDGET_USD",
    "OPENAI_GPT_INPUT_USD_PER_1K",
    "OPENAI_GPT_OUTPUT_USD_PER_1K",
    "OPENAI_WHISPER_USD_PER_MIN",
]

DEFAULT_OPENAI_RATES = {
    "OPENAI_MONTHLY_BUDGET_USD": "5.00",
    "OPENAI_GPT_INPUT_USD_PER_1K": "0.00",
    "OPENAI_GPT_OUTPUT_USD_PER_1K": "0.00",
    "OPENAI_WHISPER_USD_PER_MIN": "0.006",
}


def usage_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base) / "CortaCerto" / "logs"
    else:
        root = Path.home() / ".cortacerto" / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root / "api_usage.jsonl"


def openai_usage_settings(env_file: Path = Path(".env")) -> dict[str, Any]:
    values = load_env_file(env_file)
    return {
        "keys": {
            name: {
                "configured": bool(os.environ.get(name) or values.get(name)),
                "value": "" if name == "OPENAI_API_KEY" else str(os.environ.get(name) or values.get(name) or DEFAULT_OPENAI_RATES.get(name, "")),
            }
            for name in OPENAI_USAGE_ENV_NAMES
        },
        "log_path": str(usage_log_path()),
    }


def record_openai_usage(
    *,
    feature: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    audio_seconds: float = 0.0,
    estimated_cost_usd: float | None = None,
    ok: bool = True,
) -> dict[str, Any]:
    if estimated_cost_usd is None:
        estimated_cost_usd = estimate_openai_cost(input_tokens, output_tokens, audio_seconds)
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "month": time.strftime("%Y-%m"),
        "provider": "openai",
        "feature": str(feature or "unknown"),
        "model": str(model or ""),
        "input_tokens": int(max(0, input_tokens)),
        "output_tokens": int(max(0, output_tokens)),
        "audio_seconds": round(max(0.0, float(audio_seconds or 0.0)), 3),
        "estimated_cost_usd": round(max(0.0, float(estimated_cost_usd or 0.0)), 6),
        "ok": bool(ok),
    }
    with usage_log_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def openai_usage_summary(limit: int = 50) -> dict[str, Any]:
    events = _read_usage_events()
    current_month = time.strftime("%Y-%m")
    month_events = [event for event in events if event.get("month") == current_month]
    total_cost = sum(float(event.get("estimated_cost_usd") or 0.0) for event in month_events)
    total_input_tokens = sum(int(event.get("input_tokens") or 0) for event in month_events)
    total_output_tokens = sum(int(event.get("output_tokens") or 0) for event in month_events)
    total_audio_seconds = sum(float(event.get("audio_seconds") or 0.0) for event in month_events)
    settings = openai_usage_settings()
    budget = _float_setting(settings, "OPENAI_MONTHLY_BUDGET_USD")
    return {
        "month": current_month,
        "calls": len(month_events),
        "estimated_cost_usd": round(total_cost, 6),
        "monthly_budget_usd": budget,
        "budget_used_pct": round((total_cost / budget) * 100.0, 2) if budget > 0 else 0.0,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "audio_seconds": round(total_audio_seconds, 3),
        "events": events[-max(1, min(int(limit or 50), 200)):][::-1],
        "log_path": str(usage_log_path()),
    }


def estimate_openai_cost(input_tokens: int = 0, output_tokens: int = 0, audio_seconds: float = 0.0) -> float:
    settings = openai_usage_settings()
    input_rate = _float_setting(settings, "OPENAI_GPT_INPUT_USD_PER_1K")
    output_rate = _float_setting(settings, "OPENAI_GPT_OUTPUT_USD_PER_1K")
    whisper_rate = _float_setting(settings, "OPENAI_WHISPER_USD_PER_MIN")
    return (
        (max(0, int(input_tokens or 0)) / 1000.0) * input_rate
        + (max(0, int(output_tokens or 0)) / 1000.0) * output_rate
        + (max(0.0, float(audio_seconds or 0.0)) / 60.0) * whisper_rate
    )


def _read_usage_events() -> list[dict[str, Any]]:
    path = usage_log_path()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("provider") == "openai":
            events.append(event)
    return events


def _float_setting(settings: dict[str, Any], name: str) -> float:
    value = settings.get("keys", {}).get(name, {}).get("value", "")
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return float(DEFAULT_OPENAI_RATES.get(name, "0") or 0.0)
