"""Orchestrator mixin — unified user clarification pause/resume."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.task_state import TaskState, build_task_state
from agentforge.agents.user_clarification import (
    ClarificationKind,
    build_clarification_options,
    request_clarification,
)
from agentforge.agents.workspace_intent import WorkspaceIntent
from agentforge.models.schemas import (
    AgentMessage,
    AgentRole,
    ApprovalResponse,
    ExecutionStrategy,
    MessageRole,
    MessageResponse,
    OrchestrationMode,
    OrchestrationResponse,
    OrchestrationResumeState,
    UserChoiceOption,
)
from agentforge.storage.conversation_store import conversation_store


class ClarificationMixin:
    """Mixin for clarification pause/resume helpers."""

    async def _build_clarification_pause_response(
        self,
        chat_id: str,
        effective_strategy: ExecutionStrategy,
        outputs: list[MessageResponse] | None = None,
        discussions: list[AgentMessage] | None = None,
    ) -> OrchestrationResponse:
        """
        Return a partial orchestration response while a clarification dialog is open.

        :param chat_id: Chat session ID
        :param effective_strategy: Effective execution strategy for this run
        :param outputs: Output messages collected so far
        :param discussions: Agent discussions collected so far
        :return: Orchestration response with pending user-choice approval
        """
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=outputs or [],
            agent_discussions=discussions or [],
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
        )

    async def _request_agent_clarification(
        self,
        chat_id: str,
        kind: ClarificationKind,
        question: str,
        *,
        role: AgentRole | None,
        user_content: str,
        outputs: list[MessageResponse],
        discussions: list[AgentMessage],
        effective_strategy: ExecutionStrategy,
        task_state: TaskState | None = None,
        workspace_intent: WorkspaceIntent | None = None,
        on_event: Callable | None = None,
        context: dict[str, Any] | None = None,
        mode: OrchestrationMode = OrchestrationMode.MULTI,
        role_ids: list[str] | None = None,
    ) -> OrchestrationResponse:
        """
        Open a clarification dialog instead of a plain chat [ASK_USER] message.

        :param chat_id: Chat session ID
        :param kind: Clarification category
        :param question: User-facing question text
        :param role: Agent role that requested clarification
        :param user_content: Original user prompt
        :param outputs: Output messages collected so far
        :param discussions: Agent discussions collected so far
        :param effective_strategy: Effective execution strategy for this run
        :param task_state: Active task board, if any
        :param workspace_intent: Parsed workspace intent, if any
        :param on_event: Optional WebSocket event callback
        :param context: Extra clarification context
        :param mode: Orchestration mode for resume
        :param role_ids: Selected role IDs for resume
        :return: Orchestration response waiting for user choice
        """
        clarification_context = dict(context or {})
        if role is not None:
            clarification_context.setdefault("role_id", role.id)
            clarification_context.setdefault("role_name", role.name)

        resume_state = OrchestrationResumeState(
            kind=kind.value,
            chat_id=chat_id,
            user_content=user_content,
            context=clarification_context,
            task_state_snapshot=(
                task_state.to_persisted_payload() if task_state is not None else None
            ),
            intent=asdict(workspace_intent) if workspace_intent is not None else None,
            mode=mode.value,
            role_ids=list(role_ids or []),
            effective_strategy=effective_strategy.value,
            source_role_id=role.id if role is not None else "",
            source_role_name=role.name if role is not None else "",
            question_text=question,
        )
        options = build_clarification_options(kind, clarification_context)
        await request_clarification(
            chat_id,
            kind,
            question,
            options,
            resume_state,
            on_event,
        )
        return await self._build_clarification_pause_response(
            chat_id,
            effective_strategy,
            outputs=outputs,
            discussions=discussions,
        )

    async def _resume_orchestration_after_clarification(
        self,
        resume_state: OrchestrationResumeState,
        response: ApprovalResponse,
    ) -> MessageResponse:
        """
        Resume orchestration after a generic clarification dialog response.

        :param resume_state: Saved orchestration continuation state
        :param response: User approval response with choice_id and optional comment
        :return: Assistant message summarizing resumed work
        """
        choice_id = (response.choice_id or "").strip()
        if not response.approved or not choice_id:
            choice_id = "abort"

        metadata = {
            "kind": "clarification",
            "clarification_kind": resume_state.kind,
            "choice_id": choice_id,
            "resumed_from_approval": True,
        }

        if choice_id == "abort":
            return await conversation_store.add_message(
                resume_state.chat_id,
                MessageRole.ASSISTANT,
                "Stopped at your request.",
                metadata=metadata,
            )

        record_user_message = False
        follow_up_content = resume_state.user_content

        if choice_id == "custom_reply":
            comment = (response.comment or "").strip()
            if not comment:
                return await conversation_store.add_message(
                    resume_state.chat_id,
                    MessageRole.ASSISTANT,
                    "No answer was provided. The workflow was not resumed.",
                    metadata={**metadata, "resume_error": True},
                )
            follow_up_content = f"{resume_state.user_content}\n\nUser clarification: {comment}"
            record_user_message = True
        elif choice_id == "retry":
            follow_up_content = resume_state.user_content
        elif choice_id == "skip" and resume_state.kind == ClarificationKind.WORKFLOW_INCOMPLETE:
            reason = resume_state.context.get("reason", "incomplete workflow step")
            follow_up_content = (
                f"{resume_state.user_content}\n\n"
                f"User instruction: Skip the incomplete workflow step ({reason}) and continue."
            )
            record_user_message = True
        elif response.comment.strip():
            follow_up_content = (
                f"{resume_state.user_content}\n\nUser clarification: {response.comment.strip()}"
            )
            record_user_message = True

        if resume_state.task_state_snapshot and resume_state.intent:
            task_state = build_task_state(
                resume_state.user_content,
                WorkspaceIntent(**resume_state.intent),
                resume_state.task_state_snapshot,
            )
            if choice_id == "retry":
                task_state.weak_retry_counts.clear()

        mode = OrchestrationMode(resume_state.mode)
        strategy = ExecutionStrategy(resume_state.effective_strategy)
        result = await self.run(
            resume_state.chat_id,
            follow_up_content,
            mode,
            resume_state.role_ids,
            record_user_message=record_user_message,
        )

        if result.messages:
            last_message = result.messages[-1]
            merged_metadata = dict(last_message.metadata or {})
            merged_metadata.update(metadata)
            last_message.metadata = merged_metadata
            return last_message

        return await conversation_store.add_message(
            resume_state.chat_id,
            MessageRole.ASSISTANT,
            "Resumed after your clarification.",
            metadata=metadata,
        )

    def _build_content_from_heading_choice_options(
        self,
        requested_tag: str,
        available_tags: list[str],
    ) -> list[UserChoiceOption]:
        """
        Build user-choice options when a requested heading tag is missing.

        :param requested_tag: Tag the agenda step expected
        :param available_tags: Heading tags found in the source HTML
        :return: Selectable recovery options
        """
        return build_clarification_options(
            ClarificationKind.MISSING_CONTENT_TAG,
            {
                "requested_tag": requested_tag,
                "available_tags": available_tags,
            },
        )
