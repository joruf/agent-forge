"""Persistent first-run setup wizard state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentforge.config import settings

SETUP_STEPS = [
    "welcome",
    "ollama",
    "models",
    "openai",
    "workspace",
    "verify",
    "complete",
]


def _utcnow() -> str:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class SetupStore:
    """Store setup wizard progress on disk."""

    def __init__(self, path: Path | None = None) -> None:
        """Initialize setup store."""
        self.path = path or (settings.data_dir / "setup_state.json")

    def _default(self) -> dict[str, Any]:
        """Return default setup state."""
        return {
            "completed": False,
            "skipped": False,
            "current_step": "welcome",
            "steps_done": [],
            "last_test_results": {},
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "completed_at": None,
        }

    def load(self) -> dict[str, Any]:
        """Load setup state from disk."""
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            state = self._default()
            self.save(state)
            return state
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        """Persist setup state."""
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = _utcnow()
        self.path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        return state

    def get_status(self) -> dict[str, Any]:
        """Return setup status for API."""
        state = self.load()
        return {
            **state,
            "steps": SETUP_STEPS,
            "should_show_wizard": not state.get("completed") and not state.get("skipped"),
            "can_resume": not state.get("completed"),
        }

    def update_step(self, step: str) -> dict[str, Any]:
        """Set current wizard step."""
        if step not in SETUP_STEPS:
            raise ValueError(f"Unknown step: {step}")
        state = self.load()
        state["current_step"] = step
        if step not in state.setdefault("steps_done", []):
            state["steps_done"].append(step)
        return self.save(state)

    def mark_skipped(self) -> dict[str, Any]:
        """Skip wizard and allow app usage."""
        state = self.load()
        state["skipped"] = True
        state["current_step"] = state.get("current_step", "welcome")
        return self.save(state)

    def mark_completed(self) -> dict[str, Any]:
        """Mark setup as fully completed."""
        state = self.load()
        state["completed"] = True
        state["skipped"] = False
        state["current_step"] = "complete"
        if "complete" not in state.setdefault("steps_done", []):
            state["steps_done"].append("complete")
        state["completed_at"] = _utcnow()
        return self.save(state)

    def resume(self) -> dict[str, Any]:
        """Resume wizard after skip."""
        state = self.load()
        state["skipped"] = False
        return self.save(state)

    def save_test_results(self, results: dict[str, Any]) -> dict[str, Any]:
        """Store latest test run results."""
        state = self.load()
        state["last_test_results"] = results
        return self.save(state)


setup_store = SetupStore()
