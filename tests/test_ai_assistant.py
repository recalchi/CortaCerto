import unittest

from src.api_settings import ApiCredential
from src.core.ai_assistant import (
    AiSuggestionRequest,
    ConfiguredApiProvider,
    LocalHeuristicProvider,
    build_ai_provider,
    suggest_metadata,
)


class AiAssistantTests(unittest.TestCase):
    def test_local_provider_suggests_metadata_from_filename(self) -> None:
        suggestion = LocalHeuristicProvider().suggest_metadata(
            AiSuggestionRequest(video_path="C:/videos/meu-video-final.mp4", platform="youtube")
        )

        self.assertEqual(suggestion.title, "Meu Video Final")
        self.assertEqual(suggestion.subtitle, "Corte principal")
        self.assertEqual(suggestion.provider, "local")

    def test_local_provider_uses_vertical_subtitle_for_short_platforms(self) -> None:
        suggestion = suggest_metadata(
            AiSuggestionRequest(video_path="clip.mp4", platform="tiktok"),
            provider=LocalHeuristicProvider(),
        )

        self.assertEqual(suggestion.subtitle, "Corte vertical")

    def test_configured_provider_marks_api_without_network_call(self) -> None:
        provider = ConfiguredApiProvider(ApiCredential("OPENAI_API_KEY", "sk-pro...1234", "env"))

        suggestion = provider.suggest_metadata(AiSuggestionRequest(video_path="clip.mp4"))

        self.assertEqual(suggestion.provider, "openai:configured")
        self.assertTrue(suggestion.title)

    def test_build_ai_provider_uses_first_configured_credential(self) -> None:
        provider = build_ai_provider([ApiCredential("GEMINI_API_KEY", "gemin...1234", ".env")])

        self.assertIsInstance(provider, ConfiguredApiProvider)
        self.assertEqual(provider.name, "gemini")


if __name__ == "__main__":
    unittest.main()
