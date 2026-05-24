from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_API_ENV_NAMES = [
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_SECONDARY",
    "OPENAI_MONTHLY_BUDGET_USD",
    "OPENAI_GPT_INPUT_USD_PER_1K",
    "OPENAI_GPT_OUTPUT_USD_PER_1K",
    "OPENAI_WHISPER_USD_PER_MIN",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "CORTACERTO_AUTO_UPDATES",
    "CORTACERTO_UPDATE_NOTIFICATIONS",
    "CORTACERTO_UI_GPU_RENDERING",
    "CORTACERTO_DEFAULT_SAVE_DIR",
    "CORTACERTO_STARTUP_LAYOUT",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "UNSPLASH_APP_ID",
    "UNSPLASH_ACCESS_KEY",
    "UNSPLASH_SECRET_KEY",
    "FREESOUND_API_KEY",
    "FREESOUND_CLIENT_ID",
    "FREESOUND_CLIENT_SECRET",
]


@dataclass(frozen=True)
class ApiCredential:
    name: str
    masked_value: str
    source: str


def load_env_file(path: Path = Path(".env")) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    # utf-8-sig strips the BOM that Windows editors (Notepad, PowerShell `Out-File`) prepend
    # to UTF-8 files; without this the first key parses as "﻿OPENAI_API_KEY" and the
    # subsequent .get("OPENAI_API_KEY") lookup silently misses.
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        parsed = _parse_env_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def load_api_credentials(
    env_names: list[str] | None = None,
    env_file: Path = Path(".env"),
    environ: dict[str, str] | None = None,
) -> list[ApiCredential]:
    names = env_names or DEFAULT_API_ENV_NAMES
    current_env = os.environ if environ is None else environ
    file_values = load_env_file(env_file)
    credentials: list[ApiCredential] = []
    for name in names:
        value = str(current_env.get(name) or file_values.get(name) or "").strip()
        if not value:
            continue
        source = "env" if current_env.get(name) else str(env_file)
        credentials.append(ApiCredential(name=name, masked_value=mask_secret(value), source=source))
    return credentials


def api_credentials_summary(credentials: list[ApiCredential]) -> str:
    if not credentials:
        return "Nenhuma API configurada."
    names = ", ".join(f"{credential.name} ({credential.masked_value})" for credential in credentials)
    return f"{len(credentials)} API(s) configurada(s): {names}."


def mask_secret(value: str) -> str:
    clean = str(value or "").strip().strip('"').strip("'")
    if len(clean) <= 10:
        return "***"
    return f"{clean[:6]}...{clean[-4:]}"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if not key:
        return None
    return key, value


def update_env_file(updates: dict[str, str], path: Path = Path(".env")) -> None:
    """Upsert dotenv values without logging secrets."""
    existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    remaining = dict(updates)
    output: list[str] = []

    for line in existing_lines:
        parsed = _parse_env_line(line)
        if parsed is None:
            output.append(line)
            continue
        key, _value = parsed
        if key in remaining:
            output.append(f'{key}="{remaining.pop(key)}"')
        else:
            output.append(line)

    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# Stock media APIs")
        for key, value in remaining.items():
            output.append(f'{key}="{value}"')

    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
