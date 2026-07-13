"""Tests for Ollama timeout and multi-agent load tuning."""

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.config import settings
from agentforge.llm.provider import LLMProvider


def test_resolve_multi_rounds_for_ollama(monkeypatch) -> None:
    """Local Ollama uses fewer multi-agent rounds to avoid timeouts."""
    monkeypatch.setattr(settings, "override_model", "ollama/llama3.2:1b-instruct-q4_K_M")
    monkeypatch.setattr(settings, "multi_agent_max_rounds_ollama", 2)
    orchestrator = AgentOrchestrator()
    assert orchestrator._resolve_multi_rounds() == 2


def test_effective_tool_round_limit_for_multi_reviewer() -> None:
    """Reviewers in multi-agent mode get a single tool iteration."""
    orchestrator = AgentOrchestrator()
    assert orchestrator._effective_tool_round_limit("reviewer", False, True) == 1
    assert orchestrator._effective_tool_round_limit("developer", False, True) == 6


def test_timeout_error_detection() -> None:
    """Timeout exceptions are detected for retry logic."""
    assert LLMProvider._is_timeout_error(TimeoutError("timed out")) is True
    assert LLMProvider._format_llm_error(TimeoutError("Connection timed out")).startswith(
        "LLM error:"
    )
