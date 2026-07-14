"""Shared pytest fixtures for AgentForge backend tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentforge.config import settings


@pytest.fixture
def temp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated writable workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(settings, "workspace_root", workspace)
    return workspace


@pytest.fixture
def temp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect AgentForge data directory to a temp folder."""
    data_dir = tmp_path / "agentforge_data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    return data_dir


@pytest.fixture
def reset_i18n_cache() -> None:
    """Clear cached locale catalogs between tests."""
    from agentforge.i18n import _catalogs

    _catalogs.clear()
    yield
    _catalogs.clear()


@pytest.fixture
def english_locale(monkeypatch: pytest.MonkeyPatch, reset_i18n_cache) -> None:
    """Force English UI locale."""
    monkeypatch.setattr(settings, "ui_language", "en")


@pytest.fixture
def german_locale(monkeypatch: pytest.MonkeyPatch, reset_i18n_cache) -> None:
    """Force German UI locale."""
    monkeypatch.setattr(settings, "ui_language", "de")


@pytest.fixture
def live_tests_enabled() -> bool:
    """Return whether live Ollama integration tests should run."""
    return os.environ.get("AGENTFORGE_LIVE_TESTS", "").strip() in ("1", "true", "yes")


@pytest.fixture
def chat_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mark orchestration as ready without contacting external model providers."""
    from tests.helpers.orchestration_test_helpers import patch_chat_ready

    patch_chat_ready(monkeypatch)


@pytest.fixture
def ollama_base_url(live_tests_enabled: bool) -> str:
    """Return configured Ollama URL for live tests."""
    if not live_tests_enabled:
        pytest.skip("Set AGENTFORGE_LIVE_TESTS=1 to run live Ollama tests")
    url = os.environ.get(
        "AGENTFORGE_OLLAMA_BASE_URL",
        getattr(settings, "ollama_base_url", "http://192.168.178.12:11434"),
    )
    return url.rstrip("/")
