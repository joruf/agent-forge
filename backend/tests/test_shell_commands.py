"""Tests for shell command history helpers."""

from __future__ import annotations

from agentforge.agents.orchestrator import AgentOrchestrator


def test_parse_run_command_arguments() -> None:
    """Parse JSON run_command tool arguments."""
    command, cwd = AgentOrchestrator._parse_run_command_arguments(
        '{"command":"git status","cwd":"src"}'
    )
    assert command == "git status"
    assert cwd == "src"


def test_shell_status_from_output_exit_code() -> None:
    """Detect exit code from shell tool output."""
    status, exit_code = AgentOrchestrator._shell_status_from_output("[Exit 2]\nerror", success=False)
    assert status == "failed"
    assert exit_code == 2


def test_shell_status_from_output_blocked() -> None:
    """Detect blocked commands from shell tool output."""
    status, exit_code = AgentOrchestrator._shell_status_from_output("Command 'rm' is blocked", success=False)
    assert status == "blocked"
    assert exit_code is None
