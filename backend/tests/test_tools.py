"""Async tests for agent tools."""

import pytest

from agentforge.services.command_audit import execute_shell_command
from agentforge.tools.registry import ReadFileTool, WriteFileTool, _resolve_path
from agentforge.tools.shell_security import classify_shell_command


def test_resolve_path_blocks_escape(temp_workspace) -> None:
    """Paths outside workspace root are rejected."""
    with pytest.raises(PermissionError):
        _resolve_path("../../etc/passwd")


@pytest.mark.asyncio
async def test_write_and_read_file(temp_workspace) -> None:
    """Write and read tools operate within workspace."""
    write_tool = WriteFileTool()
    read_tool = ReadFileTool()
    write_result = await write_tool.execute({"path": "test.txt", "content": "Hello World"})
    assert write_result.success is True
    read_result = await read_tool.execute({"path": "test.txt"})
    assert read_result.success is True
    assert "Hello World" in read_result.output


@pytest.mark.asyncio
async def test_shell_whitelist_allows_echo(temp_workspace) -> None:
    """Whitelisted commands run through the central audit executor."""
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    result = await execute_shell_command(
        "chat-test",
        command="echo unit-test-ok",
        cwd=".",
        agent_id="developer",
        agent_name="Developer",
        approval_callback=None,
        on_event=on_event,
    )
    assert result.success is True
    assert "unit-test-ok" in result.output
    assert any(event["type"] == "shell_command_recorded" for event in events)


@pytest.mark.asyncio
async def test_shell_blacklist_blocks_rm(temp_workspace) -> None:
    """Blacklisted commands are rejected and logged."""
    result = await execute_shell_command(
        "chat-test",
        command="rm -rf /",
        cwd=".",
        agent_id="developer",
        agent_name="Developer",
        approval_callback=None,
        on_event=None,
    )
    assert result.success is False
    assert "blocked" in result.output.lower()


@pytest.mark.asyncio
async def test_shell_unknown_requires_approval(temp_workspace, english_locale) -> None:
    """Non-whitelisted commands require approval when no callback is set."""
    result = await execute_shell_command(
        "chat-test",
        command="unknown-cmd-test-xyz",
        cwd=".",
        agent_id="developer",
        agent_name="Developer",
        approval_callback=None,
        on_event=None,
    )
    assert result.success is False
    assert result.requires_approval is True


def test_shell_check_command_invalid_syntax() -> None:
    """Malformed shell syntax is rejected."""
    classification = classify_shell_command("echo 'unclosed")
    assert classification.allowed is False
    assert "syntax" in classification.reason.lower()
