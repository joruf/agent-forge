"""Tests for LiteLLM compatibility helpers."""

from agentforge.llm.litellm_compat import ensure_litellm_proxy_package


def test_ensure_litellm_proxy_package_registers_module() -> None:
    """Missing litellm_proxy package name is registered for provider resolution."""
    import sys

    sys.modules.pop("litellm.llms.litellm_proxy", None)
    ensure_litellm_proxy_package()
    assert "litellm.llms.litellm_proxy" in sys.modules

    import litellm

    config = litellm.LiteLLMProxyChatConfig
    assert config._should_use_litellm_proxy_by_default() is False
