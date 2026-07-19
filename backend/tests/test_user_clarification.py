"""Tests for unified user clarification dialog."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.user_clarification import (
    ClarificationKind,
    build_clarification_options,
    is_clarification_pending,
    should_skip_clarification_escalation,
)
from agentforge.agents.task_state import TaskType, build_task_state, seed_edit_facts
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.models.schemas import ApprovalResponse, OrchestrationResumeState
from tests.helpers.orchestration_test_helpers import make_team_loop, run_orchestration


@pytest.mark.asyncio
async def test_weak_retry_triggers_user_choice_not_plain_ask_user(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    """Tool loop opens clarification when weak retries are exhausted."""
    from agentforge.agents.task_state import (
        MAX_WEAK_RETRIES,
        build_task_state,
        increment_weak_retry,
    )
    from agentforge.agents.user_clarification import clarification_pending_marker
    from agentforge.agents.workspace_intent import detect_workspace_intent
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", temp_workspace)
    prompt = "Create GitHub/demo.txt with hello content"
    intent = detect_workspace_intent(prompt)
    task_state = build_task_state(prompt, intent)
    for _ in range(MAX_WEAK_RETRIES):
        increment_weak_retry(task_state, "developer")

    captured: list[str] = []

    async def fake_request(*_args, **kwargs):
        captured.append(str(kwargs.get("kind") or _args[1]))
        return "approval-test-id"

    monkeypatch.setattr(
        "agentforge.agents.orchestrator_mixins.tool_loop.request_clarification",
        fake_request,
    )

    orchestrator = AgentOrchestrator()
    llm, routing = await orchestrator._resolve_llm(prompt, role_id="developer", mode_single=True)
    tools = orchestrator._build_tools("tool-loop-test", "chat")
    agent_tools = orchestrator._tools_for_role("developer", "tool-loop-test", "chat", tools)
    messages = [
        {"role": "system", "content": "test"},
        {"role": "user", "content": prompt},
    ]

    async def fake_complete(_messages, tools=None, max_tokens=None):
        return {
            "content": '{"status": "success"}',
            "tool_calls": [],
            "model": "test-model",
        }

    monkeypatch.setattr(llm, "complete", fake_complete)

    content, _routing = await orchestrator._run_agent_tool_loop(
        llm=llm,
        routing=routing,
        chat_id="tool-loop-test",
        agent_id="developer",
        agent_name="Developer",
        role_id="developer",
        user_content=prompt,
        mode_single=True,
        mode_multi=False,
        messages=messages,
        tools=agent_tools,
        memory_scope="chat",
        on_event=None,
        workspace_intent=intent,
        task_state=task_state,
    )

    assert captured
    assert captured[0] == ClarificationKind.AGENT_BLOCKED
    assert content == clarification_pending_marker()


@pytest.mark.asyncio
async def test_ask_user_in_multi_agent_opens_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
    temp_workspace: Path,
) -> None:
    """Explicit [ASK_USER] agent output opens a clarification dialog."""
    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        "Write a config file somewhere in the workspace",
        role_ids=["developer", "project_manager"],
        agent_loop=make_team_loop(
            role_responses={
                "developer": "[ASK_USER] Which file path should I use?",
                "project_manager": "Waiting for user input.",
            },
        ),
    )

    pending = approval_manager.list_pending(result.chat_id)
    assert pending
    assert pending[0].action_type == "user_choice"
    assert pending[0].payload["kind"] == ClarificationKind.AGENT_QUESTION
    assert "Which file path" in pending[0].description
    assert result.pending_approvals
    assert not any(message.metadata.get("needs_user_input") for message in result.messages)


@pytest.mark.asyncio
async def test_custom_text_reply_resumes_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom clarification text resumes orchestration via execute_approved_command."""
    resumed: list[str] = []

    async def fake_run(self, chat_id, user_content, mode, role_ids, **kwargs):
        resumed.append(user_content)
        from agentforge.models.schemas import MessageRole, OrchestrationResponse
        from agentforge.storage.conversation_store import conversation_store

        message = await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            "Resumed successfully.",
            metadata={"resumed": True},
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[message],
            agent_discussions=[],
            pending_approvals=[],
        )

    monkeypatch.setattr(AgentOrchestrator, "run", fake_run)
    orchestrator = AgentOrchestrator()

    approval_id = await approval_manager.request(
        "custom-reply-test",
        "user_choice",
        "Which path should be used?",
        {
            "kind": ClarificationKind.AGENT_QUESTION,
            "question": "Which path should be used?",
            "options": [
                option.model_dump()
                for option in build_clarification_options(ClarificationKind.AGENT_QUESTION, {})
            ],
            "allows_custom_input": True,
            "context": {},
        },
    )
    approval_manager.set_resume_state(
        approval_id,
        OrchestrationResumeState(
            kind=ClarificationKind.AGENT_QUESTION,
            chat_id="custom-reply-test",
            user_content="Create config file",
            context={"role_id": "developer"},
            mode="multi",
            role_ids=["developer"],
            question_text="Which path should be used?",
        ),
    )

    message = await orchestrator.execute_approved_command(
        "custom-reply-test",
        approval_id,
        ApprovalResponse(approved=True, choice_id="custom_reply", comment="Use config/app.yaml"),
    )

    assert message is not None
    assert "Resumed successfully." in message.content
    assert resumed
    assert "User clarification: Use config/app.yaml" in resumed[0]


def test_build_clarification_options_agent_blocked_includes_custom_reply() -> None:
    """Agent-blocked clarifications offer retry, custom reply, and abort."""
    options = build_clarification_options(ClarificationKind.AGENT_BLOCKED, {})
    option_ids = [option.id for option in options]
    assert option_ids == ["retry", "custom_reply", "abort"]


def test_is_clarification_pending_detects_marker() -> None:
    """Clarification pending marker is recognized by helper."""
    assert is_clarification_pending("[CLARIFICATION_PENDING]")
    assert not is_clarification_pending("[ASK_USER] Need help")


def test_should_skip_clarification_for_deterministic_read_errors() -> None:
    """Read prompts with prefetch errors should not escalate to clarification."""
    from agentforge.agents.task_state import seed_read_facts

    prompt = "Read GitHub/Test12/fehlt.txt"
    intent = detect_workspace_intent(prompt)
    task_state = build_task_state(prompt, intent)
    seed_read_facts(
        task_state,
        {"GitHub/Test12/fehlt.txt": "[ERROR] File not found"},
    )

    assert should_skip_clarification_escalation(task_state, intent) is True
