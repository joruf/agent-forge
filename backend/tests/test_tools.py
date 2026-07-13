"""Async tests for agent tools."""

import pytest

from agentforge.tools.registry import ReadFileTool, ShellTool, WriteFileTool, _resolve_path


def test_resolve_path_blocks_escape(temp_workspace) -> None:
    """Paths outside workspace root are rejected."""
    with pytest.raises(PermissionError):
        _resolve_path("../../etc/passwd")


@pytest.mark.asyncio
async def test_write_and_read_file(temp_workspace) -> None:
    """Write and read tools operate within workspace."""
    write_tool = WriteFileTool()
    read_tool = ReadFileTool()
    write_result = await write_tool.execute({"path": "test.txt", "content": "hello"})
    assert write_result.success is True
    read_result = await read_tool.execute({"path": "test.txt"})
    assert read_result.success is True
    assert "hello" in read_result.output


@pytest.mark.asyncio
async def test_shell_whitelist_allows_echo(temp_workspace) -> None:
    """Whitelisted commands run without approval."""
    tool = ShellTool()
    result = await tool.execute({"command": "echo unit-test-ok"})
    assert result.success is True
    assert "unit-test-ok" in result.output


@pytest.mark.asyncio
async def test_shell_blacklist_blocks_rm(temp_workspace) -> None:
    """Blacklisted commands are rejected."""
    tool = ShellTool()
    result = await tool.execute({"command": "rm -rf /"})
    assert result.success is False
    assert "blocked" in result.output.lower()


@pytest.mark.asyncio
async def test_shell_unknown_requires_approval(temp_workspace, english_locale) -> None:
    """Non-whitelisted commands require approval when no callback is set."""
    tool = ShellTool()
    result = await tool.execute({"command": "unknown-cmd-test-xyz"})
    assert result.success is False
    assert result.requires_approval is True


def test_shell_check_command_invalid_syntax() -> None:
    """Malformed shell syntax is rejected."""
    tool = ShellTool()
    allowed, needs_approval, reason = tool._check_command("echo 'unclosed")
    assert allowed is False
    assert "syntax" in reason.lower()
