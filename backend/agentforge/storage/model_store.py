"""Persistent user model registry and task routing."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentforge.config import settings
from agentforge.i18n import current_locale
from agentforge.llm.model_catalog import model_catalog
from agentforge.llm.task_types import TaskType


def _utcnow() -> str:
    """Return ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


class ModelStore:
    """Store user-defined models and per-task routing overrides."""

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize model store."""
        self.config_path = config_path or (settings.data_dir / "model_config.json")
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        """Load config from disk."""
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            return {"models": [], "routing": {}}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        """Persist config to disk."""
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def reload(self) -> None:
        """Reload config from disk."""
        self._data = self._load()

    def list_models(self) -> list[dict[str, Any]]:
        """Return all user models."""
        return list(self._data.get("models", []))

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Get model by ID."""
        for model in self.list_models():
            if model["id"] == model_id:
                return model
        return None

    def get_model_by_tag(self, ollama_tag: str) -> dict[str, Any] | None:
        """Get model by Ollama tag."""
        tag_lower = ollama_tag.lower()
        for model in self.list_models():
            if model.get("ollama_tag", "").lower() == tag_lower:
                return model
        return None

    def add_model(
        self,
        ollama_tag: str,
        display_name: str | None = None,
        assigned_tasks: list[str] | None = None,
        enabled: bool = True,
        notes: str = "",
        auto_suggest: bool = True,
    ) -> dict[str, Any]:
        """
        Add a user model entry.

        :param ollama_tag: Ollama model tag
        :param display_name: Optional display label
        :param assigned_tasks: Task assignments
        :param enabled: Whether model is active
        :param notes: User notes
        :param auto_suggest: Use catalog to suggest metadata
        :return: Created model entry
        """
        existing = self.get_model_by_tag(ollama_tag)
        if existing:
            raise ValueError(f"Model already exists: {ollama_tag}")

        suggestion = model_catalog.suggest_for_tag(ollama_tag) if auto_suggest else {}
        now = _utcnow()
        model = {
            "id": str(uuid.uuid4()),
            "ollama_tag": ollama_tag.strip(),
            "display_name": display_name or suggestion.get("display_name", ollama_tag),
            "assigned_tasks": assigned_tasks or suggestion.get("assigned_tasks", ["general"]),
            "enabled": enabled,
            "notes": notes or suggestion.get("description", ""),
            "catalog_match": suggestion.get("catalog_match"),
            "family": suggestion.get("family", ""),
            "ram_gb": suggestion.get("ram_gb", ""),
            "created_at": now,
            "updated_at": now,
        }
        self._data.setdefault("models", []).append(model)
        self._save()
        return model

    def update_model(self, model_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update an existing model."""
        allowed = {"ollama_tag", "display_name", "assigned_tasks", "enabled", "notes"}
        for model in self._data.get("models", []):
            if model["id"] != model_id:
                continue
            for key, value in updates.items():
                if key in allowed and value is not None:
                    model[key] = value
            model["updated_at"] = _utcnow()
            self._save()
            return model
        raise KeyError(f"Model {model_id} not found")

    def delete_model(self, model_id: str) -> None:
        """Delete a model and clear routing references."""
        models = self._data.get("models", [])
        self._data["models"] = [m for m in models if m["id"] != model_id]
        routing = self._data.get("routing", {})
        for task, value in list(routing.items()):
            if value == model_id:
                routing[task] = "auto"
        self._data["routing"] = routing
        self._save()

    def get_routing(self) -> dict[str, str]:
        """Return task routing map (task -> model_id or 'auto')."""
        return dict(self._data.get("routing", {}))

    def set_routing(self, task: str, model_id: str) -> dict[str, str]:
        """
        Set routing for a task.

        :param task: Task type key
        :param model_id: User model ID or 'auto'
        :return: Updated routing map
        """
        if task not in {t.value for t in TaskType}:
            raise ValueError(f"Unknown task: {task}")
        if model_id != "auto" and self.get_model(model_id) is None:
            raise KeyError(f"Model {model_id} not found")

        routing = self._data.setdefault("routing", {})
        routing[task] = model_id
        self._save()
        return dict(routing)

    def set_routing_bulk(self, routing: dict[str, str]) -> dict[str, str]:
        """Update multiple routing entries."""
        for task, model_id in routing.items():
            self.set_routing(task, model_id)
        return self.get_routing()

    def resolve_model_for_task(
        self,
        task: TaskType,
        installed_tags: list[str],
        fallback_model: str,
    ) -> tuple[str, dict[str, Any]]:
        """
        Resolve LiteLLM model string for a task.

        :param task: Task category
        :param installed_tags: Tags available on Ollama
        :param fallback_model: Default fallback
        :return: Tuple of (litellm_model, routing_info)
        """
        routing = self.get_routing()
        override = routing.get(task.value, "auto")
        enabled_models = [m for m in self.list_models() if m.get("enabled", True)]

        if override and override != "auto":
            model = self.get_model(override)
            if model:
                tag = model["ollama_tag"]
                resolved_tag = self._resolve_installed_tag(tag, installed_tags)
                litellm = f"ollama/{resolved_tag or tag}"
                return litellm, {
                    "task": task.value,
                    "model": litellm,
                    "source": "user_routing",
                    "model_id": model["id"],
                    "display_name": model.get("display_name"),
                }

        for user_model in enabled_models:
            if task.value in user_model.get("assigned_tasks", []):
                tag = user_model["ollama_tag"]
                resolved_tag = self._resolve_installed_tag(tag, installed_tags)
                if not installed_tags or resolved_tag:
                    litellm = f"ollama/{resolved_tag or tag}"
                    return litellm, {
                        "task": task.value,
                        "model": litellm,
                        "source": "assigned_tasks",
                        "model_id": user_model["id"],
                        "display_name": user_model.get("display_name"),
                    }

        for user_model in enabled_models:
            if TaskType.GENERAL.value in user_model.get("assigned_tasks", []):
                tag = user_model["ollama_tag"]
                litellm = f"ollama/{tag}"
                return litellm, {
                    "task": task.value,
                    "model": litellm,
                    "source": "general_fallback",
                    "model_id": user_model["id"],
                }

        if fallback_model:
            return fallback_model, {
                "task": task.value,
                "model": fallback_model,
                "source": "settings_default",
            }

        if installed_tags:
            litellm = f"ollama/{installed_tags[0]}"
            return litellm, {"task": task.value, "model": litellm, "source": "ollama_first"}

        return settings.default_model, {
            "task": task.value,
            "model": settings.default_model,
            "source": "hard_default",
        }

    def resolve_ollama_litellm_model(
        self,
        litellm_model: str,
        installed_tags: list[str],
    ) -> str:
        """
        Map an ollama/ model reference to the exact tag installed on Ollama.

        :param litellm_model: LiteLLM model string such as ollama/llama3.2:1b
        :param installed_tags: Tags reported by the Ollama API
        :return: Resolved LiteLLM model string
        """
        if not litellm_model.startswith("ollama/"):
            return litellm_model
        tag = litellm_model[len("ollama/") :]
        resolved_tag = self._resolve_installed_tag(tag, installed_tags)
        return f"ollama/{resolved_tag or tag}"

    def _tag_available(self, tag: str, installed: list[str]) -> bool:
        """Check if tag matches an installed Ollama model."""
        return self._resolve_installed_tag(tag, installed) is not None

    def _resolve_installed_tag(self, tag: str, installed: list[str]) -> str | None:
        """
        Map a registry tag to the exact tag installed on Ollama.

        :param tag: Registry model tag
        :param installed: Tags reported by Ollama
        :return: Matching installed tag or None
        """
        if not installed:
            return tag
        tag_lower = tag.lower()
        for name in installed:
            if name.lower() == tag_lower:
                return name
        for name in installed:
            name_lower = name.lower()
            if name_lower.startswith(f"{tag_lower}-") or name_lower.startswith(f"{tag_lower}:"):
                return name
            if name_lower.startswith(tag_lower):
                return name
        return None

    def routing_overview(self, installed_tags: list[str]) -> dict[str, Any]:
        """Build full routing overview for API/UI."""
        tasks_meta = model_catalog.task_definitions(current_locale())
        routing = self.get_routing()
        result: dict[str, Any] = {}

        for task in TaskType:
            selected, info = self.resolve_model_for_task(
                task,
                installed_tags,
                settings.default_model,
            )
            override = routing.get(task.value, "auto")
            result[task.value] = {
                "label": tasks_meta.get(task.value, {}).get("label", task.value),
                "description": tasks_meta.get(task.value, {}).get("description", ""),
                "selected": selected,
                "routing_override": override,
                "source": info.get("source"),
                "model_id": info.get("model_id"),
                "display_name": info.get("display_name"),
            }
        return result

    def sync_from_ollama(self, installed_tags: list[str]) -> list[dict[str, Any]]:
        """
        Import newly discovered Ollama models with catalog suggestions.

        :param installed_tags: Models from Ollama API
        :return: Newly added models
        """
        added = []
        existing_tags = {m["ollama_tag"].lower() for m in self.list_models()}
        for tag in installed_tags:
            if tag.lower() in existing_tags:
                continue
            added.append(self.add_model(tag, auto_suggest=True))
            existing_tags.add(tag.lower())
        return added


model_store = ModelStore()
