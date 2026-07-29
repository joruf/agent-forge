"""Tests for shared task-board orchestration."""

from pathlib import Path

import pytest

from agentforge.agents.task_state import (
    TaskType,
    analyze_write_path_compliance,
    build_escalation_message,
    build_final_response_from_task_state,
    build_pm_verification_block,
    build_task_board_ui_payload,
    build_task_state,
    check_completion,
    collect_required_write_paths,
    discussion_entry_is_repeat,
    discussion_similarity,
    format_inter_round_memory_block,
    format_role_output_schema,
    format_task_board_block,
    increment_verdict_retry,
    increment_weak_retry,
    MAX_VERDICT_RETRIES,
    parse_reviewer_verdict,
    parse_tester_severity,
    record_tool_result_as_fact,
    reconcile_verified_write_facts,
    seed_edit_facts,
    seed_read_facts,
    seed_step_error_fact,
    seed_write_facts,
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


def test_parse_reviewer_verdict() -> None:
    """Reviewer verdict is extracted from the structured response."""
    assert parse_reviewer_verdict("VERDICT: fail\nREASON: x\nNOTES: y") == "fail"
    assert parse_reviewer_verdict("VERDICT: pass\nREASON: x\nNOTES: y") == "pass"
    assert parse_reviewer_verdict("Looks fine to me.") is None


def test_parse_tester_severity() -> None:
    """Tester/security severity is extracted from the structured response."""
    assert parse_tester_severity("FINDINGS: x\nSEVERITY: high\nRECOMMENDATION: y") == "high"
    assert parse_tester_severity("FINDINGS: x\nSEVERITY: low\nRECOMMENDATION: y") == "low"
    assert parse_tester_severity("No structured findings here.") is None


def test_increment_verdict_retry_bounded() -> None:
    """Verdict-retry counter increments per role and is independent of weak-retry counts."""
    intent = detect_workspace_intent(READ_PROMPT)
    state = build_task_state(READ_PROMPT, intent)

    assert increment_verdict_retry(state, "reviewer") == 1
    assert increment_verdict_retry(state, "reviewer") == 2
    assert increment_verdict_retry(state, "reviewer") > MAX_VERDICT_RETRIES
    assert increment_verdict_retry(state, "software_tester") == 1
    assert state.weak_retry_counts == {}


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


def test_write_file_step_complete_when_file_was_edited() -> None:
    """Write steps count as done when the target file has a verified edit fact."""
    from agentforge.agents.task_state import _step_is_complete

    intent = detect_workspace_intent(EIGHT_STEP_H3_1TXT_FITXT_PROMPT)
    state = build_task_state(EIGHT_STEP_H3_1TXT_FITXT_PROMPT, intent)
    seed_edit_facts(state, "GitHub/Test12/index.html", replace_from="", replace_to="Hello Bot")

    assert _step_is_complete(
        "write_file",
        "GitHub/Test12/index.html",
        state,
    ) is True


SEVEN_STEP_H3_AND_1TXT_PROMPT = (
    "erstelle mir Verzeichnis mit dem Namen Test12\n"
    "im Ordner GitHub\n"
    "dort eine Datei mit dem Namen index.html erstellen\n"
    'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
    "lese danach die Datei GitHub/index.html aus und geb den Inhalt hier im Prompt aus.\n"
    "erstelle danach in der datei GitHub/index.html unter dem H1 Tag einen H3 Tag "
    'mit der Beschriftung "Hello Bot".\n'
    "erstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
    "H3-Tag der erstellten HTML-Datei und hat die Dateiendung .txt\n"
    "erstelle danach die txt datei 1.txt und schreibe den text vom H1 Tag des HTML Datei rein"
)

EIGHT_STEP_H3_1TXT_FITXT_PROMPT = (
    f"{SEVEN_STEP_H3_AND_1TXT_PROMPT}\n"
    "erstelle danach die txt datei fi.txt und schreibe den text vom H2 Tag des HTML Datei rein"
)


def test_write_files_fails_when_written_to_wrong_directory() -> None:
    """Task completion fails when a file exists only under the wrong workspace path."""
    prompt = (
        "erstelle mir ein Programm unter /home/joruf/GitHub/emailsender\n"
        "named SimpleEmailSender.php"
    )
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)

    required = collect_required_write_paths(state)
    assert "GitHub/emailsender/SimpleEmailSender.php" in required

    seed_write_facts(state, ["SimpleEmailSender.php"])

    missing, wrong_location = analyze_write_path_compliance(state)
    assert wrong_location
    assert wrong_location[0][0] == "GitHub/emailsender/SimpleEmailSender.php"
    assert wrong_location[0][1] == "SimpleEmailSender.php"

    report = check_completion(state)
    assert report.complete is False
    assert "wrong location" in report.reason.lower()

    payload = build_task_board_ui_payload(state)
    assert payload["complete"] is False
    assert any(
        step["status"] != "done"
        for step in payload["steps"]
        if step["action"] == "write_file"
    )


def test_write_files_succeeds_at_required_path(temp_workspace: Path) -> None:
    """Task completion passes when files are written to the required workspace path."""
    prompt = (
        "erstelle mir ein Programm unter /home/joruf/GitHub/emailsender\n"
        "named SimpleEmailSender.php"
    )
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)
    relative = "GitHub/emailsender/SimpleEmailSender.php"
    target = temp_workspace / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "<?php\nclass SimpleEmailSender {}\n",
        encoding="utf-8",
    )
    seed_write_facts(state, [relative])

    report = check_completion(state)
    assert report.complete is True

    payload = build_task_board_ui_payload(state)
    assert payload["complete"] is True
    assert any(
        step["status"] == "done"
        for step in payload["steps"]
        if step["action"] == "write_file"
    )


def test_seed_step_error_fact_surfaces_in_workflow_final_response() -> None:
    """Step errors appear in workflow final responses and completion checks."""
    intent = detect_workspace_intent(EIGHT_STEP_H3_1TXT_FITXT_PROMPT)
    state = build_task_state(EIGHT_STEP_H3_1TXT_FITXT_PROMPT, intent)
    seed_write_facts(state, ["GitHub/Test12/index.html"])
    seed_write_facts(state, ["GitHub/Test12/Hello Bot.txt"])
    seed_write_facts(state, ["GitHub/Test12/1.txt"])
    seed_read_facts(
        state,
        {"GitHub/Test12/index.html": "<h1>Hello World</h1><h3>Hello Bot</h3>"},
    )
    seed_edit_facts(
        state,
        "GitHub/Test12/index.html",
        replace_from="",
        replace_to="Hello Bot",
    )
    error_message = (
        "Could not create `GitHub/Test12/fi.txt`: no `<h2>` found in "
        "`GitHub/Test12/index.html`"
    )
    seed_step_error_fact(
        state,
        error_message,
        step_path="GitHub/Test12/fi.txt",
        kind="file_write_error",
    )

    final = build_final_response_from_task_state(state)
    assert "Workflow step errors:" in final
    assert error_message in final

    report = check_completion(state)
    assert report.complete is False
    assert "no `<h2>` found" in report.reason


WRITE_THEN_READ_PROMPT = (
    "erstelle GitHub/Test12/hello.txt mit dem Inhalt Hello\n"
    "lese danach GitHub/Test12/hello.txt aus"
)


def test_record_tool_result_as_fact_uses_written_output_path() -> None:
    """write_file facts use the resolved path from the Written: output line."""
    intent = detect_workspace_intent(WRITE_THEN_READ_PROMPT)
    state = build_task_state(WRITE_THEN_READ_PROMPT, intent)

    record_tool_result_as_fact(
        state,
        "write_file",
        '{"path": "hello.txt", "content": "Hello"}',
        "Written: GitHub/Test12/hello.txt",
        True,
        "developer",
        1,
    )

    written_paths = {
        fact.path for fact in state.verified_facts("file_written") if fact.path
    }
    assert "GitHub/Test12/hello.txt" in written_paths


def test_reconcile_verified_write_facts_seeds_disk_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required paths that exist on disk become verified write facts."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "hello.txt"
    file_path.write_text("Hello", encoding="utf-8")
    monkeypatch.setattr(settings, "workspace_root", workspace)

    intent = detect_workspace_intent(WRITE_THEN_READ_PROMPT)
    state = build_task_state(WRITE_THEN_READ_PROMPT, intent)

    reconcile_verified_write_facts(state)

    written_paths = {
        fact.path for fact in state.verified_facts("file_written") if fact.path
    }
    assert "hello.txt" in written_paths
    missing, _wrong_location = analyze_write_path_compliance(state)
    assert "hello.txt" not in missing


def test_build_task_board_ui_payload_hides_reason_during_active_step() -> None:
    """Blocker reasons stay hidden while a plan step is still in progress."""
    intent = detect_workspace_intent(WRITE_THEN_READ_PROMPT)
    state = build_task_state(WRITE_THEN_READ_PROMPT, intent)

    payload = build_task_board_ui_payload(state)

    assert payload["complete"] is False
    assert payload["reason"] == ""
    assert any(step["status"] == "active" for step in payload["steps"])


def test_build_task_board_ui_payload_includes_missing_when_reason_shown() -> None:
    """Blocker payloads include missing paths when a reason is exposed."""
    prompt = (
        "erstelle mir ein Programm unter /home/joruf/GitHub/emailsender\n"
        "named SimpleEmailSender.php"
    )
    intent = detect_workspace_intent(prompt)
    state = build_task_state(prompt, intent)
    completion = check_completion(state)
    assert completion.missing

    state.plan_steps = []
    payload = build_task_board_ui_payload(state)

    assert payload["complete"] is False
    assert payload["reason"] == "Missing verified writes at required paths"
    assert payload.get("missing") == ["GitHub/emailsender/SimpleEmailSender.php"]


PYTHON_ANIMATION_PROMPT = (
    "erstelle unter GitHub/TEst123456 ein python programm das 30 sekunden lang "
    "eine 3D Animation anzeigt und danach beendet"
)


def test_failed_python_command_blocks_substantive_completion() -> None:
    """Failed pip/python verification commands keep the task board open."""
    intent = detect_workspace_intent(PYTHON_ANIMATION_PROMPT)
    state = build_task_state(PYTHON_ANIMATION_PROMPT, intent)
    target = "GitHub/TEst123456/main.py"
    seed_write_facts(state, [target])
    record_tool_result_as_fact(
        state,
        tool_name="run_command",
        arguments='{"command": "python main.py"}',
        output="[Exit 1]\nModuleNotFoundError: No module named 'matplotlib'",
        success=False,
        agent_id="developer",
        round_num=1,
    )

    report = check_completion(state)
    assert report.complete is False
    assert "verification command failed" in report.reason.lower()
