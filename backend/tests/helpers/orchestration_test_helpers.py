"""Reusable helpers for prompt-to-outcome orchestration tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.config import settings
from agentforge.models.schemas import ChatCreate, ChatMemorySettings, OrchestrationMode
from agentforge.storage.conversation_store import conversation_store


AgentLoopHandler = Callable[..., Awaitable[tuple[str, dict[str, Any]]]]

# Qualified placeholder text for file content in persistence/orchestration tests.
SAMPLE_FILE_CONTENT = "Hello World"
SAMPLE_WRITE_ONLY_CONTENT = "Sample content"


def patch_chat_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bypass model readiness checks so orchestration tests stay offline.

    :param monkeypatch: Pytest monkeypatch fixture
    """

    async def fake_readiness(**_kwargs: Any) -> dict[str, Any]:
        return {
            "chat_ready": True,
            "summary": "Ready for tests.",
            "blocking_message": None,
        }

    monkeypatch.setattr(
        "agentforge.agents.orchestrator.run_readiness_check",
        fake_readiness,
    )


def patch_materialize_with_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Skip LLM file generation and rely on deterministic fallback content.

    :param monkeypatch: Pytest monkeypatch fixture
    """

    async def fake_materialize(
        self: AgentOrchestrator,
        _user_content: str,
        _file_paths: list[str],
        role_id: str = "developer",
    ) -> str:
        return ""

    monkeypatch.setattr(AgentOrchestrator, "_materialize_missing_files", fake_materialize)


def patch_agent_loop(
    monkeypatch: pytest.MonkeyPatch,
    handler: AgentLoopHandler,
) -> None:
    """
    Replace the agent tool loop with a deterministic handler.

    :param monkeypatch: Pytest monkeypatch fixture
    :param handler: Async callback invoked for each agent turn
    """
    monkeypatch.setattr(AgentOrchestrator, "_agent_loop", handler)


def make_team_loop(
    *,
    role_responses: dict[str, str | list[str]] | None = None,
    pm_final: str = "Task completed.",
    capture: dict[str, list[dict[str, Any]]] | None = None,
) -> AgentLoopHandler:
    """
    Build a fake multi-agent loop with per-role canned responses.

    :param role_responses: Mapping of role_id to one response or a list per call
    :param pm_final: Response when PM receives the final synthesis prompt
    :param capture: Optional dict that collects system/user messages per role
    :return: Async handler suitable for patch_agent_loop
    """
    counters: dict[str, int] = {}
    responses = role_responses or {}

    async def handler(
        self: AgentOrchestrator,
        chat_id: str,
        agent_id: str,
        agent_name: str,
        messages: list[dict[str, Any]],
        tools,
        memory_scope: str,
        on_event=None,
        user_content: str = "",
        role_id: str | None = None,
        mode_single: bool = False,
        mode_multi: bool = False,
        intervention_queue=None,
        workspace_intent=None,
        task_state=None,
        round_num=0,
        **kwargs,
    ) -> tuple[str, dict[str, Any]]:
        if capture is not None:
            capture.setdefault(agent_id, []).append(list(messages))

        if agent_id == "project_manager":
            user_message = messages[1]["content"] if len(messages) > 1 else ""
            if "Final synthesis requested." in user_message:
                return (
                    pm_final,
                    {"model": "ollama/mock-pm", "role_id": "project_manager"},
                )

        configured = responses.get(agent_id, '{"status": "success"}')
        if isinstance(configured, list):
            index = counters.get(agent_id, 0)
            counters[agent_id] = index + 1
            content = configured[min(index, len(configured) - 1)]
        else:
            content = configured

        return (
            content,
            {"model": f"ollama/mock-{agent_id}", "role_id": agent_id},
        )

    return handler


async def create_test_chat(
    *,
    mode: str = "multi",
    role_ids: list[str] | None = None,
    title: str = "Prompt quality test",
) -> Any:
    """
    Create an isolated chat session for orchestration tests.

    :param mode: Chat mode label
    :param role_ids: Selected role identifiers
    :param title: Chat title
    :return: Created chat response
    """
    return await conversation_store.create_chat(
        ChatCreate(
            title=title,
            mode=mode,
            role_ids=role_ids or ["developer", "reviewer"],
            memory=ChatMemorySettings(),
        )
    )


async def run_orchestration(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
    prompt: str,
    *,
    mode: OrchestrationMode | str = OrchestrationMode.MULTI,
    role_ids: list[str] | None = None,
    agent_loop: AgentLoopHandler | None = None,
    skip_materialize_mock: bool = False,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> Any:
    """
    Execute one orchestration run with standard offline test patches applied.

    :param monkeypatch: Pytest monkeypatch fixture
    :param temp_workspace: Isolated workspace root
    :param prompt: User prompt
    :param mode: Orchestration mode
    :param role_ids: Selected roles
    :param agent_loop: Optional fake agent loop handler
    :param skip_materialize_mock: Keep real materialization when testing LLM writes
    :param on_event: Optional WebSocket event collector
    :return: Orchestration response
    """
    monkeypatch.setattr(settings, "workspace_root", temp_workspace)
    patch_chat_ready(monkeypatch)
    if not skip_materialize_mock:
        patch_materialize_with_fallback(monkeypatch)
    if agent_loop is not None:
        patch_agent_loop(monkeypatch, agent_loop)

    chat = await create_test_chat(
        mode=str(mode.value if isinstance(mode, OrchestrationMode) else mode),
        role_ids=role_ids,
    )
    orchestrator = AgentOrchestrator()
    return await orchestrator.run(
        chat.id,
        prompt,
        mode,
        role_ids or ["developer", "reviewer"],
        on_event=on_event,
    )


def workspace_path(temp_workspace: Path, relative: str) -> Path:
    """
    Resolve a workspace-relative path inside the temp workspace fixture.

    :param temp_workspace: Workspace root
    :param relative: Workspace-relative path
    :return: Absolute path
    """
    return temp_workspace / relative


def write_workspace_file(temp_workspace: Path, relative: str, content: str) -> Path:
    """
    Create one file under the temp workspace.

    :param temp_workspace: Workspace root
    :param relative: Workspace-relative file path
    :param content: File body
    :return: Absolute file path
    """
    target = workspace_path(temp_workspace, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def audit_commands(events: list[dict[str, Any]]) -> list[str]:
    """
    Extract command labels from collected shell_command_recorded events.

    :param events: WebSocket-style event payloads
    :return: Ordered command labels
    """
    commands: list[str] = []
    for event in events:
        if event.get("type") != "shell_command_recorded":
            continue
        entry = event.get("entry") or {}
        command = str(entry.get("command") or "")
        if command:
            commands.append(command)
    return commands


def assert_file_content(temp_workspace: Path, relative: str, expected: str) -> None:
    """
    Assert that a workspace file exists and matches expected text.

    :param temp_workspace: Workspace root
    :param relative: Workspace-relative file path
    :param expected: Expected file body
    """
    path = workspace_path(temp_workspace, relative)
    if not path.is_file():
        existing = [
            str(item.relative_to(temp_workspace))
            for item in temp_workspace.rglob("*")
            if item.is_file()
        ]
        raise AssertionError(
            f"Expected file missing: {relative}. Existing files: {existing}"
        )
    assert path.read_text(encoding="utf-8") == expected


def assert_final_message_contains(result: Any, *needles: str) -> None:
    """
    Assert that the last assistant message contains all given substrings.

    :param result: Orchestration response
    :param needles: Required substrings
    """
    assert result.messages, "Expected at least one response message"
    final_content = result.messages[-1].content
    for needle in needles:
        assert needle in final_content, (
            f"Expected {needle!r} in final message, got: {final_content[:500]}"
        )
