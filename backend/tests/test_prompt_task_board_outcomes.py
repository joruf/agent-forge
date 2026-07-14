"""Prompt-to-task-board outcome tests without orchestration."""

from __future__ import annotations

import pytest

from agentforge.agents.task_state import (
    TaskFact,
    TaskType,
    build_final_response_from_task_state,
    build_task_state,
    check_completion,
    record_tool_result_as_fact,
    seed_read_facts,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.agents.workspace_scanner import build_workspace_path_context
from agentforge.services.command_audit import command_audit_scope


@pytest.mark.parametrize(
    ("prompt", "task_type"),
    [
        (
            "lese /home/joruf/Dokumente/GitHub/Test12/test12345.txt",
            TaskType.READ_AND_DISPLAY,
        ),
        (
            "Erstelle test.txt unter /home/joruf/Dokumente/GitHub/Neu",
            TaskType.WRITE_FILES,
        ),
        (
            "List directory /home/joruf/Dokumente/GitHub/agent-forge",
            TaskType.LIST_DIRECTORY,
        ),
        (
            "Run command ls -la in workspace",
            TaskType.RUN_COMMAND,
        ),
    ],
)
def test_prompt_builds_expected_task_type(prompt: str, task_type: TaskType) -> None:
    """Representative prompts initialize the expected task-board type."""
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)
    assert state.task_type == task_type


def test_read_prompt_completion_requires_verified_content() -> None:
    """Read tasks stay incomplete until verified file content exists."""
    prompt = "lese /home/joruf/Dokumente/GitHub/Test12/test12345.txt"
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)

    incomplete = check_completion(state)
    assert incomplete.complete is False

    seed_read_facts(state, {"GitHub/Test12/test12345.txt": "Hello World"})
    complete = check_completion(state)
    assert complete.complete is True


def test_read_prompt_final_response_quotes_disk_content() -> None:
    """Read task-board summaries quote verified file content verbatim."""
    prompt = "lese /home/joruf/Dokumente/GitHub/Test12/test12345.txt"
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)
    seed_read_facts(state, {"GitHub/Test12/test12345.txt": "Hello World"})

    final = build_final_response_from_task_state(state)
    assert "GitHub/Test12/test12345.txt" in final
    assert "Hello World" in final


def test_read_prompt_missing_file_surfaces_error() -> None:
    """Missing read targets produce explicit file errors in the final response."""
    prompt = "lese /home/joruf/Dokumente/GitHub/Test12/missing.txt"
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)
    seed_read_facts(
        state,
        {"GitHub/Test12/missing.txt": "[ERROR] File not found: missing.txt"},
    )

    final = build_final_response_from_task_state(state)
    assert "GitHub/Test12/missing.txt" in final
    assert "ERROR" in final


def test_write_prompt_completion_accepts_verified_write_fact() -> None:
    """Write tasks complete once a verified write fact exists."""
    prompt = (
        "Erstelle test.txt unter /home/joruf/Dokumente/GitHub/Test123 "
        'mit dem Text "Hello World"'
    )
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)

    assert check_completion(state).complete is False

    record_tool_result_as_fact(
        state,
        "write_file",
        '{"path": "GitHub/Test123/test.txt", "content": "Hello World"}',
        "Wrote GitHub/Test123/test.txt",
        True,
        "developer",
        1,
    )
    assert check_completion(state).complete is True


def test_list_prompt_completion_requires_directory_listing_fact() -> None:
    """List tasks complete only after a directory listing fact is recorded."""
    prompt = "List directory /home/joruf/Dokumente/GitHub/agent-forge"
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)

    assert check_completion(state).complete is False

    record_tool_result_as_fact(
        state,
        "list_directory",
        '{"path": "GitHub/agent-forge"}',
        "[DIR] backend\n[FILE] README.md",
        True,
        "developer",
        1,
    )
    assert check_completion(state).complete is True
    final = build_final_response_from_task_state(state)
    assert "README.md" in final


def test_command_prompt_completion_requires_command_output_fact() -> None:
    """Command tasks complete only after command output is verified."""
    prompt = "Führe den Befehl ls -la GitHub aus"
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)

    assert check_completion(state).complete is False

    record_tool_result_as_fact(
        state,
        "run_command",
        '{"command": "pwd"}',
        "[OK] /workspace",
        True,
        "developer",
        1,
    )
    assert check_completion(state).complete is True
    assert "/workspace" in build_final_response_from_task_state(state)


@pytest.mark.asyncio
async def test_write_prompt_scanner_context_reports_missing_target(
    monkeypatch,
    tmp_path,
) -> None:
    """Write prompts include scanner context when the target folder is missing."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    (tmp_path / "GitHub").mkdir()

    prompt = (
        f"Erstelle test.txt unter {tmp_path}/GitHub/Test12 "
        'mit dem Text "Hello World"'
    )
    intent = detect_workspace_intent(prompt)
    context = await build_workspace_path_context(intent)

    assert "GitHub/Test12" in context
    assert "does not exist yet" in context


@pytest.mark.asyncio
async def test_write_prompt_scanner_audits_directory_operations(
    monkeypatch,
    tmp_path,
) -> None:
    """Automatic scanner work for write prompts is logged in command history."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    (tmp_path / "GitHub").mkdir()
    (tmp_path / "GitHub" / "README.md").write_text("# Demo", encoding="utf-8")

    prompt = (
        f"Erstelle test.txt unter {tmp_path}/GitHub/Test12 "
        'mit dem Text "Hello World"'
    )
    intent = detect_workspace_intent(prompt)
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async with command_audit_scope("chat-scan", "system", "System", on_event):
        await build_workspace_path_context(intent)

    commands = [
        event["entry"]["command"]
        for event in events
        if event.get("type") == "shell_command_recorded"
    ]
    assert any(command.startswith("list_directory") for command in commands)


def test_mixed_read_facts_keep_latest_per_path() -> None:
    """Duplicate read facts for the same path keep the latest verified content."""
    prompt = "lese /home/joruf/Dokumente/GitHub/Test12/test12345.txt"
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)

    state.add_fact(
        TaskFact(
            id="old",
            source="prefetch_read",
            kind="file_content",
            path="GitHub/Test12/test12345.txt",
            content="alt",
            verified=True,
            agent_id="developer",
            round_num=1,
        )
    )
    seed_read_facts(state, {"GitHub/Test12/test12345.txt": "Hello World"})

    final = build_final_response_from_task_state(state)
    assert "Hello World" in final
    assert "alt" not in final
