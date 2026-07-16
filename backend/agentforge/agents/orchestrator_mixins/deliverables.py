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


class DeliverablesMixin:
    """Mixin for AgentOrchestrator deliverables."""

    async def _materialize_missing_files(
        self,
        user_content: str,
        file_paths: list[str],
        role_id: str = "developer",
    ) -> str:
        """
        Generate file contents with the LLM and write them directly to disk.

        :param user_content: Original user request
        :param file_paths: Workspace-relative files that are still missing
        :param role_id: Role used for model routing
        :return: Summary of written files or empty string
        """
        if not file_paths:
            return ""

        role = role_registry.get_role(role_id)
        agent_name = role.name if role else role_id
        llm, _ = await self._resolve_llm(user_content, role_id, mode_single=True)
        written: list[str] = []
        for path in file_paths:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You generate complete file contents for software projects. "
                            "Reply with file content only. No markdown fences, no explanation."
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_materialization_prompt(
                            user_content,
                            path,
                            file_paths,
                        ),
                    },
                ]
                result = await llm.complete(messages, tools=None, max_tokens=4096)
                body = ""
                if not result.get("error"):
                    body = result.get("content") or ""
                body = prepare_deliverable_content(path, body, user_content, file_paths)
                success, _output = await write_file_direct(path, body)
                if success:
                    written.append(path)

        if not written:
            return ""
        return "Created files on disk:\n- " + "\n- ".join(written)


    async def _guarantee_workspace_deliverables(
        self,
        chat_id: str,
        user_content: str,
        intent: WorkspaceIntent,
        role_id: str = "developer",
        on_event: Callable | None = None,
    ) -> str:
        """
        Ensure planned workspace files exist, using direct writes and scaffolds.

        :param chat_id: Chat session ID
        :param user_content: Original user request
        :param intent: Parsed workspace intent
        :param role_id: Role used for model routing during content generation
        :param on_event: Optional WebSocket callback
        :return: Summary of created files or empty string
        """
        if not intent.wants_file_creation:
            return ""

        planned = plan_deliverable_files(user_content, intent)
        missing = [path for path in planned if not file_exists_in_workspace(path)]
        if not missing:
            return ""

        role = role_registry.get_role(role_id)
        agent_name = role.name if role else role_id
        async with command_audit_scope(chat_id, role_id, agent_name, on_event):
            summary = await self._materialize_missing_files(
                user_content,
                missing,
                role_id=role_id,
            )
            still_missing = [path for path in missing if not file_exists_in_workspace(path)]
            for path in still_missing:
                body = prepare_deliverable_content(
                    path,
                    fallback_file_content(path, user_content),
                    user_content,
                    planned,
                )
                await write_file_direct(path, body)

        created = [path for path in planned if file_exists_in_workspace(path)]
        if not created:
            return summary

        if summary and "Created files on disk" in summary:
            return summary
        return "Created files on disk:\n- " + "\n- ".join(created)


    def _seed_created_write_facts(
        self,
        task_state: TaskState | None,
        user_content: str,
        intent: WorkspaceIntent,
        *,
        agent_id: str = "developer",
        round_num: int = 0,
    ) -> None:
        """
        Record verified write facts for deliverables that exist on disk.

        :param task_state: Active task board or None
        :param user_content: Original user request
        :param intent: Parsed workspace intent
        :param agent_id: Agent role identifier
        :param round_num: Orchestration round index
        """
        if task_state is None or not intent.wants_file_creation:
            return
        planned = plan_deliverable_files(user_content, intent)
        created = [path for path in planned if file_exists_in_workspace(path)]
        if created:
            seed_write_facts(task_state, created, agent_id=agent_id, round_num=round_num)


    async def _ensure_requested_files(
        self,
        chat_id: str,
        user_content: str,
        intent: WorkspaceIntent,
        memory_context: str,
        tools: ToolRegistry,
        memory_scope: str,
        on_event: Callable | None,
        intervention_queue: asyncio.Queue[str] | None,
        task_state: TaskState | None = None,
    ) -> tuple[str | None, AgentMessage | None]:
        """
        Run a dedicated developer implementation pass for explicit file requests.

        :param chat_id: Chat session ID
        :param user_content: Original user request
        :param intent: Parsed workspace intent
        :param memory_context: Persistent memory context
        :param tools: Full tool registry
        :param memory_scope: Memory scope label
        :param on_event: Optional WebSocket event callback
        :param intervention_queue: Optional live user input queue
        :param task_state: Shared task board for verified write facts
        :return: Tuple of implementation summary and discussion message
        """
        if not intent.wants_file_creation:
            return None, None

        planned = plan_deliverable_files(user_content, intent)
        if not planned:
            return None, None

        missing = [path for path in planned if not file_exists_in_workspace(path)]
        if not missing:
            return None, None

        developer = role_registry.get_role("developer")
        if developer is None:
            return None, None

        if on_event:
            await on_event({
                "type": "agent_start",
                "agent_id": developer.id,
                "agent_name": developer.name,
                "round": 0,
            })

        content = await self._guarantee_workspace_deliverables(
            chat_id,
            user_content,
            intent,
            role_id=developer.id,
            on_event=on_event,
        )
        await self._emit_agent_end(on_event, developer.id, developer.name, round_num=0)
        if not content:
            return None, None

        self._seed_created_write_facts(
            task_state,
            user_content,
            intent,
            agent_id=developer.id,
            round_num=0,
        )

        discussion = AgentMessage(
            from_agent=developer.name,
            to_agent="team",
            content=content,
            timestamp=datetime.now(timezone.utc),
        )
        return content, discussion


    async def _refresh_reads_after_writes(
        self,
        chat_id: str,
        user_content: str,
        intent: WorkspaceIntent,
        task_state: TaskState | None,
        on_event: Callable | None,
        prefetched_reads: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Read files from disk after writes for write-then-read requests.

        :param chat_id: Chat session ID
        :param user_content: Original user request
        :param intent: Parsed workspace intent
        :param task_state: Shared task board
        :param on_event: Optional WebSocket callback
        :param prefetched_reads: Existing prefetch mapping to update
        :return: Updated path-to-content mapping
        """
        if not (intent.wants_file_read and intent.wants_file_creation):
            return prefetched_reads or {}

        updated = dict(prefetched_reads or {})
        async with command_audit_scope(chat_id, "system", "System", on_event):
            fresh = await prefetch_read_file_contents(user_content, intent)
        if task_state is not None:
            seed_read_facts(task_state, fresh)
        updated.update(fresh)
        return updated


    async def _apply_agenda_edits(
        self,
        chat_id: str,
        user_content: str,
        intent: WorkspaceIntent,
        task_state: TaskState | None,
        on_event: Callable | None,
    ) -> str:
        """
        Apply deterministic edit steps from the workspace agenda after read-back.

        :param chat_id: Chat session ID
        :param user_content: Original user request
        :param intent: Parsed workspace intent
        :param task_state: Shared task board
        :param on_event: Optional WebSocket callback
        :return: Summary of applied edits or empty string
        """
        if not intent.wants_file_edit:
            return ""

        agenda = build_workspace_agenda(user_content, intent)
        edit_steps = [
            step for step in agenda if step.action == AgendaAction.EDIT_FILE
        ]
        if not edit_steps:
            return ""

        applied: list[str] = []
        async with command_audit_scope(chat_id, "system", "System", on_event):
            for step in edit_steps:
                if not step.path or not step.replace_from or not step.replace_to:
                    continue
                success, message = await apply_file_text_replacement(
                    step.path,
                    step.replace_from,
                    step.replace_to,
                )
                if not success:
                    continue
                applied.append(message)
                if task_state is not None:
                    seed_edit_facts(
                        task_state,
                        step.path,
                        replace_from=step.replace_from,
                        replace_to=step.replace_to,
                    )

        if not applied:
            return ""
        return "Applied file edits:\n- " + "\n- ".join(applied)

