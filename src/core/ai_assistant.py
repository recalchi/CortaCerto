from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..api_settings import ApiCredential, load_api_credentials


@dataclass(frozen=True)
class AiSuggestionRequest:
    video_path: str
    project_name: str = ""
    platform: str = "youtube"
    transcript_excerpt: str = ""


@dataclass(frozen=True)
class AiSuggestion:
    title: str
    subtitle: str
    description: str
    provider: str


class AiProvider:
    name = "local"

    def suggest_metadata(self, request: AiSuggestionRequest) -> AiSuggestion:
        raise NotImplementedError


class LocalHeuristicProvider(AiProvider):
    name = "local"

    def suggest_metadata(self, request: AiSuggestionRequest) -> AiSuggestion:
        base = request.project_name.strip() or Path(request.video_path).stem
        title = _clean_title(base)
        subtitle = _subtitle_for_platform(request.platform)
        description = _description_for(title, request.transcript_excerpt)
        return AiSuggestion(title=title, subtitle=subtitle, description=description, provider=self.name)


class ConfiguredApiProvider(AiProvider):
    """Placeholder provider boundary for real API calls.

    It deliberately does not perform network calls yet; this keeps tests and
    editor flows deterministic while the project gains the integration surface.
    """

    def __init__(self, credential: ApiCredential) -> None:
        self.credential = credential
        self.name = _provider_name_from_env(credential.name)

    def suggest_metadata(self, request: AiSuggestionRequest) -> AiSuggestion:
        local = LocalHeuristicProvider().suggest_metadata(request)
        return AiSuggestion(
            title=local.title,
            subtitle=local.subtitle,
            description=local.description,
            provider=f"{self.name}:configured",
        )


def build_ai_provider(credentials: list[ApiCredential] | None = None) -> AiProvider:
    configured = credentials if credentials is not None else load_api_credentials()
    if configured:
        return ConfiguredApiProvider(configured[0])
    return LocalHeuristicProvider()


def suggest_metadata(request: AiSuggestionRequest, provider: AiProvider | None = None) -> AiSuggestion:
    active_provider = provider or build_ai_provider()
    return active_provider.suggest_metadata(request)


def _clean_title(value: str) -> str:
    words = [
        word.capitalize()
        for word in value.replace("_", " ").replace("-", " ").split()
        if word.strip()
    ]
    return " ".join(words[:9]) or "Novo Corte"


def _subtitle_for_platform(platform: str) -> str:
    normalized = str(platform or "").lower()
    if normalized in {"reels", "tiktok", "shorts"}:
        return "Corte vertical"
    return "Corte principal"


def _description_for(title: str, transcript_excerpt: str) -> str:
    excerpt = " ".join(str(transcript_excerpt or "").split())[:240]
    if excerpt:
        return f"{title}\n\nResumo: {excerpt}"
    return f"{title}\n\nDescrição gerada localmente para revisão antes da publicação."


def _provider_name_from_env(env_name: str) -> str:
    name = env_name.upper()
    if "OPENAI" in name:
        return "openai"
    if "GEMINI" in name:
        return "gemini"
    if "ANTHROPIC" in name:
        return "anthropic"
    if "ELEVENLABS" in name:
        return "elevenlabs"
    return "api"
