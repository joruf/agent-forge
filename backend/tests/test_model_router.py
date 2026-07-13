"""Tests for task detection and model routing."""

import pytest

from agentforge.llm.model_router import ModelRouter
from agentforge.llm.task_types import TaskType


@pytest.fixture
def router() -> ModelRouter:
    """Fresh router without cached models."""
    return ModelRouter()


def test_detect_task_from_role(router: ModelRouter) -> None:
    """Role IDs map to expected base tasks."""
    assert router.detect_task("write code", role_id="developer") == TaskType.CODING
    assert router.detect_task("hello", role_id="researcher") == TaskType.RESEARCH
    assert router.detect_task("plan modules", role_id="architect") == TaskType.ARCHITECTURE


def test_detect_sql_from_content(router: ModelRouter) -> None:
    """SQL keywords override generic coding detection."""
    assert router.detect_task("Write a SELECT query for users") == TaskType.SQL


def test_detect_vision_keywords(router: ModelRouter) -> None:
    """Vision-related content maps to vision task."""
    assert router.detect_task("Analyze this screenshot with OCR") == TaskType.VISION


def test_detect_finance_keywords(router: ModelRouter) -> None:
    """Finance keywords map to finance task."""
    assert router.detect_task("Forecast stock portfolio returns") == TaskType.FINANCE


def test_detect_general_for_simple_developer_chat(router: ModelRouter) -> None:
    """Simple developer chat without code keywords uses general task."""
    assert router.detect_task("hello there", role_id="developer") == TaskType.GENERAL
    assert router.detect_task("hello there", mode_single=True) == TaskType.GENERAL


def test_detect_coding_for_developer_with_code_keywords(router: ModelRouter) -> None:
    """Developer role with code keywords still maps to coding."""
    assert router.detect_task("fix this python bug", role_id="developer") == TaskType.CODING


def test_detect_general_fallback(router: ModelRouter) -> None:
    """Generic messages without keywords use general task."""
    assert router.detect_task("hello there") == TaskType.GENERAL


@pytest.mark.asyncio
async def test_list_installed_models_uses_cache(router: ModelRouter, monkeypatch) -> None:
    """Model list is cached until force refresh."""
    calls = {"count": 0}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"models": [{"name": "cached:7b"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str):
            calls["count"] += 1
            return FakeResponse()

    monkeypatch.setattr("agentforge.llm.model_router.httpx.AsyncClient", lambda **kw: FakeClient())

    first = await router.list_installed_models(force_refresh=True)
    second = await router.list_installed_models()
    assert first == ["cached:7b"]
    assert second == ["cached:7b"]
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_resolve_local_override(router: ModelRouter, monkeypatch) -> None:
    """Local override model is used for every task when configured."""
    monkeypatch.setattr(
        "agentforge.llm.model_router.settings.override_model",
        "ollama/llama3.2:1b",
    )

    async def fake_installed(force_refresh: bool = False) -> list[str]:
        return ["llama3.2:1b-instruct-q4_K_M"]

    monkeypatch.setattr(router, "list_installed_models", fake_installed)

    result = await router.resolve(TaskType.CODING)
    assert result["model"] == "ollama/llama3.2:1b-instruct-q4_K_M"
    assert result["source"] == "local_override"
    assert result["auto_routing"] is False


@pytest.mark.asyncio
async def test_resolve_respects_disabled_auto_routing(router: ModelRouter, monkeypatch) -> None:
    """When auto routing is off, fallback model is returned."""
    monkeypatch.setattr("agentforge.llm.model_router.settings.override_model", "")
    monkeypatch.setattr("agentforge.llm.model_router.settings.llm_auto_routing", False)
    monkeypatch.setattr("agentforge.llm.model_router.settings.default_model", "ollama/fallback")
    result = await router.resolve(TaskType.CODING)
    assert result["model"] == "ollama/fallback"
    assert result["auto_routing"] is False
