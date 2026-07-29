"""Tests for user model registry and routing."""

import pytest

from agentforge.llm.task_types import TaskType
from agentforge.storage.model_store import ModelStore
from agentforge.storage.performance_store import PerformanceStore


@pytest.fixture
def store(temp_data_dir) -> ModelStore:
    """Isolated model store instance."""
    return ModelStore(temp_data_dir / "model_config.json")


@pytest.fixture
def performance(temp_data_dir, monkeypatch) -> PerformanceStore:
    """Isolated performance store, patched in place of the model_store module's singleton."""
    instance = PerformanceStore(temp_data_dir / "model_performance.json")
    monkeypatch.setattr("agentforge.storage.model_store.performance_store", instance)
    return instance


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
    assert "ranked_by_performance" not in info


def test_resolve_model_for_task_prefers_faster_measured_model(
    store: ModelStore, performance: PerformanceStore,
) -> None:
    """Among several models assigned to a task, the faster measured one wins."""
    slow = store.add_model("slow-coder:7b", assigned_tasks=["coding"], auto_suggest=False)
    fast = store.add_model("fast-coder:7b", assigned_tasks=["coding"], auto_suggest=False)
    performance.record(
        "ollama/slow-coder:7b", accessible=True, tokens_per_second=5.0, source="benchmark",
    )
    performance.record(
        "ollama/fast-coder:7b", accessible=True, tokens_per_second=40.0, source="benchmark",
    )

    litellm, info = store.resolve_model_for_task(
        TaskType.CODING,
        installed_tags=["slow-coder:7b", "fast-coder:7b"],
        fallback_model="ollama/fallback",
    )

    assert litellm == "ollama/fast-coder:7b"
    assert info["model_id"] == fast["id"]
    assert info["ranked_by_performance"] is True
    assert slow["id"] != fast["id"]


def test_resolve_model_for_task_skips_known_inaccessible_model(
    store: ModelStore, performance: PerformanceStore,
) -> None:
    """A model measured as inaccessible loses to an unmeasured one despite list order."""
    broken = store.add_model("broken:7b", assigned_tasks=["coding"], auto_suggest=False)
    store.add_model("untested:7b", assigned_tasks=["coding"], auto_suggest=False)
    performance.record("ollama/broken:7b", accessible=False, source="benchmark", error="timeout")

    litellm, info = store.resolve_model_for_task(
        TaskType.CODING,
        installed_tags=["broken:7b", "untested:7b"],
        fallback_model="ollama/fallback",
    )

    assert litellm == "ollama/untested:7b"
    assert info["model_id"] != broken["id"]


def test_resolve_model_for_task_unranked_without_performance_data(
    store: ModelStore, performance: PerformanceStore,
) -> None:
    """With no performance data at all, behavior matches the pre-ranking order."""
    first = store.add_model("first:7b", assigned_tasks=["coding"], auto_suggest=False)
    store.add_model("second:7b", assigned_tasks=["coding"], auto_suggest=False)

    litellm, info = store.resolve_model_for_task(
        TaskType.CODING,
        installed_tags=["first:7b", "second:7b"],
        fallback_model="ollama/fallback",
    )

    assert litellm == "ollama/first:7b"
    assert info["model_id"] == first["id"]
    assert info["ranked_by_performance"] is True


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
