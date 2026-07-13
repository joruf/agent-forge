"""Tests for setup service connectivity checks."""

import pytest

from agentforge.config import settings
from agentforge.services import setup_service


@pytest.mark.asyncio
async def test_backend_test_always_ok() -> None:
    """Backend self-test succeeds."""
    result = await setup_service.test_backend()
    assert result["ok"] is True
    assert result["id"] == "backend"


@pytest.mark.asyncio
async def test_ollama_test_success(monkeypatch) -> None:
    """Ollama test passes when tags endpoint returns models."""

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"models": [{"name": "llama3.1:8b"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str):
            return FakeResponse()

    monkeypatch.setattr("agentforge.services.setup_service.httpx.AsyncClient", lambda **kw: FakeClient())
    result = await setup_service.test_ollama("http://fake:11434")
    assert result["ok"] is True
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_ollama_test_failure(monkeypatch, english_locale) -> None:
    """Ollama test fails when server is unreachable."""

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str):
            raise ConnectionError("connection refused")

    monkeypatch.setattr("agentforge.services.setup_service.httpx.AsyncClient", lambda **kw: FailingClient())
    result = await setup_service.test_ollama("http://bad:11434")
    assert result["ok"] is False


def test_workspace_test_ok(temp_workspace, english_locale) -> None:
    """Workspace test passes for writable directory."""
    result = setup_service.test_workspace(str(temp_workspace))
    assert result["ok"] is True


def test_workspace_test_missing(english_locale) -> None:
    """Workspace test fails when path does not exist."""
    result = setup_service.test_workspace("/nonexistent/path/agentforge-test")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_run_all_tests_aggregates(temp_workspace, monkeypatch, english_locale) -> None:
    """Full test suite returns summary and required flag."""

    async def fake_ollama(url=None):
        return {"id": "ollama", "ok": True, "message": "ok", "models": ["m:7b"], "count": 1}

    async def fake_openai():
        return {"id": "openai", "ok": None, "skipped": True, "message": "skip"}

    async def fake_generate(model_tag=None):
        return {"id": "ollama_generate", "ok": True, "message": "ok"}

    async def fake_cloud_providers():
        return [await fake_openai()]

    monkeypatch.setattr(setup_service, "test_ollama", fake_ollama)
    monkeypatch.setattr(setup_service, "test_all_cloud_providers", fake_cloud_providers)
    monkeypatch.setattr(setup_service, "test_ollama_generate", fake_generate)
    monkeypatch.setattr(setup_service, "test_model_registry", lambda: {
        "id": "model_registry", "ok": True, "message": "1 model", "count": 1,
    })

    report = await setup_service.run_all_tests(
        workspace_path=str(temp_workspace),
        test_generate=True,
    )
    assert "results" in report
    assert report["all_required_ok"] is True
    assert report["summary"] == "All required tests passed"


@pytest.mark.asyncio
async def test_run_model_access_tests(monkeypatch, english_locale) -> None:
    """Model access test suite aggregates Ollama and registry checks."""

    async def fake_ollama(url=None):
        return {
            "id": "ollama",
            "ok": True,
            "message": "ok",
            "models": ["llama3.1:8b", "phi3.5:3.8b-mini-instruct-q8_0"],
            "count": 2,
        }

    async def fake_openai():
        return {"id": "openai", "ok": None, "skipped": True, "message": "skip"}

    async def fake_cloud_providers():
        return [await fake_openai()]

    monkeypatch.setattr(setup_service, "test_ollama", fake_ollama)
    monkeypatch.setattr(setup_service, "test_all_cloud_providers", fake_cloud_providers)
    monkeypatch.setattr(setup_service, "test_model_registry", lambda: {
        "id": "model_registry", "ok": True, "message": "1 model", "count": 1,
    })
    monkeypatch.setattr(setup_service.model_store, "list_models", lambda: [
        {"ollama_tag": "llama3.1:8b", "enabled": True},
    ])

    report = await setup_service.run_model_access_tests(
        ollama_url="http://fake:11434",
        default_model="ollama/llama3.1:8b",
        test_inference=False,
    )
    assert report["all_required_ok"] is True
    ids = {item["id"] for item in report["results"]}
    assert "ollama" in ids
    assert "default_model" in ids
    assert "registry_ollama" in ids


def test_default_model_cloud_provider(english_locale, monkeypatch) -> None:
    """Default model check accepts configured cloud models."""
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    result = setup_service.test_default_model_available(
        "anthropic/claude-3-5-haiku-20241022",
        [],
    )
    assert result["ok"] is True


def test_parse_default_model_available(english_locale) -> None:
    """Default model check detects installed Ollama tags."""
    result = setup_service.test_default_model_available(
        "ollama/llama3.1:8b",
        ["llama3.1:8b"],
    )
    assert result["ok"] is True

    missing = setup_service.test_default_model_available(
        "ollama/missing:7b",
        ["llama3.1:8b"],
    )
    assert missing["ok"] is False
