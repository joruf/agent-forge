"""Tests for LLMProvider request construction."""

from types import SimpleNamespace

import pytest

from agentforge.config import settings
from agentforge.llm.provider import LLMProvider
from agentforge.models.schemas import LLMConfig


def _fake_response(content: str = "ok") -> SimpleNamespace:
    """Build a minimal litellm-shaped completion response."""
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_complete_sets_num_ctx_for_ollama_models(monkeypatch) -> None:
    """Ollama-routed requests include the configured num_ctx."""
    monkeypatch.setattr(settings, "ollama_num_ctx", 8192)
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr("agentforge.llm.provider.litellm.acompletion", fake_acompletion)

    provider = LLMProvider(LLMConfig(model="ollama/llama3.1:8b"))
    await provider.complete([{"role": "user", "content": "hi"}])

    assert captured["num_ctx"] == 8192


@pytest.mark.asyncio
async def test_complete_omits_num_ctx_for_cloud_models(monkeypatch) -> None:
    """Non-Ollama requests never receive the Ollama-only num_ctx param."""
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr("agentforge.llm.provider.litellm.acompletion", fake_acompletion)

    provider = LLMProvider(LLMConfig(model="anthropic/claude-3-5-haiku-20241022"))
    await provider.complete([{"role": "user", "content": "hi"}])

    assert "num_ctx" not in captured


@pytest.mark.asyncio
async def test_complete_stream_sets_num_ctx_for_ollama_models(monkeypatch) -> None:
    """Streaming requests to Ollama also include num_ctx."""
    monkeypatch.setattr(settings, "ollama_num_ctx", 16384)
    captured: dict = {}

    async def fake_stream():
        return
        yield  # pragma: no cover - makes this an async generator

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return fake_stream()

    monkeypatch.setattr("agentforge.llm.provider.litellm.acompletion", fake_acompletion)

    provider = LLMProvider(LLMConfig(model="ollama/llama3.1:8b"))
    async for _ in provider.complete_stream([{"role": "user", "content": "hi"}]):
        pass

    assert captured["num_ctx"] == 16384


@pytest.mark.asyncio
async def test_complete_uses_mock_provider_without_calling_litellm(monkeypatch) -> None:
    """A mock/-prefixed model never reaches litellm and returns a deterministic reply."""

    async def fail_if_called(**kwargs):
        raise AssertionError("litellm.acompletion should not be called for mock models")

    monkeypatch.setattr("agentforge.llm.provider.litellm.acompletion", fail_if_called)

    provider = LLMProvider(LLMConfig(model="mock/mock-1"))
    result = await provider.complete(
        [
            {"role": "system", "content": "You are a developer."},
            {"role": "user", "content": "Write a hello world script"},
        ]
    )

    assert result["tool_calls"] == []
    assert result["model"] == "mock/mock-1"
    assert "Write a hello world script" in result["content"]


@pytest.mark.asyncio
async def test_complete_stream_uses_mock_provider_without_calling_litellm(monkeypatch) -> None:
    """Streaming with a mock/-prefixed model never reaches litellm."""

    async def fail_if_called(**kwargs):
        raise AssertionError("litellm.acompletion should not be called for mock models")

    monkeypatch.setattr("agentforge.llm.provider.litellm.acompletion", fail_if_called)

    provider = LLMProvider(LLMConfig(model="mock/mock-1"))
    events = [
        event
        async for event in provider.complete_stream([{"role": "user", "content": "hi"}])
    ]

    assert len(events) == 2
    assert events[0] == {"type": "content", "text": events[0]["text"]}
    assert "hi" in events[0]["text"]
    assert events[1] == {"type": "tool_calls", "calls": []}


@pytest.mark.asyncio
async def test_complete_stream_reconstructs_native_tool_calls(monkeypatch) -> None:
    """Streaming with tools reassembles a full tool call via litellm.stream_chunk_builder."""
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

    chunks = [
        ModelResponseStream(
            id="c1",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        role="assistant",
                        tool_calls=[
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "write_file", "arguments": ""},
                            }
                        ],
                    ),
                )
            ],
            model="test-model",
        ),
        ModelResponseStream(
            id="c1",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        tool_calls=[{"index": 0, "function": {"arguments": '{"path": "a.txt"}'}}],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            model="test-model",
        ),
    ]

    async def fake_stream():
        for chunk in chunks:
            yield chunk

    async def fake_acompletion(**kwargs):
        assert kwargs["tools"] == [{"type": "function", "function": {"name": "write_file"}}]
        assert kwargs["tool_choice"] == "auto"
        return fake_stream()

    monkeypatch.setattr("agentforge.llm.provider.litellm.acompletion", fake_acompletion)

    provider = LLMProvider(LLMConfig(model="ollama/llama3.1:8b"))
    events = [
        event
        async for event in provider.complete_stream(
            [{"role": "user", "content": "write a.txt"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
        )
    ]

    content_events = [e for e in events if e["type"] == "content"]
    assert content_events == []

    tool_call_events = [e for e in events if e["type"] == "tool_calls"]
    assert len(tool_call_events) == 1
    calls = tool_call_events[0]["calls"]
    assert len(calls) == 1
    assert calls[0]["name"] == "write_file"
    assert calls[0]["arguments"] == '{"path": "a.txt"}'
