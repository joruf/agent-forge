"""Tests for setup wizard state store."""

import pytest

from agentforge.storage.setup_store import SETUP_STEPS, SetupStore


def test_default_state(temp_data_dir) -> None:
    """New setup store starts at welcome step."""
    store = SetupStore(temp_data_dir / "setup_state.json")
    status = store.get_status()
    assert status["current_step"] == "welcome"
    assert status["should_show_wizard"] is True
    assert status["can_resume"] is True
    assert status["steps"] == SETUP_STEPS


def test_skip_and_resume(temp_data_dir) -> None:
    """Skipping hides wizard; resume re-enables it."""
    store = SetupStore(temp_data_dir / "setup_state.json")
    skipped = store.mark_skipped()
    assert skipped["skipped"] is True
    assert store.get_status()["should_show_wizard"] is False

    resumed = store.resume()
    assert resumed["skipped"] is False
    assert store.get_status()["should_show_wizard"] is True


def test_update_step_tracks_progress(temp_data_dir) -> None:
    """Valid steps are persisted and marked done."""
    store = SetupStore(temp_data_dir / "setup_state.json")
    store.update_step("ollama")
    state = store.load()
    assert state["current_step"] == "ollama"
    assert "ollama" in state["steps_done"]


def test_unknown_step_raises(temp_data_dir) -> None:
    """Invalid step IDs are rejected."""
    store = SetupStore(temp_data_dir / "setup_state.json")
    with pytest.raises(ValueError, match="Unknown step"):
        store.update_step("invalid")


def test_complete_marks_finished(temp_data_dir) -> None:
    """Completing setup disables wizard display."""
    store = SetupStore(temp_data_dir / "setup_state.json")
    store.mark_completed()
    status = store.get_status()
    assert status["completed"] is True
    assert status["should_show_wizard"] is False
    assert status["current_step"] == "complete"


def test_save_test_results(temp_data_dir) -> None:
    """Latest setup test report is stored on disk."""
    store = SetupStore(temp_data_dir / "setup_state.json")
    report = {"all_required_ok": True, "results": [], "summary": "ok", "optional_issues": 0}
    store.save_test_results(report)
    assert store.load()["last_test_results"] == report
