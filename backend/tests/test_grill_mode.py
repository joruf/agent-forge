"""Tests for grill mode session helpers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.grill_mode import (
    GrillAnswer,
    GrillPhase,
    GrillSession,
    build_grill_execution_prompt,
    build_grill_test_prompt,
    build_grill_ui_payload,
    fallback_grill_interview_step,
    grill_question_already_asked,
    load_grill_session,
    normalize_grill_question,
    parse_grill_interview_response,
    persist_grill_session,
    resolve_grill_execution_mode,
)
from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.models.schemas import ApprovalResponse, ChatCreate, MessageRole, OrchestrationMode
from agentforge.storage.conversation_store import conversation_store
from tests.helpers.orchestration_test_helpers import create_test_chat, patch_chat_ready, run_orchestration


def test_parse_grill_interview_response_question() -> None:
    """Interview JSON with a question is parsed."""
    payload = parse_grill_interview_response(
        '{"status":"question","question":"Who is the user?","recommended_answer":"Developers","why":"Scope"}',
    )
    assert payload is not None
    assert payload["status"] == "question"
    assert payload["question"] == "Who is the user?"


def test_parse_grill_interview_response_complete() -> None:
    """Interview JSON with completion status is parsed."""
    payload = parse_grill_interview_response('{"status":"complete","summary":"All clear"}')
    assert payload is not None
    assert payload["status"] == "complete"


def test_grill_question_duplicate_detection() -> None:
    """Duplicate questions are detected with normalization."""
    session = GrillSession(
        chat_id="chat-1",
        answers=[
            GrillAnswer(
                question="What is the most important constraint?",
                recommended_answer="Keep it simple",
                answer="Keep it simple",
            ),
        ],
    )
    assert grill_question_already_asked(
        session,
        "What is the most important constraint?",
    )
    assert grill_question_already_asked(
        session,
        "What is the most important constraint?!",
    )
    assert not grill_question_already_asked(session, "Which PHP version should be used?")


def test_fallback_grill_interview_step_skips_already_asked() -> None:
    """Fallback questions rotate away from already asked topics."""
    session = GrillSession(
        chat_id="chat-1",
        answers=[
            GrillAnswer(
                question="What outcome defines success for this feature?",
                recommended_answer="MVP",
                answer="MVP",
            ),
        ],
    )
    step = fallback_grill_interview_step(session)
    assert step["status"] == "question"
    assert normalize_grill_question(str(step["question"])) != normalize_grill_question(
        "What outcome defines success for this feature?",
    )


def test_grill_session_round_trip_dict() -> None:
    """Grill session survives dict serialization."""
    session = GrillSession(
        chat_id="chat-1",
        phase=GrillPhase.CLARIFY,
        idea="Build a todo app",
        answers=[
            GrillAnswer(
                question="Target platform?",
                recommended_answer="Web",
                answer="Web",
            ),
        ],
        plan_markdown="# Plan",
        summary="Ready",
    )
    restored = GrillSession.from_dict(session.to_dict())
    assert restored.chat_id == "chat-1"
    assert restored.phase == GrillPhase.CLARIFY
    assert restored.answers[0].answer == "Web"
    assert restored.plan_markdown == "# Plan"


def test_build_grill_test_prompt_includes_plan() -> None:
    """Test prompt references the approved plan and verification task."""
    session = GrillSession(
        chat_id="chat-1",
        idea="Send email in PHP",
        plan_markdown="# Plan\n- Create send.php",
    )
    prompt = build_grill_test_prompt(session)
    assert "Test phase" in prompt
    assert "send.php" in prompt
    assert "PASS or FAIL" in prompt


def test_build_grill_execution_prompt_includes_plan() -> None:
    """Execution prompt contains idea, answers, and approved plan."""
    session = GrillSession(
        chat_id="chat-1",
        idea="Build landing page",
        answers=[
            GrillAnswer(
                question="Framework?",
                recommended_answer="Plain HTML",
                answer="Plain HTML",
            ),
        ],
        plan_markdown="# Plan\n- Create index.html",
    )
    prompt = build_grill_execution_prompt(session)
    assert "Build landing page" in prompt
    assert "Plain HTML" in prompt
    assert "Create index.html" in prompt


def test_build_grill_ui_payload() -> None:
    """UI payload exposes current grill phase metadata."""
    session = GrillSession(
        chat_id="chat-1",
        phase=GrillPhase.PLAN,
        idea="Feature X",
        answers=[GrillAnswer("Q", "A", "A")],
        plan_markdown="# Plan",
    )
    payload = build_grill_ui_payload(session)
    assert payload["type"] == "grill_phase_updated"
    assert payload["phase"] == "plan"
    assert payload["question_count"] == 1
    assert payload["has_plan"] is True


def test_resolve_grill_execution_mode() -> None:
    """Execution mode follows chat mode, with legacy grill inferred from roles."""
    assert resolve_grill_execution_mode("single", ["developer"]) == "single"
    assert resolve_grill_execution_mode("multi", ["developer"]) == "multi"
    assert resolve_grill_execution_mode("grill", ["developer"]) == "single"
    assert resolve_grill_execution_mode("grill", ["developer", "reviewer"]) == "multi"


@pytest.mark.asyncio
async def test_persist_and_load_grill_session(tmp_path, monkeypatch) -> None:
    """Grill session persists in chat memory."""
    from agentforge.config import settings
    from agentforge.agents.grill_mode import load_grill_session, persist_grill_session
    from agentforge.storage.conversation_store import conversation_store
    from agentforge.models.schemas import ChatCreate

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    await conversation_store.initialize()
    chat = await conversation_store.create_chat(ChatCreate(title="Grill", mode="grill"))

    session = GrillSession(chat_id=chat.id, idea="Test idea", phase=GrillPhase.CLARIFY)
    await persist_grill_session(session)
    loaded = await load_grill_session(chat.id)
    assert loaded is not None
    assert loaded.idea == "Test idea"
    assert loaded.phase == GrillPhase.CLARIFY


@pytest.mark.asyncio
async def test_grill_mode_does_not_emit_task_board_on_start(
    tmp_path,
    monkeypatch,
) -> None:
    """Grill mode must not publish execution task boards during clarify/plan."""
    from agentforge.config import settings

    events: list[dict[str, Any]] = []

    async def capture_event(event: dict[str, Any]) -> None:
        events.append(event)

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        return {
            "status": "question",
            "question": "Which PHP version?",
            "recommended_answer": "PHP 8.4",
            "why": "Compatibility",
        }

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(
        AgentOrchestrator,
        "_generate_grill_interview_step",
        fake_interview_step,
    )
    await conversation_store.initialize()

    prompt = "Create a PHP email program in home/joruf/GitHub/emailsender"
    result = await run_orchestration(
        monkeypatch,
        tmp_path / "workspace",
        prompt,
        mode=OrchestrationMode.GRILL,
        on_event=capture_event,
    )

    assert any(event.get("type") == "grill_phase_updated" for event in events)
    assert not any(event.get("type") == "task_board_updated" for event in events)
    assert result.pending_approvals
    assert result.pending_approvals[0].action_type == "user_choice"


@pytest.mark.asyncio
async def test_grill_mode_blocks_duplicate_clarification_turn(
    tmp_path,
    monkeypatch,
) -> None:
    """Follow-up chat messages must not start another grill question without an answer."""
    from agentforge.config import settings

    call_count = {"interview": 0}

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        call_count["interview"] += 1
        return {
            "status": "question",
            "question": "Which mail transport?",
            "recommended_answer": "SMTP",
            "why": "Delivery",
        }

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(
        AgentOrchestrator,
        "_generate_grill_interview_step",
        fake_interview_step,
    )
    await conversation_store.initialize()

    chat = await create_test_chat(mode="grill", title="Grill duplicate test")
    orchestrator = AgentOrchestrator()
    first = await orchestrator.run(
        chat.id,
        "Create a PHP email program",
        OrchestrationMode.GRILL,
        chat.role_ids,
    )
    assert call_count["interview"] == 1
    pending = first.pending_approvals[0]
    approval_manager.pop_resume_state(pending.id)
    await approval_manager.respond(
        pending.id,
        ApprovalResponse(approved=False, choice_id="abort"),
    )

    second = await orchestrator.run(
        chat.id,
        "SMTP with Gmail",
        OrchestrationMode.GRILL,
        chat.role_ids,
    )

    assert call_count["interview"] == 1
    assert "waiting for your clarification question" in second.messages[-1].content.lower()
    session = await load_grill_session(first.chat_id)
    assert session is not None
    assert len(session.answers) == 0


@pytest.mark.asyncio
async def test_grill_mode_duplicate_orchestration_does_not_record_idea_as_answer(
    tmp_path,
    monkeypatch,
) -> None:
    """A duplicate run of the initial idea must not advance to grill question 2."""
    from agentforge.config import settings

    call_count = {"interview": 0}
    questions = [
        {
            "status": "question",
            "question": "Which PHP version?",
            "recommended_answer": "PHP 8.4",
            "why": "Compatibility",
        },
        {
            "status": "question",
            "question": "Which mail transport?",
            "recommended_answer": "SMTP",
            "why": "Delivery",
        },
    ]

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        call_count["interview"] += 1
        index = min(len(_session.answers), len(questions) - 1)
        return questions[index]

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(
        AgentOrchestrator,
        "_generate_grill_interview_step",
        fake_interview_step,
    )
    await conversation_store.initialize()

    prompt = "Create a PHP email program in home/joruf/GitHub/emailsender"
    chat = await create_test_chat(
        mode="multi",
        title="Grill duplicate orchestration",
        grill_enabled=True,
    )
    orchestrator = AgentOrchestrator()
    first = await orchestrator.run(
        chat.id,
        prompt,
        OrchestrationMode.MULTI,
        chat.role_ids,
    )

    assert call_count["interview"] == 1
    assert first.pending_approvals
    session = await load_grill_session(chat.id)
    assert session is not None
    assert len(session.answers) == 0

    second = await orchestrator.run(
        chat.id,
        prompt,
        OrchestrationMode.MULTI,
        chat.role_ids,
    )

    assert call_count["interview"] == 1
    session = await load_grill_session(chat.id)
    assert session is not None
    assert len(session.answers) == 0
    assert second.pending_approvals
    messages = await conversation_store.list_messages(chat.id)
    grill_questions = [
        message
        for message in messages
        if message.role == MessageRole.ASSISTANT and "**Grill question" in message.content
    ]
    assert len(grill_questions) == 1


@pytest.mark.asyncio
async def test_grill_mode_concurrent_starts_only_one_question(
    tmp_path,
    monkeypatch,
) -> None:
    """Parallel grill orchestration must not emit two clarification questions."""
    import asyncio

    from agentforge.config import settings

    call_count = {"interview": 0}

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        call_count["interview"] += 1
        await asyncio.sleep(0.05)
        return {
            "status": "question",
            "question": "Which PHP version?",
            "recommended_answer": "PHP 8.4",
            "why": "Compatibility",
        }

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(
        AgentOrchestrator,
        "_generate_grill_interview_step",
        fake_interview_step,
    )
    await conversation_store.initialize()

    prompt = "Create a PHP email program in home/joruf/GitHub/emailsender"
    chat = await create_test_chat(
        mode="multi",
        title="Grill concurrent start",
        grill_enabled=True,
    )
    orchestrator = AgentOrchestrator()
    await asyncio.gather(
        orchestrator.run(
            chat.id,
            prompt,
            OrchestrationMode.MULTI,
            chat.role_ids,
        ),
        orchestrator.run(
            chat.id,
            prompt,
            OrchestrationMode.MULTI,
            chat.role_ids,
        ),
    )

    assert call_count["interview"] == 1
    session = await load_grill_session(chat.id)
    assert session is not None
    assert len(session.answers) == 0
    messages = await conversation_store.list_messages(chat.id)
    grill_questions = [
        message
        for message in messages
        if message.role == MessageRole.ASSISTANT and "**Grill question" in message.content
    ]
    assert len(grill_questions) == 1


@pytest.mark.asyncio
async def test_grill_mode_pending_message_records_custom_answer(
    tmp_path,
    monkeypatch,
) -> None:
    """A chat reply while a grill question is pending is stored as the custom answer."""
    from agentforge.config import settings

    questions = [
        {
            "status": "question",
            "question": "Which PHP version?",
            "recommended_answer": "PHP 8.4",
            "why": "Compatibility",
        },
        {
            "status": "question",
            "question": "Which mail transport?",
            "recommended_answer": "SMTP",
            "why": "Delivery",
        },
    ]

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        index = min(len(_session.answers), len(questions) - 1)
        return questions[index]

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(
        AgentOrchestrator,
        "_generate_grill_interview_step",
        fake_interview_step,
    )
    await conversation_store.initialize()

    workspace = tmp_path / "workspace"
    chat = await create_test_chat(mode="grill", title="Grill pending answer test")
    orchestrator = AgentOrchestrator()
    first = await orchestrator.run(
        chat.id,
        "Create a PHP email program",
        OrchestrationMode.GRILL,
        chat.role_ids,
    )
    pending_id = first.pending_approvals[0].id

    second = await orchestrator.run(
        chat.id,
        "Use PHP 8.4 with Composer",
        OrchestrationMode.GRILL,
        chat.role_ids,
    )

    session = await load_grill_session(first.chat_id)
    assert session is not None
    assert len(session.answers) == 1
    assert session.answers[0].answer == "Use PHP 8.4 with Composer"
    assert second.pending_approvals
    assert second.pending_approvals[0].id != pending_id


@pytest.mark.asyncio
async def test_grill_accept_recommended_creates_next_question_pending(
    tmp_path,
    monkeypatch,
) -> None:
    """Answering a grill question via approval must expose the next pending dialog."""
    from agentforge.config import settings

    questions = [
        {
            "status": "question",
            "question": "Which PHP version?",
            "recommended_answer": "PHP 8.4",
            "why": "Compatibility",
        },
        {
            "status": "question",
            "question": "Which mail transport?",
            "recommended_answer": "SMTP",
            "why": "Delivery",
        },
    ]

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        index = min(len(_session.answers), len(questions) - 1)
        return questions[index]

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(
        AgentOrchestrator,
        "_generate_grill_interview_step",
        fake_interview_step,
    )
    await conversation_store.initialize()

    chat = await create_test_chat(
        mode="multi",
        title="Grill accept recommended",
        grill_enabled=True,
    )
    orchestrator = AgentOrchestrator()
    first = await orchestrator.run(
        chat.id,
        "Create a PHP email program",
        OrchestrationMode.MULTI,
        chat.role_ids,
    )
    pending_id = first.pending_approvals[0].id

    message = await orchestrator.execute_approved_command(
        chat.id,
        pending_id,
        ApprovalResponse(approved=True, choice_id="accept_recommended"),
    )

    assert message is not None
    pending = approval_manager.list_pending(chat.id)
    assert len(pending) == 1
    assert pending[0].action_type == "user_choice"
    assert pending[0].payload.get("kind") == "grill_question"
    assert pending[0].id != pending_id
    session = await load_grill_session(chat.id)
    assert session is not None
    assert len(session.answers) == 1
    assert session.answers[0].answer == "PHP 8.4"


@pytest.mark.asyncio
async def test_grill_execute_phase_only_starts_after_plan_approval(
    tmp_path,
    monkeypatch,
) -> None:
    """Execution must not run until the user explicitly approves the generated plan."""
    from agentforge.config import settings

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        return {"status": "complete", "summary": "Requirements are clear."}

    async def fake_plan(_self, _session: GrillSession) -> str:
        return "# Plan\n- Create index.php"

    execute_calls = {"count": 0}

    async def fake_execute_phase(
        self,
        chat_id: str,
        session: GrillSession,
        role_ids: list[str],
        on_event,
        intervention_queue,
    ):
        execute_calls["count"] += 1
        raise AssertionError("Execute phase must not run before plan approval")

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(AgentOrchestrator, "_generate_grill_interview_step", fake_interview_step)
    monkeypatch.setattr(AgentOrchestrator, "_generate_grill_plan", fake_plan)
    monkeypatch.setattr(AgentOrchestrator, "_run_grill_execute_phase", fake_execute_phase)
    await conversation_store.initialize()

    workspace = tmp_path / "workspace"
    chat = await conversation_store.create_chat(
        ChatCreate(title="Grill", mode="grill", role_ids=["developer"]),
    )
    orchestrator = AgentOrchestrator()
    result = await orchestrator.run(
        chat.id,
        "Create a PHP email program",
        OrchestrationMode.GRILL,
        ["developer"],
    )

    assert execute_calls["count"] == 0
    assert result.pending_approvals
    assert result.pending_approvals[0].payload.get("kind") == "grill_plan_review"
    session = await load_grill_session(chat.id)
    assert session is not None
    assert session.phase == GrillPhase.PLAN


@pytest.mark.asyncio
async def test_multi_mode_with_grill_enabled_starts_grill(
    tmp_path,
    monkeypatch,
) -> None:
    """Multi-agent chats can opt into grill via grill_enabled without legacy grill mode."""
    from agentforge.config import settings

    events: list[dict[str, Any]] = []

    async def capture_event(event: dict[str, Any]) -> None:
        events.append(event)

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        return {
            "status": "question",
            "question": "Which framework?",
            "recommended_answer": "React",
            "why": "Frontend stack",
        }

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(
        AgentOrchestrator,
        "_generate_grill_interview_step",
        fake_interview_step,
    )
    await conversation_store.initialize()

    chat = await conversation_store.create_chat(
        ChatCreate(
            title="Multi grill",
            mode="multi",
            grill_enabled=True,
            role_ids=["developer", "reviewer"],
        ),
    )
    orchestrator = AgentOrchestrator()
    result = await orchestrator.run(
        chat.id,
        "Build a dashboard",
        OrchestrationMode.MULTI,
        chat.role_ids,
        on_event=capture_event,
    )

    assert chat.grill_enabled is True
    assert any(event.get("type") == "grill_phase_updated" for event in events)
    assert not any(event.get("type") == "task_board_updated" for event in events)
    assert result.pending_approvals
    assert result.pending_approvals[0].action_type == "user_choice"


@pytest.mark.asyncio
async def test_grill_execute_phase_resolves_paths_from_session_idea(
    tmp_path,
    monkeypatch,
) -> None:
    """Grill execute uses the original idea and plan for workspace path resolution."""
    from agentforge.config import settings
    from agentforge.models.schemas import OrchestrationResponse

    captured: dict[str, object] = {}

    async def fake_multi(self, *args, **kwargs) -> OrchestrationResponse:
        captured["workspace_intent"] = kwargs.get("workspace_intent")
        captured["path_context"] = kwargs.get("path_context")
        return OrchestrationResponse(
            chat_id=args[0],
            messages=[],
            agent_discussions=[],
            pending_approvals=[],
        )

    async def fake_single(self, *args, **kwargs) -> OrchestrationResponse:
        return await fake_multi(self, *args, **kwargs)

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(AgentOrchestrator, "_run_single", fake_single)
    monkeypatch.setattr(AgentOrchestrator, "_run_multi", fake_multi)
    await conversation_store.initialize()

    chat = await conversation_store.create_chat(
        ChatCreate(
            title="Grill execute path resolution",
            mode="multi",
            grill_enabled=True,
            role_ids=["developer", "software_tester"],
        ),
    )
    session = GrillSession(
        chat_id=chat.id,
        phase=GrillPhase.PLAN,
        idea="Create PHP email sender at /home/joruf/GitHub/emailsender",
        plan_markdown="# Plan\n- Create SimpleEmailSender.php",
        role_ids=["developer", "software_tester"],
    )
    await persist_grill_session(session)

    orchestrator = AgentOrchestrator()
    await orchestrator._run_grill_execute_phase(
        chat.id,
        session,
        ["developer", "software_tester"],
        None,
        None,
    )

    intent = captured.get("workspace_intent")
    assert intent is not None
    assert "GitHub/emailsender" in intent.target_dirs
    assert "GitHub/emailsender/SimpleEmailSender.php" in intent.target_paths
    assert "path_context" in captured


@pytest.mark.asyncio
async def test_grill_execute_phase_uses_single_for_single_mode_chat(
    tmp_path,
    monkeypatch,
) -> None:
    """Single-agent grill chats execute through the single-agent orchestration path."""
    from agentforge.config import settings
    from agentforge.models.schemas import OrchestrationResponse

    single_calls = {"count": 0}
    multi_calls = {"count": 0}

    async def fake_single(self, *args, **kwargs) -> OrchestrationResponse:
        single_calls["count"] += 1
        return OrchestrationResponse(
            chat_id=args[0],
            messages=[],
            agent_discussions=[],
            pending_approvals=[],
        )

    async def fake_multi(self, *args, **kwargs) -> OrchestrationResponse:
        multi_calls["count"] += 1
        return OrchestrationResponse(
            chat_id=args[0],
            messages=[],
            agent_discussions=[],
            pending_approvals=[],
        )

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(AgentOrchestrator, "_run_single", fake_single)
    monkeypatch.setattr(AgentOrchestrator, "_run_multi", fake_multi)
    await conversation_store.initialize()

    chat = await conversation_store.create_chat(
        ChatCreate(
            title="Single grill execute",
            mode="single",
            grill_enabled=True,
            role_ids=["developer"],
        ),
    )
    session = GrillSession(
        chat_id=chat.id,
        phase=GrillPhase.PLAN,
        idea="Build CLI tool",
        plan_markdown="# Plan\n- Create main.py",
        role_ids=["developer"],
    )
    await persist_grill_session(session)

    orchestrator = AgentOrchestrator()
    await orchestrator._run_grill_execute_phase(
        chat.id,
        session,
        ["developer"],
        None,
        None,
    )

    assert single_calls["count"] == 1
    assert multi_calls["count"] == 0

    loaded = await load_grill_session(chat.id)
    assert loaded is not None
    assert loaded.phase == GrillPhase.DONE


@pytest.mark.asyncio
async def test_grill_execute_phase_skips_test_without_software_tester(
    tmp_path,
    monkeypatch,
) -> None:
    """Build phase skips the test phase when Software Tester is not selected."""
    from agentforge.config import settings
    from agentforge.models.schemas import OrchestrationResponse

    phases: list[str] = []

    async def capture_event(event: dict[str, Any]) -> None:
        if event.get("type") == "grill_phase_updated":
            phases.append(str(event.get("phase")))

    async def fake_single(self, *args, **kwargs) -> OrchestrationResponse:
        return OrchestrationResponse(
            chat_id=args[0],
            messages=[],
            agent_discussions=[],
            pending_approvals=[],
        )

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(AgentOrchestrator, "_run_single", fake_single)
    monkeypatch.setattr(AgentOrchestrator, "_run_multi", fake_single)
    await conversation_store.initialize()

    chat = await conversation_store.create_chat(
        ChatCreate(
            title="Grill without tester",
            mode="multi",
            grill_enabled=True,
            role_ids=["developer", "reviewer", "project_manager"],
        ),
    )
    session = GrillSession(
        chat_id=chat.id,
        phase=GrillPhase.PLAN,
        idea="Build CLI tool",
        plan_markdown="# Plan\n- Create main.py",
        role_ids=["developer", "reviewer", "project_manager"],
    )
    await persist_grill_session(session)

    orchestrator = AgentOrchestrator()
    await orchestrator._run_grill_execute_phase(
        chat.id,
        session,
        chat.role_ids,
        capture_event,
        None,
    )

    loaded = await load_grill_session(chat.id)
    assert loaded is not None
    assert loaded.phase == GrillPhase.DONE
    assert "execute" in phases
    assert "test" not in phases


@pytest.mark.asyncio
async def test_grill_execute_phase_emits_test_phase_before_done(
    tmp_path,
    monkeypatch,
) -> None:
    """Build phase is followed by a test phase before grill marks done."""
    from agentforge.config import settings
    from agentforge.models.schemas import OrchestrationResponse

    phases: list[str] = []

    async def capture_event(event: dict[str, Any]) -> None:
        if event.get("type") == "grill_phase_updated":
            phases.append(str(event.get("phase")))

    async def fake_single(self, *args, **kwargs) -> OrchestrationResponse:
        return OrchestrationResponse(
            chat_id=args[0],
            messages=[],
            agent_discussions=[],
            pending_approvals=[],
        )

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(AgentOrchestrator, "_run_single", fake_single)
    monkeypatch.setattr(AgentOrchestrator, "_run_multi", fake_single)
    await conversation_store.initialize()

    chat = await conversation_store.create_chat(
        ChatCreate(
            title="Grill test phase",
            mode="multi",
            grill_enabled=True,
            role_ids=["developer", "software_tester"],
        ),
    )
    session = GrillSession(
        chat_id=chat.id,
        phase=GrillPhase.PLAN,
        idea="Build CLI tool",
        plan_markdown="# Plan\n- Create main.py",
        role_ids=["developer", "software_tester"],
    )
    await persist_grill_session(session)

    orchestrator = AgentOrchestrator()
    await orchestrator._run_grill_execute_phase(
        chat.id,
        session,
        chat.role_ids,
        capture_event,
        None,
    )

    loaded = await load_grill_session(chat.id)
    assert loaded is not None
    assert loaded.phase == GrillPhase.DONE
    assert "execute" in phases
    assert "test" in phases
    assert phases.index("test") > phases.index("execute")


@pytest.mark.asyncio
async def test_grill_plan_approval_returns_before_execute_finishes(
    tmp_path,
    monkeypatch,
) -> None:
    """Plan approval ack returns before the execute phase completes."""
    import asyncio
    import time

    from agentforge.agents.orchestrator_mixins.grill import _grill_execute_tasks
    from agentforge.config import settings

    execute_started = asyncio.Event()
    execute_gate = asyncio.Event()

    async def slow_execute_phase(
        self,
        chat_id: str,
        session: GrillSession,
        role_ids: list[str],
        on_event,
        intervention_queue,
    ):
        execute_started.set()
        await execute_gate.wait()
        session.phase = GrillPhase.DONE
        await persist_grill_session(session)
        from agentforge.models.schemas import OrchestrationResponse

        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[],
            agent_discussions=[],
            pending_approvals=[],
        )

    async def fake_interview_step(_self, _session: GrillSession) -> dict[str, Any]:
        return {"status": "complete", "summary": "Requirements are clear."}

    async def fake_plan(_self, _session: GrillSession) -> str:
        return "# Plan\n- Create index.php"

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    patch_chat_ready(monkeypatch)
    monkeypatch.setattr(AgentOrchestrator, "_generate_grill_interview_step", fake_interview_step)
    monkeypatch.setattr(AgentOrchestrator, "_generate_grill_plan", fake_plan)
    monkeypatch.setattr(AgentOrchestrator, "_run_grill_execute_phase", slow_execute_phase)
    await conversation_store.initialize()

    chat = await create_test_chat(mode="grill", title="Grill plan approval timing")
    orchestrator = AgentOrchestrator()
    result = await orchestrator.run(
        chat.id,
        "Create a PHP email program",
        OrchestrationMode.GRILL,
        chat.role_ids,
    )
    pending_id = result.pending_approvals[0].id
    assert result.pending_approvals[0].payload.get("kind") == "grill_plan_review"

    events: list[dict[str, Any]] = []

    async def capture_event(event: dict[str, Any]) -> None:
        events.append(event)

    start = time.monotonic()
    message = await orchestrator.execute_approved_command(
        chat.id,
        pending_id,
        ApprovalResponse(approved=True, choice_id="approve_plan"),
        on_event=capture_event,
    )
    elapsed = time.monotonic() - start

    assert message is not None
    assert "building and testing" in message.content.lower()
    assert elapsed < 1.0
    await asyncio.wait_for(execute_started.wait(), timeout=2.0)

    task = _grill_execute_tasks.get(chat.id)
    assert task is not None
    execute_gate.set()
    await asyncio.wait_for(task, timeout=5.0)
    assert any(event.get("type") == "grill_execute_complete" for event in events)
