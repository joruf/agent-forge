"""Unified LLM provider via LiteLLM."""

from collections.abc import AsyncIterator
from typing import Any

import litellm

from agentforge.config import settings
from agentforge.llm.cloud_providers import apply_cloud_credentials
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
        self._apply_env()
        request_timeout = timeout if timeout is not None else settings.llm_request_timeout
        kwargs: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
            "timeout": request_timeout,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

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
    ) -> AsyncIterator[str]:
        """
        Stream chat completion tokens.

        :param messages: OpenAI-style message list
        :param model: Optional model override for this request
        :yield: Incremental text deltas from the model
        """
        self._apply_env()
        kwargs: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "timeout": settings.llm_request_timeout,
            "stream": True,
        }

        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                choice = chunk.choices[0]
                delta = choice.delta.content if choice.delta else None
                if delta:
                    yield delta
        except Exception as exc:
            yield f"LLM error: {exc}"

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
