"""Mandatory command audit coverage tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.services.command_audit import (
    command_audit_scope,
    execute_approved_shell_command,
    execute_shell_command,
    record_from_context,
    serialize_shell_command_entry,
)
from agentforge.storage.conversation_store import conversation_store
from agentforge.tools.registry import ListDirectoryTool, ReadFileTool, ShellTool, WriteFileTool


ALLOWED_RUN_SHELL_FILES = {
    "command_audit.py",
    "shell_security.py",
}


@pytest.fixture
def audit_events() -> list[dict]:
    """Collect shell_command_recorded WebSocket events during a test."""
    return []


@pytest.fixture
def on_audit_event(audit_events: list[dict]):
    """Return an on_event callback that stores audit events."""

    async def _on_event(event: dict) -> None:
        audit_events.append(event)

    return _on_event


def logged_commands(audit_events: list[dict]) -> list[str]:
    """
    Extract command labels from collected audit events.

    :param audit_events: WebSocket-style event payloads
    :return: Ordered command labels
    """
    commands: list[str] = []
    for event in audit_events:
        if event.get("type") != "shell_command_recorded":
            continue
        entry = event.get("entry") or {}
        command = str(entry.get("command") or "")
        if command:
            commands.append(command)
    return commands


async def shell_commands_in_chat(chat_id: str) -> list[dict]:
    """
    Return persisted shell command entries for one chat.

    :param chat_id: Chat session ID
    :return: Serialized command entries
    """
    messages = await conversation_store.list_messages(chat_id)
    entries: list[dict] = []
    for message in messages:
        if (message.metadata or {}).get("kind") != "shell_command":
            continue
        entries.append(serialize_shell_command_entry(message))
    return entries


@pytest.mark.audit
@pytest.mark.parametrize("command", ["ls", "pwd", "echo audit-marker"])
@pytest.mark.asyncio
async def test_whitelisted_shell_commands_are_logged(
    temp_workspace,
    command: str,
    audit_events: list[dict],
    on_audit_event,
) -> None:
    """Every executed whitelisted shell command is persisted and emitted."""
    chat_id = f"audit-shell-{command.replace(' ', '-')}"

    result = await execute_shell_command(
        chat_id,
        command=command,
        cwd=".",
        agent_id="developer",
        agent_name="Developer",
        approval_callback=None,
        on_event=on_audit_event,
    )

    assert result.success is True
    assert command in logged_commands(audit_events)

    stored = await shell_commands_in_chat(chat_id)
    assert stored
    assert stored[-1]["command"] == command
    assert stored[-1]["source"] == "shell"


@pytest.mark.audit
@pytest.mark.asyncio
async def test_ls_command_via_execute_tool_call_is_audited(
    temp_workspace,
    audit_events: list[dict],
    on_audit_event,
) -> None:
    """Orchestrator tool execution routes shell commands through the audit gateway."""
    orchestrator = AgentOrchestrator()

    result = await orchestrator._execute_tool_call(
        chat_id="audit-tool-call",
        tools=orchestrator._build_tools("audit-tool-call", "chat"),
        tool_call={
            "name": "run_command",
            "arguments": json.dumps({"command": "ls", "cwd": "."}),
        },
        approval_cb=AsyncMock(),
        agent_id="developer",
        agent_name="Developer",
        on_event=on_audit_event,
    )

    assert result.success is True
    assert "ls" in logged_commands(audit_events)

    stored = await shell_commands_in_chat("audit-tool-call")
    assert any(entry["command"] == "ls" for entry in stored)


@pytest.mark.audit
@pytest.mark.asyncio
async def test_approved_shell_command_is_logged(
    temp_workspace,
    audit_events: list[dict],
    on_audit_event,
) -> None:
    """Post-approval shell execution is logged like any other shell command."""
    message = await execute_approved_shell_command(
        "audit-approved-chat",
        command="ls",
        cwd=".",
        approval_id="approval-123",
        on_event=on_audit_event,
    )

    assert message.metadata is not None
    assert message.metadata.get("command") == "ls"
    assert "ls" in logged_commands(audit_events)


@pytest.mark.audit
@pytest.mark.parametrize(
    ("tool_cls", "arguments", "expected_prefix"),
    [
        (ReadFileTool, {"path": "GitHub/demo.txt"}, "read_file"),
        (WriteFileTool, {"path": "GitHub/demo.txt", "content": "Hello World"}, "write_file"),
        (ListDirectoryTool, {"path": "GitHub"}, "list_directory"),
    ],
)
@pytest.mark.asyncio
async def test_workspace_tools_require_audit_context(
    temp_workspace,
    tool_cls,
    arguments: dict,
    expected_prefix: str,
    audit_events: list[dict],
    on_audit_event,
) -> None:
    """Workspace tools log operations only when command audit scope is active."""
    (temp_workspace / "GitHub").mkdir(parents=True, exist_ok=True)
    if tool_cls is ReadFileTool:
        (temp_workspace / "GitHub" / "demo.txt").write_text("Hello World", encoding="utf-8")

    tool = tool_cls()
    result_without_scope = await tool.execute(arguments)
    assert result_without_scope.success in {True, False}
    assert logged_commands(audit_events) == []

    async with command_audit_scope("audit-workspace", "developer", "Developer", on_audit_event):
        result_with_scope = await tool.execute(arguments)

    assert result_with_scope.success is True
    assert any(command.startswith(expected_prefix) for command in logged_commands(audit_events))


@pytest.mark.audit
@pytest.mark.asyncio
async def test_record_from_context_without_scope_is_silent(temp_workspace) -> None:
    """Missing audit scope must not crash, but also must not emit command history."""
    recorded = await record_from_context(
        command="ls",
        cwd=".",
        status="success",
        success=True,
        exit_code=0,
        output="noop",
        source="shell",
    )
    assert recorded is None


@pytest.mark.audit
def test_run_shell_command_is_only_used_in_audit_layer() -> None:
    """Direct shell execution must stay inside the audit/security layer."""
    backend_root = Path(__file__).resolve().parents[1] / "agentforge"
    offenders: list[str] = []

    for path in backend_root.rglob("*.py"):
        if path.name in ALLOWED_RUN_SHELL_FILES:
            continue
        if "__pycache__" in path.parts:
            continue
        content = path.read_text(encoding="utf-8")
        if "run_shell_command(" in content:
            offenders.append(str(path.relative_to(backend_root.parent)))

    assert offenders == [], (
        "run_shell_command() must only be called from command_audit.py or "
        f"shell_security.py. Found in: {', '.join(offenders)}"
    )


@pytest.mark.audit
@pytest.mark.asyncio
async def test_shell_tool_stub_does_not_execute_directly() -> None:
    """The registry shell tool must not execute commands outside command_audit."""
    tool = ShellTool()
    result = await tool.execute({"command": "ls"})
    assert result.success is False
    assert "central command audit" in result.output.lower()
