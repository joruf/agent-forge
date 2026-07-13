"""Tests for user model registry and routing."""

import pytest

from agentforge.llm.task_types import TaskType
from agentforge.storage.model_store import ModelStore


@pytest.fixture
def store(temp_data_dir) -> ModelStore:
    """Isolated model store instance."""
    return ModelStore(temp_data_dir / "model_config.json")


def test_add_and_list_models(store: ModelStore) -> None:
    """Models can be added and retrieved."""
    created = store.add_model("test-model:7b", auto_suggest=False)
    assert created["ollama_tag"] == "test-model:7b"
    assert len(store.list_models()) == 1


def test_duplicate_model_rejected(store: ModelStore) -> None:
    """Adding the same Ollama tag twice raises ValueError."""
    store.add_model("dup:7b", auto_suggest=False)
    with pytest.raises(ValueError, match="already exists"):
        store.add_model("dup:7b", auto_suggest=False)


def test_update_model_fields(store: ModelStore) -> None:
    """Display name and notes can be updated."""
    model = store.add_model("edit:7b", auto_suggest=False)
    updated = store.update_model(model["id"], {"display_name": "Edited", "notes": "note"})
    assert updated["display_name"] == "Edited"
    assert updated["notes"] == "note"


def test_delete_model(store: ModelStore) -> None:
    """Models can be removed from registry."""
    model = store.add_model("del:7b", auto_suggest=False)
    store.delete_model(model["id"])
    assert store.get_model(model["id"]) is None


def test_routing_override(store: ModelStore) -> None:
    """Per-task routing overrides persist."""
    store.set_routing(TaskType.CODING, "auto")
    model = store.add_model("coder:7b", assigned_tasks=["coding"], auto_suggest=False)
    store.set_routing(TaskType.CODING, model["id"])
    assert store.get_routing()[TaskType.CODING.value] == model["id"]


def test_resolve_model_for_task_uses_assignment(store: ModelStore) -> None:
    """Task resolution prefers models assigned to the task."""
    model = store.add_model("coder:7b", assigned_tasks=["coding"], auto_suggest=False)
    litellm, info = store.resolve_model_for_task(
        TaskType.CODING,
        installed_tags=["coder:7b"],
        fallback_model="ollama/fallback",
    )
    assert litellm == "ollama/coder:7b"
    assert info["model_id"] == model["id"]


def test_sync_from_ollama_adds_new_tags(store: ModelStore) -> None:
    """Sync imports only tags not yet in registry."""
    store.add_model("existing:7b", auto_suggest=False)
    added = store.sync_from_ollama(["existing:7b", "new-model:7b"])
    assert len(added) == 1
    assert added[0]["ollama_tag"] == "new-model:7b"
    assert len(store.list_models()) == 2


def test_resolve_installed_tag_matches_variant(store: ModelStore) -> None:
    """Registry tags resolve to the exact Ollama variant name."""
    resolved = store._resolve_installed_tag(
        "mistral:7b-instruct",
        ["mistral:7b-instruct-q4_K_M"],
    )
    assert resolved == "mistral:7b-instruct-q4_K_M"


def test_resolve_model_uses_installed_variant(store: ModelStore) -> None:
    """LiteLLM model string uses the installed Ollama tag variant."""
    model = store.add_model(
        "mistral:7b-instruct",
        assigned_tasks=["coding"],
        auto_suggest=False,
    )
    litellm, info = store.resolve_model_for_task(
        TaskType.CODING,
        installed_tags=["mistral:7b-instruct-q4_K_M"],
        fallback_model="ollama/fallback",
    )
    assert litellm == "ollama/mistral:7b-instruct-q4_K_M"
    assert info["model_id"] == model["id"]


def test_routing_overview_contains_all_tasks(store: ModelStore, english_locale) -> None:
    """Routing overview exposes every task type."""
    overview = store.routing_overview(installed_tags=["coder:7b"])
    for task in TaskType:
        assert task.value in overview
        assert "label" in overview[task.value]
        assert "selected" in overview[task.value]
