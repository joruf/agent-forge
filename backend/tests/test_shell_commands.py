"""Tests for shell command history helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.models.schemas import ToolCallResult
from agentforge.services.command_audit import shell_status_from_output


def test_parse_run_command_arguments() -> None:
    """Parse JSON run_command tool arguments."""
    command, cwd = AgentOrchestrator._parse_run_command_arguments(
        '{"command":"git status","cwd":"src"}'
    )
    assert command == "git status"
    assert cwd == "src"


def test_shell_status_from_output_exit_code() -> None:
    """Detect exit code from shell tool output."""
    status, exit_code = shell_status_from_output("[Exit 2]\nerror", success=False)
    assert status == "failed"
    assert exit_code == 2


def test_shell_status_from_output_blocked() -> None:
    """Detect blocked commands from shell tool output."""
    status, exit_code = shell_status_from_output("Command 'rm' is blocked", success=False)
    assert status == "blocked"
    assert exit_code is None


@pytest.mark.asyncio
async def test_execute_tool_call_routes_shell_through_audit(monkeypatch) -> None:
    """Shell tool calls must use the central audit executor."""
    orchestrator = AgentOrchestrator()
    expected = ToolCallResult(tool="run_command", success=True, output="[OK]")
    monkeypatch.setattr(
        "agentforge.agents.orchestrator.execute_shell_command",
        AsyncMock(return_value=expected),
    )

    result = await orchestrator._execute_tool_call(
        chat_id="chat-1",
        tools=AsyncMock(),
        tool_call={
            "name": "run_command",
            "arguments": json.dumps({"command": "mkdir GitHub/Test123"}),
        },
        approval_cb=AsyncMock(),
        agent_id="developer",
        agent_name="Developer",
        on_event=None,
    )

    assert result.success is True
