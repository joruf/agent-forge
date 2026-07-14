"""Tests for central command audit logging."""

from __future__ import annotations

import pytest

from agentforge.services.command_audit import (
    command_audit_scope,
    parents_to_create,
)
from agentforge.tools.registry import WriteFileTool


@pytest.mark.asyncio
async def test_write_file_is_audited_when_context_active(
    monkeypatch,
    tmp_path,
) -> None:
    """Workspace file writes are logged in command history when audit scope is active."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    messages: list[dict] = []

    async def on_event(event: dict) -> None:
        messages.append(event)

    async with command_audit_scope("chat-1", "developer", "Developer", on_event):
        tool = WriteFileTool()
        result = await tool.execute({
            "path": "GitHub/Test123/test.txt",
            "content": "Hallo Welt",
        })

    assert result.success is True
    assert any(event["type"] == "shell_command_recorded" for event in messages)
    commands = [
        event["entry"]["command"]
        for event in messages
        if event.get("type") == "shell_command_recorded"
    ]
    assert "write_file GitHub/Test123/test.txt" in commands
    assert "mkdir -p GitHub/Test123" in commands


def test_parents_to_create_lists_missing_directories(monkeypatch, tmp_path) -> None:
    """Missing parent directories are detected before file writes."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    assert parents_to_create("GitHub/Test123/test.txt") == ["GitHub", "GitHub/Test123"]
