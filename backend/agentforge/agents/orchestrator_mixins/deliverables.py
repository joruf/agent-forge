"""Orchestrator mixin — extracted from orchestrator.py (no behavior change)."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from dataclasses import asdict
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
    emit_task_board_update,
    persist_task_board,
    record_tool_result_as_fact,
    seed_read_facts,
    seed_write_facts,
    seed_edit_facts,
    seed_step_error_fact,
    MAX_WEAK_RETRIES,
)
from agentforge.agents.prompt_normalizer import (
    PromptNormalizationResult,
    format_prompt_normalization_block,
)
from agentforge.agents.compound_planner import build_compound_plan, format_compound_plan_block
from agentforge.agents.workspace_agenda import AgendaAction, build_workspace_agenda
from agentforge.agents.workspace_intent import WorkspaceIntent, detect_workspace_intent
from agentforge.agents.workspace_executor import (
    apply_file_text_replacement,
    apply_html_heading_insertion,
    apply_html_tag_insertion,
    build_deliverable_status_summary,
    build_implementation_prompt,
    build_materialization_prompt,
    build_read_task_summary,
    fallback_file_content,
    file_exists_in_workspace,
    missing_requested_files,
    plan_deliverable_files,
    plan_derived_txt_from_heading,
    plan_derived_txt_from_h1,
    list_available_headings,
    plan_write_body_from_html_source,
    prefetch_read_file_contents,
    prepare_deliverable_content,
    read_workspace_file,
    strip_code_fences,
    write_file_direct,
)
from agentforge.config import settings
from pathlib import Path
from agentforge.llm.provider import LLMProvider
from agentforge.memory.store import memory_store
from agentforge.models.schemas import (
    AgentMessage,
    AgentRole,
    AgendaResumeState,
    ApprovalResponse,
    ApprovalResumeState,
    ExecutionStrategy,
    MessageRole,
    MessageResponse,
    OrchestrationResponse,
    ToolCallResult,
    UserChoiceOption,
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

        await emit_task_board_update(on_event, task_state)
        if not applied:
            return ""
        return "Applied file edits:\n- " + "\n- ".join(applied)


    async def _apply_agenda_derived_writes(
        self,
        chat_id: str,
        user_content: str,
        intent: WorkspaceIntent,
        task_state: TaskState | None,
        on_event: Callable | None,
    ) -> str:
        """
        Create files whose names are derived from on-disk HTML content (e.g. H1 → .txt).

        :param chat_id: Chat session ID
        :param user_content: Original user request
        :param intent: Parsed workspace intent
        :param task_state: Shared task board
        :param on_event: Optional WebSocket callback
        :return: Summary of derived writes or empty string
        """
        if not intent.wants_derived_file and not any(
            step.action == AgendaAction.WRITE_DERIVED_FILE
            for step in build_workspace_agenda(user_content, intent)
        ):
            return ""

        agenda = build_workspace_agenda(user_content, intent)
        derived_steps = [
            step for step in agenda if step.action == AgendaAction.WRITE_DERIVED_FILE
        ]
        if not derived_steps:
            return ""

        applied: list[str] = []
        async with command_audit_scope(chat_id, "system", "System", on_event):
            for step in derived_steps:
                source_path = step.source_path
                if not source_path:
                    continue
                success, html_content = read_workspace_file(source_path)
                if not success:
                    continue
                planned = plan_derived_txt_from_heading(
                    source_path,
                    html_content,
                    naming_source=step.naming_source or "h1",
                )
                if not planned:
                    continue
                derived_path, body = planned
                extension = step.derived_extension or ".txt"
                if extension != ".txt":
                    derived_path = derived_path.rsplit(".", 1)[0] + extension
                write_ok, _output = await write_file_direct(derived_path, body)
                if not write_ok:
                    continue
                naming_label = step.naming_source or "heading"
                applied.append(
                    f"Created `{derived_path}` named after {naming_label} in `{source_path}`",
                )
                if task_state is not None:
                    seed_write_facts(
                        task_state,
                        [derived_path],
                        source=f"derived_from_{naming_label}",
                    )
                    if derived_path not in task_state.targets:
                        task_state.targets.append(derived_path)

        await emit_task_board_update(on_event, task_state)
        if not applied:
            return ""
        return "Applied derived file writes:\n- " + "\n- ".join(applied)


    async def _request_content_from_heading_choice(
        self,
        chat_id: str,
        on_event: Callable | None,
        *,
        step_index: int,
        step_path: str,
        requested_tag: str,
        content_source_path: str,
        available_tags: list[str],
        user_content: str,
        intent: WorkspaceIntent,
        task_state: TaskState | None,
        prefetched_reads: dict[str, str],
    ) -> str:
        """
        Pause the agenda pipeline and ask the user how to recover.

        :return: Approval request ID
        """
        alternates = [tag for tag in available_tags if tag != requested_tag]
        options = self._build_content_from_heading_choice_options(
            requested_tag,
            alternates,
        )
        if alternates:
            description = (
                f"Could not create `{step_path}`: no `<{requested_tag}>` found in "
                f"`{content_source_path}`. Available headings: "
                f"{', '.join(f'<{tag}>' for tag in alternates)}."
            )
        else:
            description = (
                f"Could not create `{step_path}`: no `<{requested_tag}>` found in "
                f"`{content_source_path}` and no alternate headings are available."
            )
        from agentforge.agents.user_clarification import (
            ClarificationKind,
            request_clarification,
        )
        from agentforge.models.schemas import AgendaResumeState

        task_snapshot = task_state.to_persisted_payload() if task_state is not None else None
        resume_state = AgendaResumeState(
            chat_id=chat_id,
            user_content=user_content,
            intent=asdict(intent),
            task_state_snapshot=task_snapshot,
            step_index=step_index,
            step_path=step_path,
            requested_tag=requested_tag,
            content_source_path=content_source_path,
            prefetched_reads=dict(prefetched_reads),
        )
        return await request_clarification(
            chat_id,
            ClarificationKind.MISSING_CONTENT_TAG,
            description,
            options,
            resume_state,
            on_event,
            allows_custom_input=False,
        )


    async def _resume_agenda_after_user_choice(
        self,
        resume_state: AgendaResumeState,
        response: ApprovalResponse,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> MessageResponse:
        """
        Resume a paused workspace agenda pipeline after user choice.

        :param resume_state: Saved agenda continuation state
        :param response: User approval response with choice_id
        :return: Assistant message summarizing resumed pipeline work
        """
        choice_id = (response.choice_id or "").strip()
        if not response.approved or not choice_id:
            choice_id = "abort"

        if choice_id == "abort":
            return await conversation_store.add_message(
                resume_state.chat_id,
                MessageRole.ASSISTANT,
                "Workflow aborted at your request. Remaining agenda steps were not executed.",
                metadata={
                    "kind": "agenda_user_choice",
                    "choice_id": choice_id,
                    "step_path": resume_state.step_path,
                },
            )

        resume_action: str | None = None
        alternate_tag: str | None = None
        start_index = resume_state.step_index
        if choice_id == "skip":
            resume_action = "skip"
            start_index = resume_state.step_index + 1
        elif choice_id.startswith("use_"):
            resume_action = "use_tag"
            alternate_tag = choice_id.removeprefix("use_")
        else:
            return await conversation_store.add_message(
                resume_state.chat_id,
                MessageRole.ASSISTANT,
                f"Unknown choice '{choice_id}'. Workflow was not resumed.",
                metadata={
                    "kind": "agenda_user_choice",
                    "choice_id": choice_id,
                    "resume_error": True,
                },
            )

        intent = WorkspaceIntent(**resume_state.intent)
        task_state = build_task_state(
            resume_state.user_content,
            intent,
            resume_state.task_state_snapshot,
        )
        summary, _reads, paused = await self._execute_workspace_agenda_pipeline(
            resume_state.chat_id,
            resume_state.user_content,
            intent,
            task_state,
            on_event,
            resume_state.prefetched_reads,
            start_index=start_index,
            resume_at_step=resume_state.step_index,
            resume_action=resume_action,
            resume_alternate_tag=alternate_tag,
        )
        await persist_task_board(resume_state.chat_id, task_state, on_event=on_event)
        if paused:
            pending = approval_manager.list_pending(resume_state.chat_id)
            pending_desc = pending[0].description if pending else "Another choice is required."
            content = (
                "The workflow paused again while waiting for your input:\n"
                f"{pending_desc}"
            )
        elif summary:
            content = summary
        else:
            content = "Agenda pipeline resumed and completed with no additional actions."
        return await conversation_store.add_message(
            resume_state.chat_id,
            MessageRole.ASSISTANT,
            content,
            metadata={
                "kind": "agenda_user_choice",
                "choice_id": choice_id,
                "resumed_from_approval": True,
                "step_path": resume_state.step_path,
            },
        )


    async def _execute_workspace_agenda_pipeline(
        self,
        chat_id: str,
        user_content: str,
        intent: WorkspaceIntent,
        task_state: TaskState | None,
        on_event: Callable | None,
        prefetched_reads: dict[str, str] | None = None,
        start_index: int = 0,
        resume_at_step: int | None = None,
        resume_action: str | None = None,
        resume_alternate_tag: str | None = None,
    ) -> tuple[str, dict[str, str], bool]:
        """
        Execute workspace agenda steps sequentially in plan order.

        Reads, edits, and derived writes run only when reached in the ordered
        agenda so cross-step associations stay consistent.

        :param chat_id: Chat session ID
        :param user_content: Original user request
        :param intent: Parsed workspace intent
        :param task_state: Shared task board
        :param on_event: Optional WebSocket callback
        :param prefetched_reads: Existing read cache to update
        :param start_index: Agenda step index to begin execution from
        :param resume_at_step: Step index being resolved after user choice
        :param resume_action: Recovery action (use_tag or skip)
        :param resume_alternate_tag: Alternate heading tag when resume_action is use_tag
        :return: Tuple of summary text, updated read cache, and paused flag
        """
        agenda = build_workspace_agenda(user_content, intent)
        if not agenda:
            return "", prefetched_reads or {}, False

        reads = dict(prefetched_reads or {})
        summaries: list[str] = []

        async with command_audit_scope(chat_id, "system", "System", on_event):
            for index, step in enumerate(agenda):
                if index < start_index:
                    continue
                if (
                    resume_at_step is not None
                    and index == resume_at_step
                    and resume_action == "skip"
                ):
                    summaries.append(f"Skipped write for `{step.path}` at your request")
                    continue
                if step.action == AgendaAction.CREATE_DIRECTORY and step.path:
                    target = (settings.workspace_root / step.path).resolve()
                    target.mkdir(parents=True, exist_ok=True)
                    summaries.append(f"Ensured directory `{step.path}`")
                    continue

                if step.action == AgendaAction.WRITE_FILE and step.path:
                    if step.content_from_heading and step.content_source_path:
                        content_tag = step.content_from_heading
                        if (
                            resume_at_step is not None
                            and index == resume_at_step
                            and resume_action == "use_tag"
                            and resume_alternate_tag
                        ):
                            content_tag = resume_alternate_tag
                        success, html_content = read_workspace_file(step.content_source_path)
                        if not success:
                            error_message = (
                                f"Could not create `{step.path}`: failed to read "
                                f"`{step.content_source_path}` ({html_content})"
                            )
                            summaries.append(error_message)
                            if task_state is not None:
                                seed_step_error_fact(
                                    task_state,
                                    error_message,
                                    step_path=step.path,
                                    kind="file_write_error",
                                )
                            continue
                        body = plan_write_body_from_html_source(
                            html_content,
                            content_tag,
                        )
                        if not body:
                            tag = content_tag
                            available_tags = list_available_headings(html_content)
                            await self._request_content_from_heading_choice(
                                chat_id,
                                on_event,
                                step_index=index,
                                step_path=step.path,
                                requested_tag=tag,
                                content_source_path=step.content_source_path,
                                available_tags=available_tags,
                                user_content=user_content,
                                intent=intent,
                                task_state=task_state,
                                prefetched_reads=reads,
                            )
                            await emit_task_board_update(on_event, task_state)
                            return "", reads, True
                        write_ok, write_output = await write_file_direct(step.path, body)
                        if write_ok and task_state is not None:
                            seed_write_facts(
                                task_state,
                                [step.path],
                                source=f"content_from_{content_tag}",
                            )
                            summaries.append(
                                f"Created `{step.path}` with "
                                f"{content_tag} text from "
                                f"`{step.content_source_path}`",
                            )
                        else:
                            error_message = (
                                f"Could not create `{step.path}`: {write_output}"
                            )
                            summaries.append(error_message)
                            if task_state is not None:
                                seed_step_error_fact(
                                    task_state,
                                    error_message,
                                    step_path=step.path,
                                    kind="file_write_error",
                                )
                    elif not file_exists_in_workspace(step.path):
                        body = prepare_deliverable_content(
                            step.path,
                            fallback_file_content(step.path, user_content),
                            user_content,
                            [step.path],
                        )
                        success, _output = await write_file_direct(step.path, body)
                        if success and task_state is not None:
                            seed_write_facts(task_state, [step.path], source="agenda_write")
                    continue

                if step.action == AgendaAction.READ_FILE and step.path:
                    success, payload = read_workspace_file(step.path)
                    if task_state is not None:
                        seed_read_facts(
                            task_state,
                            {step.path: payload if success else f"[ERROR] {payload}"},
                        )
                    if success:
                        reads[step.path] = payload
                    summaries.append(f"Read `{step.path}` for chat output")
                    continue

                if step.action == AgendaAction.EDIT_FILE and step.path:
                    if step.insert_heading_text:
                        if step.insert_tag and step.insert_after_tag:
                            success, message = await apply_html_tag_insertion(
                                step.path,
                                step.insert_after_tag,
                                step.insert_tag,
                                step.insert_heading_text,
                            )
                        else:
                            success, message = await apply_html_heading_insertion(
                                step.path,
                                step.insert_after_heading or 1,
                                step.insert_heading_level or 2,
                                step.insert_heading_text,
                            )
                        if success and task_state is not None:
                            seed_edit_facts(
                                task_state,
                                step.path,
                                replace_from="",
                                replace_to=step.insert_heading_text,
                            )
                            summaries.append(message)
                    elif step.replace_from and step.replace_to:
                        success, message = await apply_file_text_replacement(
                            step.path,
                            step.replace_from,
                            step.replace_to,
                        )
                        if success and task_state is not None:
                            seed_edit_facts(
                                task_state,
                                step.path,
                                replace_from=step.replace_from,
                                replace_to=step.replace_to,
                            )
                            summaries.append(message)
                    continue

                if step.action == AgendaAction.WRITE_DERIVED_FILE and step.source_path:
                    success, html_content = read_workspace_file(step.source_path)
                    if not success:
                        continue
                    planned = plan_derived_txt_from_heading(
                        step.source_path,
                        html_content,
                        naming_source=step.naming_source or "h1",
                    )
                    if not planned:
                        continue
                    derived_path, body = planned
                    extension = step.derived_extension or ".txt"
                    if extension != ".txt":
                        derived_path = derived_path.rsplit(".", 1)[0] + extension
                    write_ok, _output = await write_file_direct(derived_path, body)
                    if write_ok and task_state is not None:
                        naming_label = step.naming_source or "heading"
                        seed_write_facts(
                            task_state,
                            [derived_path],
                            source=f"derived_from_{naming_label}",
                        )
                        if derived_path not in task_state.targets:
                            task_state.targets.append(derived_path)
                        summaries.append(
                            f"Created `{derived_path}` named after {naming_label} in `{step.source_path}`",
                        )

        await emit_task_board_update(on_event, task_state)
        if not summaries:
            return "", reads, False
        return "Agenda pipeline:\n- " + "\n- ".join(summaries), reads, False

