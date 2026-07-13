"""Live integration tests against real Ollama server."""

from __future__ import annotations

import os

import httpx
import pytest

from agentforge.config import settings
from agentforge.llm.model_router import ModelRouter
from agentforge.storage.model_store import ModelStore


pytestmark = pytest.mark.live


@pytest.fixture
def live_ollama_url(ollama_base_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure settings to use the live Ollama server."""
    monkeypatch.setattr(settings, "ollama_base_url", ollama_base_url)
    return ollama_base_url


@pytest.mark.asyncio
async def test_ollama_tags_endpoint_reachable(live_ollama_url: str) -> None:
    """Ollama /api/tags responds with installed models."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{live_ollama_url}/api/tags")
        response.raise_for_status()
        payload = response.json()

    models = [item["name"] for item in payload.get("models", []) if item.get("name")]
    assert len(models) > 0, "Expected at least one Ollama model on the server"
    print(f"Ollama models found: {len(models)}")


@pytest.mark.asyncio
async def test_model_router_lists_live_models(live_ollama_url: str) -> None:
    """ModelRouter fetches models from live Ollama."""
    router = ModelRouter()
    models = await router.list_installed_models(force_refresh=True)
    assert len(models) > 0


@pytest.mark.asyncio
async def test_setup_ollama_test_passes(live_ollama_url: str) -> None:
    """Setup service Ollama test succeeds against live server."""
    from agentforge.services.setup_service import test_ollama

    result = await test_ollama(live_ollama_url)
    assert result["ok"] is True
    assert result.get("count", 0) > 0


@pytest.mark.asyncio
async def test_sync_all_ollama_models(temp_data_dir, live_ollama_url: str) -> None:
    """Import all live Ollama models into an isolated registry."""
    store = ModelStore(temp_data_dir / "live_model_config.json")
    router = ModelRouter()
    installed = await router.list_installed_models(force_refresh=True)
    added = store.sync_from_ollama(installed)

    assert len(store.list_models()) == len(installed)
    print(f"Synced {len(added)} new models ({len(installed)} total on Ollama)")


@pytest.mark.asyncio
async def test_live_generate_smoke(live_ollama_url: str) -> None:
    """Run a minimal generate request against a small available model."""
    from agentforge.services.setup_service import test_ollama, test_ollama_generate

    ollama = await test_ollama(live_ollama_url)
    models = ollama.get("models") or []
    preferred = [
        "phi3.5:3.8b-mini-instruct-q8_0",
        "llama3.1:8b",
        "mistral:7b-instruct-q4_K_M",
        "qwen2.5:7b-instruct-q8_0",
    ]
    candidates = [m for m in preferred if m in models] or models[:3]

    last_message = ""
    for tag in candidates:
        result = await test_ollama_generate(tag)
        if result.get("ok") is True:
            return
        last_message = result.get("message", "")

    pytest.skip(f"No model responded in time (last: {last_message})")


def test_openwebui_reachable() -> None:
    """OpenWebUI front-end is reachable (uses same Ollama backend)."""
    if os.environ.get("AGENTFORGE_LIVE_TESTS", "").strip() not in ("1", "true", "yes"):
        pytest.skip("Set AGENTFORGE_LIVE_TESTS=1 to run live tests")

    url = os.environ.get("AGENTFORGE_OPENWEBUI_URL", "").strip()
    if not url:
        pytest.skip("Set AGENTFORGE_OPENWEBUI_URL to run OpenWebUI reachability test")

    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        response = client.get(url)
    assert response.status_code == 200
    assert "html" in response.headers.get("content-type", "").lower()
