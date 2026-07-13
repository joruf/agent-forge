"""Tests for model knowledge catalog."""

from pathlib import Path

from agentforge.llm.model_catalog import ModelCatalog


def test_catalog_loads_entries() -> None:
    """Default catalog contains model entries and tasks."""
    catalog = ModelCatalog()
    entries = catalog.entries()
    tasks = catalog.task_definitions(locale="en")
    assert len(entries) > 5
    assert "coding" in tasks
    assert tasks["coding"]["label"] == "Coding / Development"


def test_match_entry_finds_deepseek() -> None:
    """Catalog matches Ollama tags by pattern."""
    catalog = ModelCatalog()
    entry = catalog.match_entry("deepseek-coder:6.7b-instruct-q4_K_M")
    assert entry is not None
    assert entry.get("family") == "deepseek"


def test_suggest_for_known_tag() -> None:
    """Known tags receive display name and recommended tasks."""
    catalog = ModelCatalog()
    suggestion = catalog.suggest_for_tag("codellama:13b-instruct")
    assert suggestion["display_name"]
    assert "coding" in suggestion["assigned_tasks"]
    assert suggestion["catalog_match"]


def test_suggest_for_unknown_tag(english_locale) -> None:
    """Unknown tags get heuristic task assignment."""
    catalog = ModelCatalog()
    suggestion = catalog.suggest_for_tag("custom-unknown-model:7b")
    assert suggestion["assigned_tasks"]
    assert suggestion["description"] == "Auto-detected — please verify"


def test_custom_catalog_path(tmp_path: Path) -> None:
    """Catalog can load entries from a custom JSON file."""
    path = tmp_path / "catalog.json"
    path.write_text(
        '{"tasks": {"general": {"label": "X", "description": "Y"}}, "entries": []}',
        encoding="utf-8",
    )
    catalog = ModelCatalog(path)
    assert catalog.entries() == []
    assert "general" in catalog._data.get("tasks", {})
