"""Async tests for agent tools."""

import pytest

from agentforge.services.command_audit import execute_shell_command
from agentforge.tools.registry import ReadFileTool, WriteFileTool, _resolve_path
from agentforge.tools.shell_security import classify_shell_command


@pytest.mark.asyncio
async def test_normalize_workspace_relative_path_maps_home_github_path(
    temp_workspace,
    monkeypatch,
) -> None:
    """write_file accepts home-relative GitHub paths when workspace root is Dokumente."""
    from pathlib import Path

    from agentforge.config import settings
    from agentforge.tools.registry import normalize_workspace_relative_path

    monkeypatch.setattr(settings, "workspace_root", temp_workspace)
    absolute = str(Path.home() / "GitHub" / "emailsender" / "SimpleEmailSender.php")
    mapped = normalize_workspace_relative_path(absolute)
    assert mapped == "GitHub/emailsender/SimpleEmailSender.php"


def test_resolve_path_blocks_escape(temp_workspace) -> None:
    """Paths outside workspace root are rejected."""
    with pytest.raises(PermissionError):
        _resolve_path("../../etc/passwd")


def test_resolve_path_absolute_under_workspace(temp_workspace) -> None:
    """Absolute paths under the workspace root must not double-prefix."""
    target = temp_workspace / "GitHub" / "Test12" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<h1>Hello World</h1>", encoding="utf-8")

    absolute = str(target)
    resolved = _resolve_path(absolute)

    assert resolved == target.resolve()
    assert resolved.is_file()


def test_resolve_path_absolute_workspace_root_file(temp_workspace) -> None:
    """Absolute paths pointing at workspace-root files resolve correctly."""
    target = temp_workspace / "index.html"
    target.write_text("Hello", encoding="utf-8")

    absolute = str(temp_workspace / "index.html")
    resolved = _resolve_path(absolute)

    assert resolved == target.resolve()


@pytest.mark.asyncio
async def test_read_file_accepts_absolute_workspace_path(temp_workspace) -> None:
    """read_file accepts absolute paths that lie inside the workspace root."""
    read_tool = ReadFileTool()
    target = temp_workspace / "GitHub" / "Test12" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Hello Bot", encoding="utf-8")

    result = await read_tool.execute({"path": str(target)})
    assert result.success is True
    assert "Hello Bot" in result.output


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
