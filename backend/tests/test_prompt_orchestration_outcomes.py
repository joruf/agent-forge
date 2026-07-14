"""End-to-end prompt-to-outcome orchestration quality tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.quality

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.task_state import load_task_board_memory
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.models.schemas import OrchestrationMode
from agentforge.services.command_audit import command_audit_scope
from agentforge.agents.workspace_scanner import build_workspace_path_context
from tests.helpers.orchestration_test_helpers import (
    assert_file_content,
    assert_final_message_contains,
    audit_commands,
    make_team_loop,
    patch_agent_loop,
    patch_chat_ready,
    patch_materialize_with_fallback,
    run_orchestration,
    write_workspace_file,
)


WORKSPACE = "/home/joruf/Dokumente/GitHub"


@pytest.mark.asyncio
async def test_multi_read_prompt_returns_verified_content_despite_weak_agents(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Read prompts must quote disk content even when agents emit status JSON."""
    write_workspace_file(
        temp_workspace,
        "GitHub/Test12/test12345.txt",
        "Hello World",
    )
    prompt = (
        f"lese den dateiinhalt von {temp_workspace}/GitHub/Test12/test12345.txt "
        "und liste mir den inhalt hier auf"
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    assert_final_message_contains(result, "Hello World", "GitHub/Test12/test12345.txt")


@pytest.mark.asyncio
async def test_single_read_prompt_returns_verified_content_despite_weak_agent(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Single-agent read prompts also fall back to verified disk content."""
    write_workspace_file(temp_workspace, "GitHub/Demo/readme.txt", "Alpha Beta Gamma")
    prompt = f"Lese {temp_workspace}/GitHub/Demo/readme.txt und zeige den Inhalt"

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        mode=OrchestrationMode.SINGLE,
        role_ids=["developer"],
        agent_loop=make_team_loop(role_responses={"developer": '{"status": "success"}'}),
    )

    assert_final_message_contains(result, "Alpha Beta Gamma", "GitHub/Demo/readme.txt")


@pytest.mark.asyncio
async def test_read_prompt_reports_missing_file_in_final_answer(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Missing read targets must not be silently replaced by weak agent output."""
    prompt = (
        f"lese den dateiinhalt von {temp_workspace}/GitHub/Test12/fehlt.txt "
        "und liste mir den inhalt hier auf"
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    final = result.messages[-1].content
    assert "GitHub/Test12/fehlt.txt" in final
    assert (
        "ERROR" in final
        or "not found" in final.lower()
        or "File not found" in final
        or "Missing verified file content" in final
    )


@pytest.mark.asyncio
async def test_write_named_folder_prompt_creates_literal_txt_file(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Named-folder write prompts create the requested TXT file with literal content."""
    (temp_workspace / "GitHub").mkdir()
    prompt = (
        f"erstelle mir im verzeichnis\n{temp_workspace}/GitHub\n"
        "einen Ordner mit dem Namen. Test123\n"
        "darin eine Datei mit dem Namen test.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    assert_file_content(temp_workspace, "GitHub/Test123/test.txt", "Hello World")
    assert_final_message_contains(result, "GitHub/Test123/test.txt")


@pytest.mark.asyncio
async def test_write_test12_prompt_creates_requested_structure(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """The Test12 folder prompt creates the requested nested file on disk."""
    (temp_workspace / "GitHub").mkdir()
    prompt = (
        f"erstelle einen Ordner mit dem Namen Test12\nim Verzeichnis\n{temp_workspace}/GitHub\n"
        "darin eine Datei mit dem Namen test12345.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    assert (temp_workspace / "GitHub" / "Test12" / "test12345.txt").is_file()
    content = (temp_workspace / "GitHub" / "Test12" / "test12345.txt").read_text(
        encoding="utf-8",
    )
    assert "Hello World" in content
    assert_final_message_contains(result, "GitHub/Test12")


@pytest.mark.asyncio
async def test_write_prompt_audits_scan_and_file_operations(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Write prompts log scanner listings and file writes in command history."""
    (temp_workspace / "GitHub").mkdir()
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    prompt = (
        f"erstelle mir im verzeichnis\n{temp_workspace}/GitHub\n"
        "einen Ordner mit dem Namen. Test123\n"
        "darin eine Datei mit dem Namen test.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )

    patch_chat_ready(monkeypatch)
    patch_materialize_with_fallback(monkeypatch)
    patch_agent_loop(monkeypatch, make_team_loop())

    from tests.helpers.orchestration_test_helpers import create_test_chat

    chat = await create_test_chat(title="Audit write test")
    orchestrator = AgentOrchestrator()
    await orchestrator.run(
        chat.id,
        prompt,
        OrchestrationMode.MULTI,
        ["developer", "reviewer"],
        on_event=on_event,
    )

    commands = audit_commands(events)
    assert any(command.startswith("list_directory") for command in commands)
    assert any(command.startswith("mkdir -p") for command in commands)
    assert any(command.startswith("write_file") for command in commands)


@pytest.mark.asyncio
async def test_write_prompt_injects_missing_directory_context_to_agents(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Write prompts expose scanner context about missing target folders to agents."""
    (temp_workspace / "GitHub").mkdir()
    captured: dict[str, list] = {}
    prompt = (
        f"Erstelle test.txt unter {temp_workspace}/GitHub/Test12 "
        'mit dem Text "Hello World"'
    )

    await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(capture=captured),
    )

    developer_messages = captured.get("developer", [])
    assert developer_messages, "Expected at least one developer turn"
    combined = "\n".join(
        message.get("content", "")
        for turn in developer_messages
        for message in turn
        if isinstance(message, dict)
    )
    assert "GitHub/Test12" in combined
    assert "does not exist yet" in combined


@pytest.mark.asyncio
async def test_read_prompt_audits_prefetch_operations(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Read prompts log pre-fetch reads in command history."""
    write_workspace_file(temp_workspace, "GitHub/Test12/test12345.txt", "Hello World")
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    prompt = (
        f"lese den dateiinhalt von {temp_workspace}/GitHub/Test12/test12345.txt "
        "und liste mir den inhalt hier auf"
    )

    patch_chat_ready(monkeypatch)
    patch_agent_loop(monkeypatch, make_team_loop())

    from tests.helpers.orchestration_test_helpers import create_test_chat

    chat = await create_test_chat(title="Audit read test")
    orchestrator = AgentOrchestrator()
    await orchestrator.run(
        chat.id,
        prompt,
        OrchestrationMode.MULTI,
        ["developer", "reviewer"],
        on_event=on_event,
    )

    commands = audit_commands(events)
    assert "read_file GitHub/Test12/test12345.txt" in commands


@pytest.mark.asyncio
async def test_multi_write_prompt_survives_weak_developer_json(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Weak developer JSON must not prevent successful write deliverables."""
    (temp_workspace / "GitHub").mkdir()
    prompt = (
        f"Erstelle index.php unter {temp_workspace}/GitHub/Project "
        "mit Header, Menü, Content und Footer"
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(role_responses={"developer": '{"status": "success"}'}),
    )

    assert (temp_workspace / "GitHub" / "Project" / "index.php").is_file()
    assert result.messages[-1].content


@pytest.mark.asyncio
async def test_reviewer_receives_verified_write_summary_in_prompt(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Reviewers must see verified write summaries instead of only weak JSON."""
    (temp_workspace / "GitHub").mkdir()
    captured: dict[str, list] = {}
    prompt = (
        f"erstelle mir im verzeichnis\n{temp_workspace}/GitHub\n"
        "einen Ordner mit dem Namen. Test123\n"
        "darin eine Datei mit dem Namen test.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )

    await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(
            role_responses={
                "developer": '{"status": "success"}',
                "reviewer": "Files look consistent.",
            },
            capture=captured,
        ),
    )

    reviewer_messages = captured.get("reviewer", [])
    assert reviewer_messages, "Expected reviewer to participate"
    combined = "\n".join(
        message.get("content", "")
        for turn in reviewer_messages
        for message in turn
        if isinstance(message, dict)
    )
    assert "GitHub/Test123/test.txt" in combined or "Created files on disk" in combined


@pytest.mark.asyncio
async def test_read_prompt_persists_task_board_memory(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Successful read orchestration persists task-board state for follow-up turns."""
    write_workspace_file(temp_workspace, "GitHub/Test12/test12345.txt", "Hello World")
    prompt = (
        f"lese den dateiinhalt von {temp_workspace}/GitHub/Test12/test12345.txt "
        "und liste mir den inhalt hier auf"
    )

    patch_chat_ready(monkeypatch)
    patch_agent_loop(monkeypatch, make_team_loop())

    from tests.helpers.orchestration_test_helpers import create_test_chat

    chat = await create_test_chat(title="Task board persistence")
    orchestrator = AgentOrchestrator()
    await orchestrator.run(
        chat.id,
        prompt,
        OrchestrationMode.MULTI,
        ["developer", "reviewer"],
    )

    board = await load_task_board_memory(chat.id)
    assert board is not None
    assert "GitHub/Test12/test12345.txt" in str(board.get("last_targets", []))


@pytest.mark.asyncio
async def test_list_prompt_injects_directory_context_from_scanner(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """List-directory prompts inject scanner output into agent context."""
    project = temp_workspace / "GitHub" / "Demo"
    project.mkdir(parents=True)
    (project / "alpha.txt").write_text("a", encoding="utf-8")
    (project / "beta.txt").write_text("b", encoding="utf-8")

    captured: dict[str, list] = {}
    prompt = f"Dateien im Ordner {temp_workspace}/GitHub/Demo anzeigen"

    await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(capture=captured),
    )

    pm_messages = captured.get("project_manager", [])
    assert pm_messages, "Expected project manager to participate"
    combined = "\n".join(
        message.get("content", "")
        for turn in pm_messages
        for message in turn
        if isinstance(message, dict)
    )
    assert "GitHub/Demo" in combined
    assert "alpha.txt" in combined or "[FILE]" in combined


@pytest.mark.asyncio
async def test_read_prompt_does_not_trigger_weather_context_for_workspace_task(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Workspace read tasks must not attach unrelated ambient context plugins."""
    write_workspace_file(temp_workspace, "GitHub/Test12/test12345.txt", "Hello World")
    captured: dict[str, list] = {}
    prompt = (
        f"lese den dateiinhalt von {temp_workspace}/GitHub/Test12/test12345.txt "
        "und liste mir den inhalt hier auf"
    )

    monkeypatch.setattr("agentforge.config.settings.context_plugins_enabled", True)
    monkeypatch.setattr(
        "agentforge.config.settings.context_plugins_enabled_list",
        ["weather", "datetime"],
    )

    await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(capture=captured),
    )

    combined = "\n".join(
        message.get("content", "")
        for role_messages in captured.values()
        for turn in role_messages
        for message in turn
        if isinstance(message, dict)
    )
    assert "weather" not in combined.lower()
    assert "temperature" not in combined.lower()


@pytest.mark.asyncio
async def test_write_prompt_does_not_classify_as_read(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Write prompts must stay write tasks and still create deliverables."""
    (temp_workspace / "GitHub").mkdir()
    prompt = (
        f"Erstelle test.txt unter {temp_workspace}/GitHub/OnlyWrite "
        'und schreibe "Sample content"'
    )
    intent = detect_workspace_intent(prompt)

    assert intent.wants_file_creation is True
    assert intent.wants_file_read is False

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    assert_file_content(temp_workspace, "GitHub/OnlyWrite/test.txt", "Sample content")
    assert_final_message_contains(result, "GitHub/OnlyWrite/test.txt")


@pytest.mark.asyncio
async def test_multi_read_prompt_pm_verification_mentions_verified_files(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """PM verification in read flows references verified file facts."""
    write_workspace_file(temp_workspace, "GitHub/Test12/test12345.txt", "Hello World")
    prompt = (
        f"lese den dateiinhalt von {temp_workspace}/GitHub/Test12/test12345.txt "
        "und liste mir den inhalt hier auf"
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    verification_text = "\n".join(
        discussion.content
        for discussion in result.agent_discussions
        if "VERDICT:" in discussion.content
    )
    assert "VERDICT: pass" in verification_text


@pytest.mark.asyncio
async def test_scanner_lists_existing_github_structure_for_write_prompt(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Write prompts against GitHub receive existing sibling directory context."""
    github = temp_workspace / "GitHub"
    github.mkdir()
    (github / "agent-forge").mkdir()
    (github / "RecoverScope").mkdir()

    prompt = (
        f"Erstelle test.txt unter {temp_workspace}/GitHub/Test12 "
        'mit dem Text "Hello World"'
    )
    intent = detect_workspace_intent(prompt)

    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async with command_audit_scope("chat", "system", "System", on_event):
        context = await build_workspace_path_context(intent)

    assert "GitHub/Test12" in context
    assert "does not exist yet" in context
    assert any(command.startswith("list_directory") for command in audit_commands(events))
