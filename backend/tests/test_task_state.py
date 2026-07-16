"""Tests for shared task-board orchestration."""

from pathlib import Path

import pytest

from agentforge.agents.task_state import (
    TaskType,
    build_escalation_message,
    build_final_response_from_task_state,
    build_pm_verification_block,
    build_task_board_ui_payload,
    build_task_state,
    check_completion,
    discussion_entry_is_repeat,
    discussion_similarity,
    format_inter_round_memory_block,
    format_role_output_schema,
    format_task_board_block,
    increment_weak_retry,
    record_tool_result_as_fact,
    seed_read_facts,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.config import settings


READ_PROMPT = (
    "lese den dateiinhalt von /home/joruf/Dokumente/GitHub/Test12/test12345.txt "
    "und liste mir den inhalt hier auf"
)


def test_classify_read_task_type() -> None:
    """Read requests map to read_and_display task type."""
    intent = detect_workspace_intent(READ_PROMPT)
    assert intent.wants_file_read is True
    state = build_task_state(READ_PROMPT, intent)
    assert state.task_type == TaskType.READ_AND_DISPLAY


def test_seed_read_facts_and_completion() -> None:
    """Verified read facts satisfy read completion criteria."""
    intent = detect_workspace_intent(READ_PROMPT)
    state = build_task_state(READ_PROMPT, intent)
    seed_read_facts(
        state,
        {"GitHub/Test12/test12345.txt": "Hello World"},
    )

    report = check_completion(state)
    assert report.complete is True
    final = build_final_response_from_task_state(state)
    assert "Hello World" in final


def test_record_tool_result_as_fact_for_read_file() -> None:
    """Successful read_file tool calls become verified file-content facts."""
    intent = detect_workspace_intent(READ_PROMPT)
    state = build_task_state(READ_PROMPT, intent)

    record_tool_result_as_fact(
        state,
        "read_file",
        '{"path": "GitHub/Test12/test12345.txt"}',
        "Hello World",
        True,
        "developer",
        1,
    )

    assert state.fact_content_for_path("GitHub/Test12/test12345.txt") == "Hello World"
    assert check_completion(state).complete is True


def test_build_task_state_loads_prior_targets() -> None:
    """Prior task-board snapshots enrich the next turn."""
    intent = detect_workspace_intent(READ_PROMPT)
    prior = {
        "last_request": "Previous read",
        "last_targets": ["GitHub/Test12"],
        "facts": [],
    }
    state = build_task_state(READ_PROMPT, intent, prior)

    assert state.prior_targets == ["GitHub/Test12"]
    assert state.prior_summary == "Previous read"
    assert len(state.plan_steps) >= 1
    assert state.plan_steps[0].action == "read_file"


@pytest.mark.asyncio
async def test_persist_and_load_task_board_memory(tmp_path: Path, monkeypatch) -> None:
    """Task-board snapshots round-trip through chat memory."""
    import aiosqlite

    from agentforge.agents.task_state import load_task_board_memory, persist_task_board
    from agentforge.memory.store import MemoryStore

    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path=db_path)
    monkeypatch.setattr("agentforge.agents.task_state.memory_store", store)

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
                id TEXT PRIMARY KEY,
                chat_id TEXT,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                tokens_estimate INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()

    intent = detect_workspace_intent(READ_PROMPT)
    state = build_task_state(READ_PROMPT, intent)
    seed_read_facts(state, {"GitHub/Test12/test12345.txt": "Hello World"})

    chat_id = "chat-task-board-test"
    await persist_task_board(chat_id, state)

    loaded = await load_task_board_memory(chat_id)
    assert loaded is not None
    assert loaded["last_task_type"] == TaskType.READ_AND_DISPLAY.value
    assert loaded["facts"][0]["content"] == "Hello World"


def test_format_inter_round_memory_block_shows_prior_facts() -> None:
    """Prior-turn facts are exposed to later orchestration turns."""
    intent = detect_workspace_intent(READ_PROMPT)
    prior = {
        "last_request": "Previous read",
        "last_targets": ["GitHub/Test12"],
        "facts": [
            {
                "id": "fact-old",
                "source": "prefetch_read",
                "kind": "file_content",
                "path": "GitHub/Test12/old.txt",
                "content": "Old content",
                "verified": True,
                "agent_id": "system",
                "round_num": 0,
            }
        ],
    }
    state = build_task_state("Follow-up question", intent, prior)
    block = format_inter_round_memory_block(state)

    assert "Previous request in this chat: Previous read" in block
    assert "GitHub/Test12/old.txt" in block


def test_build_pm_verification_block_pass_and_fail() -> None:
    """PM verification reflects completion status and verified facts."""
    intent = detect_workspace_intent(READ_PROMPT)
    state = build_task_state(READ_PROMPT, intent)
    seed_read_facts(state, {"GitHub/Test12/test12345.txt": "Hello World"})

    completion = check_completion(state)
    verification = build_pm_verification_block(state, completion)

    assert "VERDICT: pass" in verification
    assert "Hello World" in verification

    empty_state = build_task_state(READ_PROMPT, intent)
    fail_report = check_completion(empty_state)
    fail_verification = build_pm_verification_block(empty_state, fail_report)
    assert "VERDICT: fail" in fail_verification


def test_increment_weak_retry_builds_escalation_message() -> None:
    """Repeated weak output eventually produces a user escalation message."""
    intent = detect_workspace_intent(READ_PROMPT)
    state = build_task_state(READ_PROMPT, intent)

    assert increment_weak_retry(state, "developer") == 1
    assert increment_weak_retry(state, "developer") == 2
    message = build_escalation_message(state, "developer", reason="Missing verified file content")

    assert "2 attempts" in message
    assert "Missing verified file content" in message


def test_format_role_output_schema_for_reviewer() -> None:
    """Reviewer responses include a structured verdict schema."""
    schema = format_role_output_schema("reviewer", TaskType.READ_AND_DISPLAY)
    assert "VERDICT: pass|fail" in schema


def test_discussion_entry_is_repeat_detects_similar_messages() -> None:
    """Repeated agent messages are detected from the transcript history."""
    transcript = [
        "Reviewer: VERDICT: fail\nREASON: Missing verified file content for GitHub/Test12/test12345.txt",
    ]
    repeated = (
        "VERDICT: fail\nREASON: Missing verified file content for GitHub/Test12/test12345.txt"
    )
    assert discussion_entry_is_repeat("Reviewer", repeated, transcript) is True


def test_discussion_similarity_ignores_minor_changes() -> None:
    """Near-identical messages exceed the repetition similarity threshold."""
    left = "VERDICT: fail REASON: Missing verified file content for the requested path"
    right = "VERDICT: fail REASON: Missing verified file content for requested path"
    assert discussion_similarity(left, right) >= 0.85


def test_build_task_board_ui_payload_marks_completed_read_step() -> None:
    """UI payload marks read steps done when verified file content exists."""
    intent = detect_workspace_intent(READ_PROMPT)
    state = build_task_state(READ_PROMPT, intent)
    seed_read_facts(state, {"GitHub/Test12/test12345.txt": "Hello World"})

    payload = build_task_board_ui_payload(state)

    assert payload["type"] == "task_board_updated"
    assert payload["task_type"] == TaskType.READ_AND_DISPLAY.value
    assert payload["steps"]
    assert any(step["status"] == "done" for step in payload["steps"])
    assert payload["complete"] is True
