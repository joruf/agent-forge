"""Tests for central command audit logging."""

from __future__ import annotations

import pytest

from agentforge.services.command_audit import (
    command_audit_scope,
    parents_to_create,
)
from agentforge.tools.registry import ListDirectoryTool, ReadFileTool, WriteFileTool


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
            "content": "Hello World",
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


@pytest.mark.asyncio
async def test_read_file_is_audited_when_context_active(
    monkeypatch,
    tmp_path,
) -> None:
    """Workspace file reads are logged in command history when audit scope is active."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    target = tmp_path / "GitHub" / "Test12"
    target.mkdir(parents=True)
    (target / "test.txt").write_text("Hello World", encoding="utf-8")

    messages: list[dict] = []

    async def on_event(event: dict) -> None:
        messages.append(event)

    async with command_audit_scope("chat-1", "developer", "Developer", on_event):
        tool = ReadFileTool()
        result = await tool.execute({"path": "GitHub/Test12/test.txt"})

    assert result.success is True
    commands = [
        event["entry"]["command"]
        for event in messages
        if event.get("type") == "shell_command_recorded"
    ]
    assert "read_file GitHub/Test12/test.txt" in commands


@pytest.mark.asyncio
async def test_list_directory_is_audited_when_context_active(
    monkeypatch,
    tmp_path,
) -> None:
    """Workspace directory listings are logged in command history when audit scope is active."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    target = tmp_path / "GitHub"
    target.mkdir(parents=True)
    (target / "readme.txt").write_text("Hello World", encoding="utf-8")

    messages: list[dict] = []

    async def on_event(event: dict) -> None:
        messages.append(event)

    async with command_audit_scope("chat-1", "system", "System", on_event):
        tool = ListDirectoryTool()
        result = await tool.execute({"path": "GitHub"})

    assert result.success is True
    commands = [
        event["entry"]["command"]
        for event in messages
        if event.get("type") == "shell_command_recorded"
    ]
    assert "list_directory GitHub" in commands


@pytest.mark.asyncio
async def test_workspace_scanner_audits_directory_scan(
    monkeypatch,
    tmp_path,
) -> None:
    """Automatic workspace scans are logged in command history."""
    from agentforge.agents.workspace_intent import detect_workspace_intent
    from agentforge.agents.workspace_scanner import build_workspace_path_context
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    github = tmp_path / "GitHub"
    github.mkdir(parents=True)
    (github / "agent-forge").mkdir()

    messages: list[dict] = []

    async def on_event(event: dict) -> None:
        messages.append(event)

    prompt = f"Erstelle Test12 unter {tmp_path}/GitHub"
    intent = detect_workspace_intent(prompt)

    async with command_audit_scope("chat-1", "system", "System", on_event):
        await build_workspace_path_context(intent)

    commands = [
        event["entry"]["command"]
        for event in messages
        if event.get("type") == "shell_command_recorded"
    ]
    assert any(command.startswith("list_directory") for command in commands)


def test_parents_to_create_lists_missing_directories(monkeypatch, tmp_path) -> None:
    """Missing parent directories are detected before file writes."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    assert parents_to_create("GitHub/Test123/test.txt") == ["GitHub", "GitHub/Test123"]
