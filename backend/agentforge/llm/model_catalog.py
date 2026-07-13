"""Reference catalog of known LLM models and task recommendations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentforge.i18n import current_locale, t
from agentforge.llm.task_types import TaskType


class ModelCatalog:
    """Load and query the static models knowledge base."""

    def __init__(self, catalog_path: Path | None = None) -> None:
        """Initialize catalog from JSON file."""
        self.catalog_path = catalog_path or self._default_path()
        self._data = self._load()

    def _default_path(self) -> Path:
        """Return default catalog path."""
        return Path(__file__).resolve().parents[3] / "assets" / "models_catalog.json"

    def _load(self) -> dict[str, Any]:
        """Load catalog JSON."""
        if not self.catalog_path.exists():
            return {"tasks": {}, "entries": []}
        return json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def task_definitions(self, locale: str | None = None) -> dict[str, dict[str, str]]:
        """Return task metadata for UI in the requested locale."""
        lang = locale or current_locale()
        result: dict[str, dict[str, str]] = {}
        for task_id in self._data.get("tasks", {}):
            result[task_id] = {
                "label": t(f"tasks.{task_id}.label", locale=lang),
                "description": t(f"tasks.{task_id}.description", locale=lang),
            }
        return result

    def entries(self) -> list[dict[str, Any]]:
        """Return all catalog entries."""
        return self._data.get("entries", [])

    def match_entry(self, ollama_tag: str) -> dict[str, Any] | None:
        """
        Find best matching catalog entry for an Ollama tag.

        :param ollama_tag: Ollama model tag
        :return: Matching catalog entry or None
        """
        tag_lower = ollama_tag.lower()
        best: dict[str, Any] | None = None
        best_score = 0

        for entry in self.entries():
            for pattern in entry.get("patterns", []):
                pattern_lower = pattern.lower()
                score = 0
                if tag_lower == pattern_lower:
                    score = 100
                elif tag_lower.startswith(pattern_lower):
                    score = 80
                elif pattern_lower in tag_lower:
                    score = 60
                elif re.search(re.escape(pattern_lower.split(":")[0]), tag_lower):
                    score = 40

                if score > best_score:
                    best_score = score
                    best = entry

        return best

    def suggest_for_tag(self, ollama_tag: str) -> dict[str, Any]:
        """
        Suggest display name and tasks for a model tag.

        :param ollama_tag: Ollama model tag
        :return: Suggestion payload
        """
        entry = self.match_entry(ollama_tag)
        if entry:
            return {
                "ollama_tag": ollama_tag,
                "display_name": entry.get("display_name", ollama_tag),
                "assigned_tasks": list(entry.get("recommended_tasks", [])),
                "catalog_match": entry.get("id"),
                "description": entry.get("description", ""),
                "ram_gb": entry.get("ram_gb", ""),
                "family": entry.get("family", ""),
            }

        tag_lower = ollama_tag.lower()
        tasks: list[str] = [TaskType.GENERAL.value]
        if any(k in tag_lower for k in ("code", "coder", "codellama", "deepseek")):
            tasks = [TaskType.CODING.value, TaskType.CODE_REVIEW.value]
        elif "sql" in tag_lower:
            tasks = [TaskType.SQL.value]
        elif any(k in tag_lower for k in ("vision", "llava")):
            tasks = [TaskType.VISION.value]
        elif "fin" in tag_lower:
            tasks = [TaskType.FINANCE.value]
        elif "mistral" in tag_lower or "mixtral" in tag_lower:
            tasks = [TaskType.RESEARCH.value, TaskType.COORDINATION.value]
        elif "gemma" in tag_lower:
            tasks = [TaskType.DOCUMENTATION.value, TaskType.RESEARCH.value]
        elif "phi" in tag_lower:
            tasks = [TaskType.GENERAL.value, TaskType.TITLE.value]

        return {
            "ollama_tag": ollama_tag,
            "display_name": ollama_tag,
            "assigned_tasks": tasks,
            "catalog_match": None,
            "description": t("catalog.auto_detected", locale=current_locale()),
            "ram_gb": "",
            "family": "",
        }


model_catalog = ModelCatalog()
