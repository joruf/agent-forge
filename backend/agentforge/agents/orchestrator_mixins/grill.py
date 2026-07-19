"""Orchestrator mixin for Grill Mode (Idea → Clarify → Plan → Execute → Test)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.grill_mode import (
    GRILL_INTERVIEW_SYSTEM,
    GRILL_PLAN_SYSTEM,
    GrillAnswer,
    GrillPhase,
    GrillSession,
    build_grill_execution_prompt,
    build_grill_test_prompt,
    build_grill_ui_payload,
    format_grill_context_block,
    fallback_grill_interview_step,
    grill_question_already_asked,
    load_grill_session,
    normalize_grill_question,
    parse_grill_interview_response,
    persist_grill_session,
    MAX_GRILL_QUESTIONS,
)
from agentforge.agents.role_registry import role_registry
from agentforge.agents.user_clarification import (
    ClarificationKind,
    request_clarification,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.agents.workspace_path_resolver import (
    activate_path_resolution_context,
    build_path_resolution_context,
    deactivate_path_resolution_context,
)
from agentforge.agents.workspace_scanner import build_workspace_path_context
from agentforge.models.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    ExecutionStrategy,
    GrillResumeState,
    MessageRole,
    MessageResponse,
    OrchestrationResponse,
    UserChoiceOption,
)
from agentforge.storage.conversation_store import conversation_store

_grill_chat_locks: dict[str, asyncio.Lock] = {}
_grill_execute_tasks: dict[str, asyncio.Task] = {}


def _grill_chat_lock(chat_id: str) -> asyncio.Lock:
    """
    Return a per-chat asyncio lock so concurrent grill runs cannot double-start.

    :param chat_id: Chat session ID
    :return: Lock instance for the chat
    """
    lock = _grill_chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _grill_chat_locks[chat_id] = lock
    return lock


class GrillMixin:
    """Mixin for grill-mode orchestration."""

    def _find_pending_grill_approval(self, chat_id: str) -> ApprovalRequest | None:
        """
        Return the active grill clarification or plan-review approval, if any.

        :param chat_id: Chat session ID
        :return: Pending user-choice approval or None
        """
        for approval in approval_manager.list_pending(chat_id):
            if approval.action_type != "user_choice":
                continue
            kind = str(approval.payload.get("kind") or "")
            if kind in {
                ClarificationKind.GRILL_QUESTION.value,
                ClarificationKind.GRILL_PLAN_REVIEW.value,
            }:
                return approval
        return None

    async def _clear_stale_grill_approvals(self, chat_id: str) -> None:
        """
        Remove superseded grill clarification approvals for one chat.

        :param chat_id: Chat session ID
        """
        for approval in list(approval_manager.list_pending(chat_id)):
            if approval.action_type != "user_choice":
                continue
            kind = str(approval.payload.get("kind") or "")
            if kind != ClarificationKind.GRILL_QUESTION.value:
                continue
            await approval_manager.respond(
                approval.id,
                ApprovalResponse(approved=False),
            )
            approval_manager.pop_resume_state(approval.id)

    async def _should_treat_message_as_grill_answer(
        self,
        chat_id: str,
        user_content: str,
        session: GrillSession,
    ) -> bool:
        """
        Return True when a chat turn is a new reply to the pending grill question.

        Duplicate orchestration of the original idea (e.g. double WebSocket submit)
        must not be recorded as a clarification answer.

        :param chat_id: Chat session ID
        :param user_content: Current user message text
        :param session: Active grill session
        :return: True when the message should resume the pending grill dialog
        """
        normalized_content = user_content.strip()
        normalized_idea = session.idea.strip()
        if normalized_content and normalized_content == normalized_idea:
            return False

        messages = await conversation_store.list_messages(chat_id)
        user_messages = [message for message in messages if message.role == MessageRole.USER]
        last_grill_question_at: datetime | None = None
        for message in reversed(messages):
            if message.role != MessageRole.ASSISTANT:
                continue
            metadata = message.metadata or {}
            if metadata.get("grill_question"):
                last_grill_question_at = message.created_at
                break

        if last_grill_question_at is None or not user_messages:
            return False

        users_after_question = [
            message
            for message in user_messages
            if message.created_at > last_grill_question_at
        ]
        if not users_after_question:
            return False

        latest_reply = users_after_question[-1]
        first_user = user_messages[0]
        if latest_reply.content.strip() == first_user.content.strip():
            return False

        return latest_reply.content.strip() == normalized_content

    def _grill_idempotent_wait_response(
        self,
        chat_id: str,
        effective_strategy: ExecutionStrategy,
    ) -> OrchestrationResponse:
        """
        Return a no-op orchestration result while a grill dialog is already pending.

        :param chat_id: Chat session ID
        :param effective_strategy: Effective execution strategy
        :return: Response that preserves pending approvals without advancing grill
        """
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[],
            agent_discussions=[],
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
        )

    async def _continue_grill_from_pending_message(
        self,
        chat_id: str,
        approval: ApprovalRequest,
        user_content: str,
        role_ids: list[str],
        effective_strategy: ExecutionStrategy,
        on_event: Callable | None,
    ) -> OrchestrationResponse:
        """
        Treat a chat message as the custom reply for a pending grill dialog.

        :param chat_id: Chat session ID
        :param approval: Pending grill user-choice approval
        :param user_content: User message text
        :param role_ids: Selected role IDs for later execution
        :param effective_strategy: Effective execution strategy
        :param on_event: Optional WebSocket callback
        :return: Orchestration response after resuming grill mode
        """
        try:
            resume_state = approval_manager.pop_resume_state(approval.id)
        except ValueError:
            assistant = await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                (
                    "Grill mode could not resume because the pending question state "
                    "is invalid. Please use the choice dialog above."
                ),
                metadata={"grill_phase": GrillPhase.CLARIFY.value, "resume_error": True},
            )
            return OrchestrationResponse(
                chat_id=chat_id,
                messages=[assistant],
                agent_discussions=[],
                pending_approvals=approval_manager.list_pending(chat_id),
                effective_execution_strategy=effective_strategy,
            )
        if not isinstance(resume_state, GrillResumeState):
            assistant = await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                "Grill mode could not resume from the pending question.",
                metadata={"resume_error": True},
            )
            return OrchestrationResponse(
                chat_id=chat_id,
                messages=[assistant],
                agent_discussions=[],
                pending_approvals=approval_manager.list_pending(chat_id),
                effective_execution_strategy=effective_strategy,
            )

        response = ApprovalResponse(
            approved=True,
            choice_id="custom_reply",
            comment=user_content.strip(),
        )
        await approval_manager.respond(approval.id, response)
        message = await self._resume_grill_after_choice(
            resume_state,
            response,
            role_ids,
            on_event,
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[message],
            agent_discussions=[],
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
        )

    async def _respond_grill_waiting_for_choice(
        self,
        chat_id: str,
        session: GrillSession,
        effective_strategy: ExecutionStrategy,
    ) -> OrchestrationResponse:
        """
        Tell the user to answer via the choice dialog instead of a new chat turn.

        :param chat_id: Chat session ID
        :param session: Active grill session
        :param effective_strategy: Effective execution strategy
        :return: Orchestration response without advancing the workflow
        """
        phase_label = "clarification question" if session.phase == GrillPhase.CLARIFY else "plan review"
        assistant = await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            (
                f"Grill mode is waiting for your {phase_label}. "
                "Use the choice dialog to accept the recommendation or provide a custom answer."
            ),
            metadata={"grill_phase": session.phase.value, "grill_waiting_for_choice": True},
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[assistant],
            agent_discussions=[],
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
        )

    async def _emit_grill_phase(
        self,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
        session: GrillSession,
    ) -> None:
        """
        Push grill phase updates to connected clients.

        :param on_event: Optional WebSocket callback
        :param session: Active grill session
        """
        if on_event is None:
            return
        await on_event(build_grill_ui_payload(session))

    async def _generate_grill_interview_step(
        self,
        session: GrillSession,
    ) -> dict[str, Any]:
        """
        Ask the LLM for the next grill question or completion signal.

        Retries when the model repeats an already asked question.

        :param session: Active grill session
        :return: Parsed interview payload
        """
        avoid_questions: list[str] = []
        for _attempt in range(3):
            step = await self._generate_grill_interview_step_once(
                session,
                avoid_questions=avoid_questions,
            )
            if step.get("status") == "complete":
                return step
            question = str(step.get("question") or "").strip()
            if not question:
                continue
            if grill_question_already_asked(session, question):
                avoid_questions.append(question)
                continue
            return step

        if len(session.answers) >= 3:
            return {
                "status": "complete",
                "summary": "Proceeding to planning with collected clarifications.",
            }
        return fallback_grill_interview_step(session)

    async def _generate_grill_interview_step_once(
        self,
        session: GrillSession,
        avoid_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Perform one LLM call for the next grill interview step.

        :param session: Active grill session
        :param avoid_questions: Questions that must not be repeated
        :return: Parsed or fallback interview payload
        """
        llm, _routing = await self._resolve_llm(
            session.idea,
            role_id="project_manager",
            mode_single=True,
        )
        avoid_block = ""
        if avoid_questions:
            avoid_lines = "\n".join(f"- {question}" for question in avoid_questions if question.strip())
            avoid_block = (
                "\n\nThe following candidate questions were already asked and are forbidden:\n"
                f"{avoid_lines}\n"
                "Choose a different topic or return status complete."
            )
        messages = [
            {"role": "system", "content": GRILL_INTERVIEW_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{format_grill_context_block(session)}\n\n"
                    f"Questions answered so far: {len(session.answers)} / {MAX_GRILL_QUESTIONS}."
                    f"{avoid_block}"
                ),
            },
        ]
        result = await llm.complete(messages, max_tokens=1200)
        parsed = parse_grill_interview_response(result.get("content") or "")
        if parsed:
            return parsed
        return fallback_grill_interview_step(session)

    async def _generate_grill_plan(self, session: GrillSession) -> str:
        """
        Generate an implementation plan markdown document.

        :param session: Grill session with completed clarifications
        :return: Plan markdown text
        """
        llm, _routing = await self._resolve_llm(session.idea, role_id="architect", mode_single=True)
        messages = [
            {"role": "system", "content": GRILL_PLAN_SYSTEM},
            {"role": "user", "content": format_grill_context_block(session)},
        ]
        result = await llm.complete(messages, max_tokens=2500)
        plan = (result.get("content") or "").strip()
        return plan or (
            "# Implementation plan\n\n"
            "- Review the clarified requirements.\n"
            "- Implement the requested feature in the workspace.\n"
            "- Verify acceptance criteria manually."
        )

    async def _request_grill_question(
        self,
        chat_id: str,
        session: GrillSession,
        question: str,
        recommended_answer: str,
        why: str,
        on_event: Callable | None,
    ) -> None:
        """
        Pause grill mode and ask the user one clarification question.

        :param chat_id: Chat session ID
        :param session: Active grill session
        :param question: Question text
        :param recommended_answer: Suggested answer
        :param why: Short rationale shown in the dialog
        :param on_event: Optional WebSocket callback
        """
        await self._clear_stale_grill_approvals(chat_id)
        options = [
            UserChoiceOption(
                id="accept_recommended",
                label="Accept recommended answer",
                description=recommended_answer,
            ),
            UserChoiceOption(
                id="custom_reply",
                label="Provide a different answer",
                description="Type your own clarification.",
            ),
            UserChoiceOption(
                id="abort",
                label="Abort grill session",
                description="Stop planning and return to idle.",
            ),
        ]
        description = question.strip()
        if why.strip():
            description = f"{description}\n\nWhy this matters: {why.strip()}"
        resume_state = GrillResumeState(
            chat_id=chat_id,
            phase=GrillPhase.CLARIFY.value,
            session_snapshot=session.to_dict(),
            pending_question=question,
            recommended_answer=recommended_answer,
        )
        await request_clarification(
            chat_id,
            ClarificationKind.GRILL_QUESTION,
            description,
            options,
            resume_state,
            on_event,
            allows_custom_input=True,
        )

    async def _request_grill_plan_review(
        self,
        chat_id: str,
        session: GrillSession,
        on_event: Callable | None,
    ) -> None:
        """
        Pause grill mode for plan approval before execution.

        :param chat_id: Chat session ID
        :param session: Grill session with generated plan
        :param on_event: Optional WebSocket callback
        """
        options = [
            UserChoiceOption(
                id="approve_plan",
                label="Approve plan and build",
                description="Start implementation using this plan.",
            ),
            UserChoiceOption(
                id="custom_reply",
                label="Request plan changes",
                description="Describe what to change in the plan.",
            ),
            UserChoiceOption(
                id="abort",
                label="Abort",
                description="Do not execute the plan.",
            ),
        ]
        resume_state = GrillResumeState(
            chat_id=chat_id,
            phase=GrillPhase.PLAN.value,
            session_snapshot=session.to_dict(),
            pending_question="",
            recommended_answer="",
        )
        question = (
            "Review the generated implementation plan. Approve it to start building, "
            "or request changes."
        )
        await request_clarification(
            chat_id,
            ClarificationKind.GRILL_PLAN_REVIEW,
            question,
            options,
            resume_state,
            on_event,
            allows_custom_input=True,
        )

    async def _begin_grill_clarification(
        self,
        chat_id: str,
        session: GrillSession,
        on_event: Callable | None,
    ) -> OrchestrationResponse:
        """
        Start or continue the clarification interview.

        :param chat_id: Chat session ID
        :param session: Active grill session
        :param on_event: Optional WebSocket callback
        :return: Partial orchestration response waiting for user input
        """
        if self._find_pending_grill_approval(chat_id) is not None:
            return OrchestrationResponse(
                chat_id=chat_id,
                messages=[],
                agent_discussions=[],
                pending_approvals=approval_manager.list_pending(chat_id),
                effective_execution_strategy=ExecutionStrategy.SERIAL,
            )

        session.phase = GrillPhase.CLARIFY
        await persist_grill_session(session)
        await self._emit_grill_phase(on_event, session)

        if len(session.answers) >= MAX_GRILL_QUESTIONS:
            session.summary = (
                "Maximum number of clarification questions reached. "
                "Proceeding to planning with collected answers."
            )
            return await self._begin_grill_planning(chat_id, session, on_event)

        step = await self._generate_grill_interview_step(session)
        if step.get("status") == "complete":
            session.summary = str(step.get("summary") or "").strip()
            return await self._begin_grill_planning(chat_id, session, on_event)

        question = str(step.get("question") or "").strip()
        recommended = str(step.get("recommended_answer") or "").strip()
        why = str(step.get("why") or step.get("rationale") or "").strip()
        if not question or grill_question_already_asked(session, question):
            session.summary = "Clarification complete; proceeding to planning."
            return await self._begin_grill_planning(chat_id, session, on_event)
        if not recommended:
            recommended = "A working first version that matches the core user need."

        await self._request_grill_question(
            chat_id,
            session,
            question,
            recommended,
            why,
            on_event,
        )
        assistant = await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            f"**Grill question {len(session.answers) + 1}:** {question}",
            metadata={"grill_phase": GrillPhase.CLARIFY.value, "grill_question": question},
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[assistant],
            agent_discussions=[],
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=ExecutionStrategy.SERIAL,
        )

    async def _begin_grill_planning(
        self,
        chat_id: str,
        session: GrillSession,
        on_event: Callable | None,
    ) -> OrchestrationResponse:
        """
        Generate a plan and request user approval.

        :param chat_id: Chat session ID
        :param session: Active grill session
        :param on_event: Optional WebSocket callback
        :return: Partial orchestration response waiting for plan approval
        """
        session.phase = GrillPhase.PLAN
        session.plan_markdown = await self._generate_grill_plan(session)
        await persist_grill_session(session)
        await self._emit_grill_phase(on_event, session)
        await self._request_grill_plan_review(chat_id, session, on_event)

        assistant = await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            session.plan_markdown,
            metadata={
                "grill_phase": GrillPhase.PLAN.value,
                "grill_plan": True,
            },
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[assistant],
            agent_discussions=[],
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=ExecutionStrategy.SERIAL,
        )

    async def _load_memory_context(self, chat_id: str) -> str:
        """
        Load chat memory context for nested orchestration calls.

        :param chat_id: Chat session ID
        :return: Memory context string
        """
        from agentforge.memory.store import memory_store

        chat = await conversation_store.get_chat(chat_id)
        return await memory_store.get_context(chat_id, chat.memory)

    async def _run_grill_execute_phase(
        self,
        chat_id: str,
        session: GrillSession,
        role_ids: list[str],
        on_event: Callable | None,
        intervention_queue,
    ) -> OrchestrationResponse:
        """
        Execute the approved plan using the standard multi-agent pipeline.

        :param chat_id: Chat session ID
        :param session: Approved grill session
        :param role_ids: Roles for implementation
        :param on_event: Optional WebSocket callback
        :param intervention_queue: Optional live intervention queue
        :return: Orchestration result from execute phase
        """
        from agentforge.models.schemas import ChatUpdate
        from agentforge.agents.grill_mode import resolve_grill_execution_mode

        session.phase = GrillPhase.EXECUTE
        await persist_grill_session(session)
        await self._emit_grill_phase(on_event, session)

        execution_prompt = build_grill_execution_prompt(session)
        from agentforge.agents.task_state import (
            build_task_state,
            emit_task_board_update,
            load_task_board_memory,
            persist_task_board,
        )

        path_source = "\n".join(
            part.strip()
            for part in (session.idea, session.summary, session.plan_markdown)
            if part and part.strip()
        )
        authoritative_path_source = (session.idea or "").strip() or path_source or execution_prompt
        workspace_intent = detect_workspace_intent(authoritative_path_source)
        prior_board = await load_task_board_memory(chat_id)
        task_state = build_task_state(
            execution_prompt,
            workspace_intent,
            prior_board,
            interpreted_request=authoritative_path_source,
        )
        path_resolution = build_path_resolution_context(
            authoritative_path_source,
            workspace_intent,
        )
        path_context_token = activate_path_resolution_context(path_resolution)
        execute_roles = role_ids or session.role_ids or [
            "developer",
            "reviewer",
            "project_manager",
        ]
        run_test_phase = "software_tester" in execute_roles
        build_roles = [
            role for role in execute_roles if role != "software_tester"
        ]
        if not build_roles:
            build_roles = ["developer"]
        try:
            path_context = await build_workspace_path_context(workspace_intent)
            await emit_task_board_update(on_event, task_state)
            chat = await conversation_store.get_chat(chat_id)
            execution_mode = resolve_grill_execution_mode(chat.mode, build_roles)
            memory_context = await self._load_memory_context(chat_id)
            tools = self._build_tools(chat_id, chat.memory.memory_scope)

            if execution_mode == "single":
                result = await self._run_single(
                    chat_id,
                    execution_prompt,
                    build_roles,
                    memory_context,
                    tools,
                    chat.memory.memory_scope,
                    ExecutionStrategy.SERIAL,
                    on_event,
                    intervention_queue,
                    workspace_intent=workspace_intent,
                    path_context=path_context,
                    task_state=task_state,
                    prefetched_reads={},
                    prompt_normalization=None,
                )
                if result.resolved_role_id:
                    await conversation_store.update_chat(
                        chat_id,
                        ChatUpdate(role_ids=[result.resolved_role_id]),
                    )
            else:
                pm = role_registry.get_role("project_manager")
                roles = [
                    role.id
                    for role in role_registry.get_roles(build_roles)
                    if role is not None
                ]
                if pm and pm.id not in roles:
                    roles = [pm.id, *roles]

                result = await self._run_multi(
                    chat_id,
                    execution_prompt,
                    roles,
                    memory_context,
                    tools,
                    chat.memory.memory_scope,
                    ExecutionStrategy.HYBRID,
                    on_event,
                    intervention_queue,
                    workspace_intent=workspace_intent,
                    path_context=path_context,
                    task_state=task_state,
                    prefetched_reads={},
                    prompt_normalization=None,
                )

            await persist_task_board(chat_id, task_state, on_event=on_event)
        finally:
            deactivate_path_resolution_context(path_context_token)

        if run_test_phase:
            return await self._run_grill_test_phase(
                chat_id,
                session,
                result,
                on_event,
                intervention_queue,
            )

        session.phase = GrillPhase.DONE
        await persist_grill_session(session)
        await self._emit_grill_phase(on_event, session)
        return result

    async def _run_grill_execute_background(
        self,
        chat_id: str,
        session: GrillSession,
        role_ids: list[str],
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """
        Run grill execute + test in the background after plan approval.

        :param chat_id: Chat session ID
        :param session: Approved grill session
        :param role_ids: Roles for implementation
        :param on_event: WebSocket callback for live updates
        """
        try:
            await self._run_grill_execute_phase(
                chat_id,
                session,
                role_ids,
                on_event,
                None,
            )
            if on_event is not None:
                await on_event(
                    {
                        "type": "grill_execute_complete",
                        "success": True,
                        "chat_id": chat_id,
                    },
                )
        except Exception as exc:
            error_session = await load_grill_session(chat_id)
            if error_session is None:
                error_session = GrillSession(chat_id=chat_id)
            error_session.phase = GrillPhase.DONE
            await persist_grill_session(error_session)
            await self._emit_grill_phase(on_event, error_session)
            await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                f"Grill execution failed: {exc}",
                metadata={"grill_phase": GrillPhase.DONE.value, "grill_error": True},
            )
            if on_event is not None:
                await on_event(
                    {
                        "type": "grill_execute_complete",
                        "success": False,
                        "chat_id": chat_id,
                        "error": str(exc),
                    },
                )
        finally:
            _grill_execute_tasks.pop(chat_id, None)

    async def _run_grill_test_phase(
        self,
        chat_id: str,
        session: GrillSession,
        build_result: OrchestrationResponse,
        on_event: Callable | None,
        intervention_queue,
    ) -> OrchestrationResponse:
        """
        Verify the build against the approved plan before marking grill complete.

        :param chat_id: Chat session ID
        :param session: Grill session after build
        :param build_result: Orchestration result from execute phase
        :param on_event: Optional WebSocket callback
        :param intervention_queue: Optional live intervention queue
        :return: Combined orchestration result including test verification
        """
        session.phase = GrillPhase.TEST
        await persist_grill_session(session)
        await self._emit_grill_phase(on_event, session)

        test_prompt = build_grill_test_prompt(session)
        chat = await conversation_store.get_chat(chat_id)
        memory_context = await self._load_memory_context(chat_id)
        tools = self._build_tools(chat_id, chat.memory.memory_scope)
        test_roles = ["software_tester"]

        test_result = await self._run_single(
            chat_id,
            test_prompt,
            test_roles,
            memory_context,
            tools,
            chat.memory.memory_scope,
            ExecutionStrategy.SERIAL,
            on_event,
            intervention_queue,
            workspace_intent=detect_workspace_intent(test_prompt),
            path_context="",
            task_state=None,
            prefetched_reads={},
            prompt_normalization=None,
        )

        session.phase = GrillPhase.DONE
        await persist_grill_session(session)
        await self._emit_grill_phase(on_event, session)

        combined_messages = [*build_result.messages, *test_result.messages]
        combined_discussions = [
            *build_result.agent_discussions,
            *test_result.agent_discussions,
        ]
        combined_approvals = [
            *build_result.pending_approvals,
            *test_result.pending_approvals,
        ]
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=combined_messages,
            agent_discussions=combined_discussions,
            pending_approvals=combined_approvals,
            effective_execution_strategy=build_result.effective_execution_strategy,
            resolved_role_id=test_result.resolved_role_id or build_result.resolved_role_id,
            title=build_result.title or test_result.title,
        )

    async def _run_grill(
        self,
        chat_id: str,
        user_content: str,
        role_ids: list[str],
        effective_strategy: ExecutionStrategy,
        on_event: Callable | None,
        intervention_queue,
    ) -> OrchestrationResponse:
        """
        Run grill-mode orchestration for the current user turn.

        :param chat_id: Chat session ID
        :param user_content: User message text (initial idea)
        :param role_ids: Selected role IDs for later execution
        :param effective_strategy: Effective execution strategy
        :param on_event: Optional WebSocket callback
        :param intervention_queue: Optional live intervention queue
        :return: Orchestration response
        """
        async with _grill_chat_lock(chat_id):
            return await self._run_grill_locked(
                chat_id,
                user_content,
                role_ids,
                effective_strategy,
                on_event,
                intervention_queue,
            )

    async def _run_grill_locked(
        self,
        chat_id: str,
        user_content: str,
        role_ids: list[str],
        effective_strategy: ExecutionStrategy,
        on_event: Callable | None,
        intervention_queue,
    ) -> OrchestrationResponse:
        """
        Run grill-mode orchestration while holding the per-chat grill lock.

        :param chat_id: Chat session ID
        :param user_content: User message text (initial idea)
        :param role_ids: Selected role IDs for later execution
        :param effective_strategy: Effective execution strategy
        :param on_event: Optional WebSocket callback
        :param intervention_queue: Optional live intervention queue
        :return: Orchestration response
        """
        session = await load_grill_session(chat_id)
        if session is None:
            session = GrillSession(chat_id=chat_id, idea=user_content.strip(), role_ids=list(role_ids))
            session.phase = GrillPhase.IDEA
            await persist_grill_session(session)
            await self._emit_grill_phase(on_event, session)
            intro = await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                (
                    "Grill mode started. I will ask clarifying questions one at a time "
                    "before planning, building, and testing."
                ),
                metadata={"grill_phase": GrillPhase.IDEA.value},
            )
            clarify_result = await self._begin_grill_clarification(chat_id, session, on_event)
            clarify_result.messages = [intro, *clarify_result.messages]
            return clarify_result

        if session.phase in {GrillPhase.DONE, GrillPhase.EXECUTE, GrillPhase.TEST}:
            assistant = await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                "This grill session is already complete. Start a new chat for a new idea.",
                metadata={"grill_phase": session.phase.value},
            )
            return OrchestrationResponse(
                chat_id=chat_id,
                messages=[assistant],
                agent_discussions=[],
                pending_approvals=approval_manager.list_pending(chat_id),
                effective_execution_strategy=effective_strategy,
            )

        pending = self._find_pending_grill_approval(chat_id)
        if pending is not None:
            if await self._should_treat_message_as_grill_answer(
                chat_id,
                user_content,
                session,
            ):
                return await self._continue_grill_from_pending_message(
                    chat_id,
                    pending,
                    user_content,
                    role_ids,
                    effective_strategy,
                    on_event,
                )
            return self._grill_idempotent_wait_response(chat_id, effective_strategy)

        if session.phase in {GrillPhase.CLARIFY, GrillPhase.PLAN}:
            return await self._respond_grill_waiting_for_choice(
                chat_id,
                session,
                effective_strategy,
            )

        session.idea = session.idea or user_content.strip()
        session.role_ids = list(role_ids or session.role_ids)
        return await self._begin_grill_clarification(chat_id, session, on_event)

    async def _resume_grill_after_choice(
        self,
        resume_state: GrillResumeState,
        response: ApprovalResponse,
        role_ids: list[str],
        on_event: Callable | None,
    ) -> MessageResponse:
        """
        Continue grill mode after a user-choice response.

        :param resume_state: Saved grill continuation state
        :param response: User approval response
        :param role_ids: Selected role IDs for execute phase
        :param on_event: Optional WebSocket callback
        :return: Assistant message summarizing the next grill step
        """
        session = await load_grill_session(resume_state.chat_id)
        if session is None:
            session = GrillSession.from_dict(resume_state.session_snapshot)
        session.chat_id = resume_state.chat_id
        choice_id = (response.choice_id or "").strip()
        if not response.approved or choice_id == "abort":
            session.phase = GrillPhase.DONE
            await persist_grill_session(session)
            await self._emit_grill_phase(on_event, session)
            return await conversation_store.add_message(
                resume_state.chat_id,
                MessageRole.ASSISTANT,
                "Grill session aborted.",
                metadata={"grill_phase": GrillPhase.DONE.value, "grill_aborted": True},
            )

        if resume_state.phase == GrillPhase.CLARIFY.value:
            if choice_id == "accept_recommended":
                answer = resume_state.recommended_answer
            elif choice_id == "custom_reply":
                answer = (response.comment or "").strip()
                if not answer:
                    return await conversation_store.add_message(
                        resume_state.chat_id,
                        MessageRole.ASSISTANT,
                        "No answer provided. Grill session was not resumed.",
                        metadata={"grill_phase": GrillPhase.CLARIFY.value, "resume_error": True},
                    )
            else:
                answer = (response.comment or resume_state.recommended_answer).strip()

            pending_question = resume_state.pending_question.strip()
            already_answered = any(
                normalize_grill_question(item.question) == normalize_grill_question(pending_question)
                for item in session.answers
            )
            if not already_answered:
                session.answers.append(
                    GrillAnswer(
                        question=pending_question,
                        recommended_answer=resume_state.recommended_answer,
                        answer=answer,
                    ),
                )
            await persist_grill_session(session)
            next_step = await self._begin_grill_clarification(
                resume_state.chat_id,
                session,
                on_event,
            )
            if next_step.messages:
                return next_step.messages[-1]
            return await conversation_store.add_message(
                resume_state.chat_id,
                MessageRole.ASSISTANT,
                "Continuing grill clarification.",
                metadata={"grill_phase": GrillPhase.CLARIFY.value},
            )

        if resume_state.phase == GrillPhase.PLAN.value:
            if choice_id == "approve_plan":
                if on_event is not None:
                    existing_task = _grill_execute_tasks.get(resume_state.chat_id)
                    if existing_task is not None and not existing_task.done():
                        return await conversation_store.add_message(
                            resume_state.chat_id,
                            MessageRole.ASSISTANT,
                            "Build already in progress.",
                            metadata={"grill_phase": GrillPhase.EXECUTE.value},
                        )

                    execute_session = await load_grill_session(resume_state.chat_id)
                    if execute_session is None:
                        execute_session = GrillSession.from_dict(resume_state.session_snapshot)
                        execute_session.chat_id = resume_state.chat_id

                    execute_session.phase = GrillPhase.EXECUTE
                    await persist_grill_session(execute_session)
                    await self._emit_grill_phase(on_event, execute_session)

                    task = asyncio.create_task(
                        self._run_grill_execute_background(
                            resume_state.chat_id,
                            execute_session,
                            role_ids,
                            on_event,
                        ),
                    )
                    _grill_execute_tasks[resume_state.chat_id] = task

                    return await conversation_store.add_message(
                        resume_state.chat_id,
                        MessageRole.ASSISTANT,
                        "Plan approved. Building and testing…",
                        metadata={
                            "grill_phase": GrillPhase.EXECUTE.value,
                            "grill_executing": True,
                        },
                    )

                execute_result = await self._run_grill_execute_phase(
                    resume_state.chat_id,
                    session,
                    role_ids,
                    on_event,
                    None,
                )
                if execute_result.messages:
                    last = execute_result.messages[-1]
                    metadata = dict(last.metadata or {})
                    metadata.update({"grill_phase": GrillPhase.DONE.value, "grill_executed": True})
                    last.metadata = metadata
                    return last
                return await conversation_store.add_message(
                    resume_state.chat_id,
                    MessageRole.ASSISTANT,
                    "Plan approved. Execution finished without a final message.",
                    metadata={"grill_phase": GrillPhase.DONE.value},
                )

            revision = (response.comment or "").strip()
            if not revision:
                return await conversation_store.add_message(
                    resume_state.chat_id,
                    MessageRole.ASSISTANT,
                    "No plan changes were provided.",
                    metadata={"grill_phase": GrillPhase.PLAN.value, "resume_error": True},
                )
            session.answers.append(
                GrillAnswer(
                    question="Plan revision request",
                    recommended_answer="",
                    answer=revision,
                ),
            )
            session.summary = f"{session.summary}\nPlan revision: {revision}".strip()
            plan_result = await self._begin_grill_planning(resume_state.chat_id, session, on_event)
            if plan_result.messages:
                return plan_result.messages[-1]
            return await conversation_store.add_message(
                resume_state.chat_id,
                MessageRole.ASSISTANT,
                "Updated plan ready for review.",
                metadata={"grill_phase": GrillPhase.PLAN.value},
            )

        return await conversation_store.add_message(
            resume_state.chat_id,
            MessageRole.ASSISTANT,
            "Unknown grill resume phase.",
            metadata={"resume_error": True},
        )
