"""Unified LLM provider via LiteLLM."""

from collections.abc import AsyncIterator
from typing import Any

import litellm

from agentforge.config import settings
from agentforge.llm.cloud_providers import apply_cloud_credentials
from agentforge.llm.litellm_compat import ensure_litellm_proxy_package
from agentforge.llm.mock_provider import mock_complete

ensure_litellm_proxy_package()
from agentforge.llm.model_router import TaskType, model_router
from agentforge.models.schemas import LLMConfig


class LLMProvider:
    """Adapter for Ollama, OpenAI, Anthropic, Gemini, and other LiteLLM providers."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        """Initialize provider with optional runtime config."""
        self.config = config or LLMConfig(
            model=settings.default_model,
            ollama_base_url=settings.ollama_base_url,
            openai_api_key=settings.openai_api_key,
            openai_api_base=settings.openai_api_base,
            anthropic_api_key=settings.anthropic_api_key,
            gemini_api_key=settings.gemini_api_key,
            groq_api_key=settings.groq_api_key,
            mistral_api_key=settings.mistral_api_key,
        )

    def _apply_env(self) -> None:
        """Apply API credentials to LiteLLM environment."""
        apply_cloud_credentials(self.config)

    def with_model(self, model: str) -> "LLMProvider":
        """Return a provider instance using a different model."""
        updated = self.config.model_copy(update={"model": model})
        return LLMProvider(updated)

    @staticmethod
    def _is_ollama_model(model: str) -> bool:
        """
        Return True when a model string targets Ollama.

        :param model: LiteLLM-style model string
        :return: Whether the model is served by Ollama
        """
        return model.startswith("ollama/")

    @staticmethod
    def _is_mock_model(model: str) -> bool:
        """
        Return True when a model string targets the built-in mock provider.

        :param model: LiteLLM-style model string
        :return: Whether the model is the local test mock
        """
        return model.startswith("mock/")

    async def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """
        Send a chat completion request.

        :param messages: OpenAI-style message list
        :param tools: Optional tool definitions
        :param model: Optional model override for this request
        :param timeout: Optional request timeout override in seconds
        :param max_tokens: Optional max output tokens override
        :return: Response dict with content and optional tool_calls
        """
        resolved_model = model or self.config.model
        if self._is_mock_model(resolved_model):
            return mock_complete(messages, resolved_model)

        self._apply_env()
        request_timeout = timeout if timeout is not None else settings.llm_request_timeout
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
            "timeout": request_timeout,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self._is_ollama_model(resolved_model):
            kwargs["num_ctx"] = settings.ollama_num_ctx

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                response = await litellm.acompletion(**kwargs)
                choice = response.choices[0]
                message = choice.message

                result: dict[str, Any] = {
                    "content": message.content or "",
                    "tool_calls": [],
                    "model": kwargs["model"],
                }

                if message.tool_calls:
                    for call in message.tool_calls:
                        result["tool_calls"].append(
                            {
                                "id": call.id,
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            }
                        )
                return result
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and self._is_timeout_error(exc):
                    continue
                break

        return {
            "content": self._format_llm_error(last_exc),
            "tool_calls": [],
            "error": True,
            "model": kwargs["model"],
        }

    @staticmethod
    def _is_timeout_error(exc: Exception | None) -> bool:
        """
        Detect timeout-related LLM failures.

        :param exc: Raised exception
        :return: True when the error looks like a timeout
        """
        if exc is None:
            return False
        message = str(exc).lower()
        return "timeout" in message or "timed out" in message

    @staticmethod
    def _format_llm_error(exc: Exception | None) -> str:
        """
        Build a user-facing LLM error message.

        :param exc: Raised exception
        :return: Error text for chat output
        """
        if exc is None:
            return "LLM request failed."
        message = str(exc)
        if LLMProvider._is_timeout_error(exc):
            return (
                f"LLM error: {message}\n\n"
                "The model did not respond in time. For remote or CPU-only Ollama, "
                "increase AGENTFORGE_LLM_REQUEST_TIMEOUT in backend/.env, use fewer "
                "agents, or switch to Quick Chat / Single Agent."
            )
        return f"LLM error: {message}"

    async def complete_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Stream chat completion tokens, optionally with tool-calling.

        :param messages: OpenAI-style message list
        :param model: Optional model override for this request
        :param tools: Optional tool definitions
        :param max_tokens: Optional max output tokens override
        :yield: ``{"type": "content", "text": str}`` per text delta, then a
            final ``{"type": "tool_calls", "calls": [...]}``, or
            ``{"type": "error", "message": str}`` on failure
        """
        resolved_model = model or self.config.model
        if self._is_mock_model(resolved_model):
            result = mock_complete(messages, resolved_model)
            yield {"type": "content", "text": result["content"]}
            yield {"type": "tool_calls", "calls": result.get("tool_calls") or []}
            return

        self._apply_env()
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
            "timeout": settings.llm_request_timeout,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self._is_ollama_model(resolved_model):
            kwargs["num_ctx"] = settings.ollama_num_ctx

        chunks: list[Any] = []
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                chunks.append(chunk)
                choice = chunk.choices[0]
                delta = choice.delta.content if choice.delta else None
                if delta:
                    yield {"type": "content", "text": delta}
        except Exception as exc:
            yield {"type": "error", "message": self._format_llm_error(exc)}
            return

        tool_calls: list[dict[str, Any]] = []
        if tools and chunks:
            try:
                built = litellm.stream_chunk_builder(chunks, messages=messages)
                message = built.choices[0].message if built and built.choices else None
                if message and getattr(message, "tool_calls", None):
                    for call in message.tool_calls:
                        tool_calls.append(
                            {
                                "id": call.id,
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            }
                        )
            except Exception:
                pass
        yield {"type": "tool_calls", "calls": tool_calls}

    async def generate_title(self, user_message: str) -> str:
        """
        Generate a short chat title from the first user message.

        :param user_message: Initial user input
        :return: Generated title (max ~60 chars)
        """
        fallback = " ".join((user_message or "").split())[:60] or "New Chat"
        messages = [
            {
                "role": "system",
                "content": (
                    "Generate a short, descriptive chat title (max 6 words) "
                    "based on the user message. Reply with title only, no quotes."
                ),
            },
            {"role": "user", "content": user_message[:500]},
        ]
        try:
            routing = await model_router.resolve(
                TaskType.TITLE,
                fallback_model=self.config.model,
            )
            title_llm = self.with_model(routing["model"])
            result = await title_llm.complete(
                messages,
                timeout=settings.llm_title_timeout,
                max_tokens=32,
            )
            if result.get("error"):
                return fallback
            title = (result.get("content") or "").strip().strip('"').strip("'")
            title = title.splitlines()[0].strip() if title else ""
            if title.startswith("LLM error") or not title:
                return fallback
            return title[:60]
        except Exception:
            return fallback
