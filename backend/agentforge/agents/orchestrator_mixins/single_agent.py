"""Orchestrator mixin — extracted from orchestrator.py (no behavior change)."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.role_registry import role_registry
from agentforge.agents.role_router import resolve_single_role
from agentforge.agents.task_state import (
    TaskState,
    TaskType as WorkspaceTaskType,
    build_escalation_message,
    build_final_response_from_task_state,
    build_pm_verification_block,
    build_task_state,
    check_completion,
    discussion_entry_is_repeat,
    format_role_output_schema,
    format_task_board_block,
    format_task_plan_block,
    increment_weak_retry,
    MAX_REPETITION_STALLS,
    record_tool_result_as_fact,
    seed_read_facts,
    seed_write_facts,
    seed_edit_facts,
    MAX_WEAK_RETRIES,
)
from agentforge.agents.prompt_normalizer import (
    PromptNormalizationResult,
    format_prompt_normalization_block,
)
from agentforge.agents.workspace_agenda import AgendaAction, build_workspace_agenda
from agentforge.agents.workspace_intent import WorkspaceIntent, detect_workspace_intent
from agentforge.agents.workspace_executor import (
    apply_file_text_replacement,
    build_deliverable_status_summary,
    build_implementation_prompt,
    build_materialization_prompt,
    build_read_task_summary,
    fallback_file_content,
    file_exists_in_workspace,
    missing_requested_files,
    plan_deliverable_files,
    prefetch_read_file_contents,
    prepare_deliverable_content,
    strip_code_fences,
    write_file_direct,
)
from agentforge.config import settings
from agentforge.llm.provider import LLMProvider
from agentforge.memory.store import memory_store
from agentforge.models.schemas import (
    AgentMessage,
    AgentRole,
    ApprovalResponse,
    ApprovalResumeState,
    ExecutionStrategy,
    MessageRole,
    MessageResponse,
    OrchestrationResponse,
    ToolCallResult,
)
from agentforge.storage.conversation_store import conversation_store
from agentforge.tools.registry import ToolRegistry
from agentforge.services.command_audit import (
    CommandAuditContext,
    audit_context,
    command_audit_scope,
    execute_approved_shell_command,
    execute_shell_command,
    record_command,
)


class SingleAgentMixin:
    """Mixin for AgentOrchestrator single_agent."""

    async def _run_single(
        self,
        chat_id: str,
        user_content: str,
        role_ids: list[str],
        memory_context: str,
        tools: ToolRegistry,
        memory_scope: str,
        effective_strategy: ExecutionStrategy,
        on_event: Callable | None,
        intervention_queue: asyncio.Queue[str] | None = None,
        workspace_intent: WorkspaceIntent | None = None,
        path_context: str = "",
        task_state: TaskState | None = None,
        prefetched_reads: dict[str, str] | None = None,
        prompt_normalization: PromptNormalizationResult | None = None,
    ) -> OrchestrationResponse:
        """Single agent with a selected software-development role."""
        _ = prompt_normalization
        await self._ensure_not_cancelled()
        prefetched_reads = prefetched_reads or {}
        role_id, used_auto = resolve_single_role(role_ids, user_content)
        if used_auto and on_event:
            await on_event({
                "type": "role_resolved",
                "role_id": role_id,
                "auto": True,
            })

        role = role_registry.get_role(role_id) or role_registry.get_role(self.DEFAULT_SINGLE_ROLE)
        if role is None:
            raise RuntimeError("Default developer role is not registered.")
        role_id = role.id
        workspace_intent = workspace_intent or detect_workspace_intent(user_content)
        if task_state is None:
            task_state = build_task_state(user_content, workspace_intent)
        needs_tools = self._prompt_needs_tools(user_content, role_id)
        agent_tools = (
            self._tools_for_role(role_id, chat_id, memory_scope, tools)
            if needs_tools
            else ToolRegistry()
        )
        system = self._build_system_prompt(
            role,
            memory_context,
            tools_enabled=needs_tools,
            workspace_intent=workspace_intent,
            path_context=path_context,
            task_state=task_state,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        discussions: list[AgentMessage] = []
        pending = approval_manager.list_pending(chat_id)

        if on_event:
            await on_event({
                "type": "agent_start",
                "agent_id": role_id,
                "agent_name": role.name,
                "round": 1,
            })

        content, routing = await self._agent_loop(
            chat_id,
            role_id,
            role.name,
            messages,
            agent_tools,
            memory_scope,
            on_event,
            user_content=user_content,
            role_id=role_id,
            mode_single=True,
            intervention_queue=intervention_queue,
            workspace_intent=workspace_intent,
            task_state=task_state,
        )
        await self._emit_agent_end(on_event, role_id, role.name, round_num=1)

        still_missing = missing_requested_files(user_content, workspace_intent)
        if still_missing and workspace_intent.wants_file_creation:
            fallback = await self._guarantee_workspace_deliverables(
                chat_id,
                user_content,
                workspace_intent,
                role_id=role_id,
                on_event=on_event,
            )
            self._seed_created_write_facts(
                task_state,
                user_content,
                workspace_intent,
                agent_id=role_id,
                round_num=1,
            )
            if fallback:
                content = (
                    fallback
                    if self._is_weak_discussion_content(content)
                    else f"{content}\n\n{fallback}"
                )
        elif workspace_intent.wants_file_creation:
            fallback = await self._guarantee_workspace_deliverables(
                chat_id,
                user_content,
                workspace_intent,
                role_id=role_id,
                on_event=on_event,
            )
            self._seed_created_write_facts(
                task_state,
                user_content,
                workspace_intent,
                agent_id=role_id,
                round_num=1,
            )
            if fallback and self._is_weak_discussion_content(content):
                content = fallback
        if workspace_intent.wants_file_creation and workspace_intent.wants_file_read:
            pipeline_summary, refreshed = await self._execute_workspace_agenda_pipeline(
                chat_id,
                user_content,
                workspace_intent,
                task_state,
                on_event,
                prefetched_reads,
            )
            if pipeline_summary and on_event:
                await on_event({"type": "agent_message", "content": pipeline_summary})
            read_summary = build_final_response_from_task_state(task_state)
            if not read_summary:
                read_summary = build_read_task_summary(
                    user_content,
                    workspace_intent,
                    refreshed,
                )
            if read_summary and self._is_weak_discussion_content(content):
                content = read_summary
        elif workspace_intent.wants_file_read:
            read_summary = build_final_response_from_task_state(task_state)
            if not read_summary:
                read_summary = build_read_task_summary(
                    user_content,
                    workspace_intent,
                    prefetched_reads,
                )
            if read_summary and self._is_weak_discussion_content(content):
                content = read_summary

        await self._ensure_not_cancelled()

        msg = await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            content,
            agent_id=role_id,
            agent_name=role.name,
            metadata={"routing": routing},
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[msg],
            agent_discussions=discussions,
            pending_approvals=pending,
            resolved_role_id=role_id if used_auto else None,
            effective_execution_strategy=effective_strategy,
        )


    async def _run_quick(
        self,
        chat_id: str,
        user_content: str,
        memory_context: str,
        memory_enabled: bool,
        effective_strategy: ExecutionStrategy,
        on_event: Callable | None,
        intervention_queue: asyncio.Queue[str] | None = None,
        path_context: str = "",
    ) -> OrchestrationResponse:
        """Fast chat without tools, role routing, or heavy system prompts."""
        await self._ensure_not_cancelled()
        llm, routing = await self._resolve_llm(user_content, role_id=None, mode_single=False)

        if on_event:
            await on_event({
                "type": "agent_start",
                "agent_id": "assistant",
                "agent_name": "Assistant",
                "round": 1,
            })
            await on_event({
                "type": "model_selected",
                "agent_id": "assistant",
                "agent_name": "Assistant",
                "routing": routing,
            })

        system = self.QUICK_SYSTEM_PROMPT
        if self._ambient_context:
            system += f"\n\n{self._ambient_context}"
        if memory_enabled and memory_context:
            system += f"\n\n{memory_context}"
        if path_context:
            system += f"\n\n{path_context}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        content = ""
        model_used = routing.get("model", llm.config.model)

        while True:
            await self._ensure_not_cancelled()
            await self._append_interventions_to_messages(
                messages,
                intervention_queue,
                on_event,
            )
            content, model_used = await self._stream_llm_complete(
                llm,
                messages,
                on_event,
            )
            if intervention_queue is None or intervention_queue.empty():
                break
            messages.append({"role": "assistant", "content": content})

        await self._emit_agent_end(on_event, "assistant", "Assistant", round_num=1)
        routing["model"] = model_used
        pending = approval_manager.list_pending(chat_id)

        msg = await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            content,
            metadata={"routing": routing, "quick_chat": True},
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[msg],
            agent_discussions=[],
            pending_approvals=pending,
            effective_execution_strategy=effective_strategy,
        )

