"""Cloud LLM provider definitions for LiteLLM credential wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentforge.config import settings


@dataclass(frozen=True)
class CloudProvider:
    """Metadata for a LiteLLM-compatible cloud provider."""

    id: str
    settings_field: str
    env_var: str
    test_model: str
    model_prefixes: tuple[str, ...]
    optional_base_field: str | None = None
    optional_base_env: str | None = None


CLOUD_PROVIDERS: tuple[CloudProvider, ...] = (
    CloudProvider(
        id="openai",
        settings_field="openai_api_key",
        env_var="OPENAI_API_KEY",
        test_model="gpt-4o-mini",
        model_prefixes=("gpt-", "o1", "o3", "openai/"),
        optional_base_field="openai_api_base",
        optional_base_env="OPENAI_API_BASE",
    ),
    CloudProvider(
        id="anthropic",
        settings_field="anthropic_api_key",
        env_var="ANTHROPIC_API_KEY",
        test_model="anthropic/claude-3-5-haiku-20241022",
        model_prefixes=("anthropic/", "claude-"),
    ),
    CloudProvider(
        id="gemini",
        settings_field="gemini_api_key",
        env_var="GEMINI_API_KEY",
        test_model="gemini/gemini-2.0-flash",
        model_prefixes=("gemini/", "gemini-"),
    ),
    CloudProvider(
        id="groq",
        settings_field="groq_api_key",
        env_var="GROQ_API_KEY",
        test_model="groq/llama-3.1-8b-instant",
        model_prefixes=("groq/",),
    ),
    CloudProvider(
        id="mistral",
        settings_field="mistral_api_key",
        env_var="MISTRAL_API_KEY",
        test_model="mistral/mistral-small-latest",
        model_prefixes=("mistral/", "mistral-"),
    ),
)


def get_provider(provider_id: str) -> CloudProvider | None:
    """
    Return provider metadata by identifier.

    :param provider_id: Provider id such as openai or anthropic
    :return: Provider metadata or None
    """
    for provider in CLOUD_PROVIDERS:
        if provider.id == provider_id:
            return provider
    return None


def get_api_key(provider: CloudProvider, config: Any | None = None) -> str:
    """
    Read an API key from config object or global settings.

    :param provider: Cloud provider metadata
    :param config: Optional config object with provider fields
    :return: API key string
    """
    source = config or settings
    return str(getattr(source, provider.settings_field, "") or "")


def detect_provider_from_model(model_ref: str | None) -> CloudProvider | None:
    """
    Detect cloud provider from a LiteLLM model reference.

    :param model_ref: Model string such as anthropic/claude-3-5-haiku-20241022
    :return: Matching provider or None
    """
    if not model_ref:
        return None
    value = model_ref.strip().lower()
    if value.startswith("ollama/"):
        return None
    for provider in CLOUD_PROVIDERS:
        if any(value.startswith(prefix.lower()) for prefix in provider.model_prefixes):
            return provider
        if value.startswith(f"{provider.id}/"):
            return provider
    return None


def apply_cloud_credentials(config: Any | None = None) -> None:
    """
    Apply configured cloud provider credentials to process environment.

    :param config: Optional config object overriding global settings
    """
    import os

    source = config or settings
    if getattr(source, "ollama_base_url", None):
        os.environ["OLLAMA_API_BASE"] = str(source.ollama_base_url).rstrip("/")

    for provider in CLOUD_PROVIDERS:
        api_key = get_api_key(provider, source)
        if api_key:
            os.environ[provider.env_var] = api_key
        optional_base_field = provider.optional_base_field
        if optional_base_field:
            base_url = str(getattr(source, optional_base_field, "") or "")
            if base_url and provider.optional_base_env:
                os.environ[provider.optional_base_env] = base_url


def cloud_key_flags(config: Any | None = None) -> dict[str, bool]:
    """
    Return has-key flags for settings API responses.

    :param config: Optional config object overriding global settings
    :return: Mapping of has_<provider>_key booleans
    """
    source = config or settings
    return {
        f"has_{provider.id}_key": bool(get_api_key(provider, source))
        for provider in CLOUD_PROVIDERS
    }
