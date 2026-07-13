"""Tests for cloud provider credential wiring."""

from agentforge.config import settings
from agentforge.llm.cloud_providers import (
    apply_cloud_credentials,
    cloud_key_flags,
    detect_provider_from_model,
)


def test_detect_provider_from_model() -> None:
    """Model strings map to the correct cloud provider."""
    anthropic = detect_provider_from_model("anthropic/claude-3-5-haiku-20241022")
    assert anthropic is not None
    assert anthropic.id == "anthropic"
    openai = detect_provider_from_model("gpt-4o-mini")
    assert openai is not None
    assert openai.id == "openai"
    gemini = detect_provider_from_model("gemini/gemini-2.0-flash")
    assert gemini is not None
    assert gemini.id == "gemini"
    assert detect_provider_from_model("ollama/llama3.1:8b") is None


def test_cloud_key_flags(monkeypatch) -> None:
    """Settings API flags reflect configured provider keys."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    flags = cloud_key_flags()
    assert flags["has_openai_key"] is True
    assert flags["has_anthropic_key"] is False


def test_apply_cloud_credentials(monkeypatch) -> None:
    """Configured keys are exported to LiteLLM environment variables."""
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama:11434")
    apply_cloud_credentials()
    import os

    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test"
    assert os.environ.get("OLLAMA_API_BASE") == "http://ollama:11434"
