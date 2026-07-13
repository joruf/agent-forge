"""Task-based LLM model routing for Ollama."""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from agentforge.config import settings
from agentforge.llm.task_types import TaskType
from agentforge.storage.model_store import model_store


ROLE_TASK_MAP: dict[str, TaskType] = {
    "developer": TaskType.CODING,
    "reviewer": TaskType.CODE_REVIEW,
    "architect": TaskType.ARCHITECTURE,
    "researcher": TaskType.RESEARCH,
    "documentation": TaskType.DOCUMENTATION,
    "project_manager": TaskType.COORDINATION,
    "software_tester": TaskType.CODE_REVIEW,
    "security": TaskType.CODE_REVIEW,
    "devops": TaskType.CODING,
}

SQL_KEYWORDS = re.compile(
    r"\b(sql|select|insert|update|delete|database schema|postgres|mysql|sqlite)\b",
    re.IGNORECASE,
)
VISION_KEYWORDS = re.compile(
    r"\b(bild|image|ocr|scan|foto|photo|screenshot|vision|llava)\b",
    re.IGNORECASE,
)
FINANCE_KEYWORDS = re.compile(
    r"\b(finanz|finance|aktie|stock|trading|portfolio|ohlcv|kurs|forecast)\b",
    re.IGNORECASE,
)
CODE_KEYWORDS = re.compile(
    r"\b(code|coding|programm|function|class|bug|refactor|git|npm|python|php|typescript)\b",
    re.IGNORECASE,
)


class ModelRouter:
    """Resolve the best available Ollama model for a task."""

    def __init__(self) -> None:
        """Initialize router caches."""
        self._cache_models: list[str] = []
        self._cache_at = 0.0
        self._cache_ttl = 60.0

    async def list_installed_models(self, force_refresh: bool = False) -> list[str]:
        """
        Fetch installed models from Ollama.

        :param force_refresh: Skip cache when True
        :return: List of Ollama model names
        """
        now = time.time()
        if (
            not force_refresh
            and self._cache_models
            and now - self._cache_at < self._cache_ttl
        ):
            return self._cache_models

        url = f"{settings.ollama_base_url.rstrip('/')}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return self._cache_models

        models = [item.get("name", "") for item in payload.get("models", [])]
        models = [name for name in models if name]
        self._cache_models = models
        self._cache_at = now
        return models

    def detect_task(
        self,
        user_content: str,
        role_id: str | None = None,
        mode_single: bool = False,
    ) -> TaskType:
        """
        Detect the optimal task type from user input and role.

        :param user_content: User message
        :param role_id: Agent role identifier
        :param mode_single: Whether single-agent coding mode is active
        :return: Resolved task type
        """
        if role_id and role_id in ROLE_TASK_MAP:
            base_task = ROLE_TASK_MAP[role_id]
            if base_task == TaskType.CODING:
                if SQL_KEYWORDS.search(user_content):
                    return TaskType.SQL
                if CODE_KEYWORDS.search(user_content):
                    return TaskType.CODING
                return TaskType.GENERAL
            return base_task

        if VISION_KEYWORDS.search(user_content):
            return TaskType.VISION
        if FINANCE_KEYWORDS.search(user_content):
            return TaskType.FINANCE
        if SQL_KEYWORDS.search(user_content):
            return TaskType.SQL
        if CODE_KEYWORDS.search(user_content):
            return TaskType.CODING

        return TaskType.GENERAL

    async def resolve(
        self,
        task: TaskType,
        fallback_model: str | None = None,
    ) -> dict[str, Any]:
        """
        Resolve model for task using user registry and Ollama inventory.

        :param task: Task category
        :param fallback_model: Optional fallback model
        :return: Routing decision details
        """
        fallback = fallback_model or settings.default_model
        override = settings.override_model.strip()
        if override:
            installed = await self.list_installed_models()
            model = model_store.resolve_ollama_litellm_model(override, installed)
            return {
                "task": task.value,
                "model": model,
                "auto_routing": False,
                "source": "local_override",
            }

        if not settings.llm_auto_routing:
            return {
                "task": task.value,
                "model": fallback,
                "auto_routing": False,
                "source": "disabled",
            }

        model_store.reload()
        installed = await self.list_installed_models()
        litellm_model, info = model_store.resolve_model_for_task(
            task,
            installed,
            fallback,
        )
        return {
            "task": task.value,
            "model": litellm_model,
            "auto_routing": True,
            "installed_count": len(installed),
            **info,
        }


model_router = ModelRouter()
