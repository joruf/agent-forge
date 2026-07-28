"""Deterministic mock LLM used when no real model backend is configured."""

from __future__ import annotations

from typing import Any


def mock_complete(messages: list[dict[str, str]], model: str) -> dict[str, Any]:
    """
    Build a deterministic mock completion without calling any real LLM.

    :param messages: OpenAI-style message list
    :param model: Requested mock model string
    :return: Response dict shaped like LLMProvider.complete()'s return value
    """
    last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    role_hint = system.split(".")[0][:60] if system else "assistant"
    # No leading "{"/"[" — that would look like a text-JSON tool call to
    # orchestrator._stream_llm_complete's suppression heuristic.
    content = f"Mock reply from {model} ({role_hint}) — replying to: {last_user[:200]}"
    return {"content": content, "tool_calls": [], "model": model}
