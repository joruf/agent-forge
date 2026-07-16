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
from agentforge.agents.compound_planner import build_compound_plan, format_compound_plan_block
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


class MultiAgentMixin:
    """Mixin for AgentOrchestrator multi_agent."""

    def _build_multi_prompt(
        self,
        role: AgentRole,
        round_num: int,
        user_content: str,
        transcript: list[str],
        workspace_intent: WorkspaceIntent | None = None,
        task_state: TaskState | None = None,
    ) -> str:
        """
        Build the role-specific multi-agent prompt for one turn.

        :param role: Current role instance
        :param round_num: Zero-based round index
        :param user_content: Original user request
        :param transcript: Discussion transcript (or frozen snapshot)
        :param workspace_intent: Parsed workspace file/command intent
        :return: Prompt text for the role
        """
        intent = workspace_intent or detect_workspace_intent(user_content)
        max_rounds = self._resolve_multi_rounds()
        workspace_note = ""
        task_board_note = ""
        if task_state:
            task_board_note = "\n\n" + format_task_board_block(task_state)
            if role.id == "project_manager" and round_num == 0:
                task_board_note = "\n\n" + format_task_plan_block(task_state) + task_board_note
        if intent.wants_file_read:
            planned = intent.target_paths
            workspace_note = (
                "\n\nIMPORTANT: The user wants to READ existing file content and see it in chat. "
                "Use read_file for each requested path and quote the content verbatim. "
                "Do not write files or reply with JSON status placeholders."
            )
            if planned:
                files_block = "\n".join(f"- {path}" for path in planned)
                workspace_note += (
                    "\nRequested workspace-relative file path(s):\n"
                    f"{files_block}"
                )
        elif intent.wants_file_creation and role.id in self.FULL_TOOL_ROLES:
            planned = plan_deliverable_files(user_content, intent)
            workspace_note = (
                "\n\nIMPORTANT: The user wants files saved on disk. "
                "Use write_file for every file you create. "
                "Do not paste code or JSON templates in chat."
            )
            if planned:
                files_block = "\n".join(f"- {path}" for path in planned)
                workspace_note += (
                    "\nRequired workspace-relative file path(s):\n"
                    f"{files_block}\n"
                    "Use these exact paths with write_file."
                )
            elif intent.target_dirs:
                workspace_note += (
                    f"\nTarget directory (workspace-relative): {', '.join(intent.target_dirs)}"
                )

        if role.id == "project_manager" and round_num < max_rounds - 1:
            pm_note = ""
            if intent.wants_file_read:
                pm_note = (
                    "\nThe user expects the actual file content in the final answer. "
                    "Ensure the Developer uses read_file and quotes the content."
                )
            elif intent.wants_file_creation:
                pm_note = (
                    "\nThe user expects real files in the workspace. "
                    "Ensure the Developer uses write_file — not chat output only."
                )
            return (
                f"Team discussion (round {round_num + 1}):\n"
                + "\n".join(transcript[-10:])
                + "\n\nAs Project Manager, coordinate the team. "
                "If you need user input, prefix with [ASK_USER] and state your question."
                + pm_note
            )
        if role.id == "project_manager":
            deliverable_note = ""
            if intent.wants_file_read:
                paths = intent.target_paths
                if paths:
                    files_block = "\n".join(f"- {path}" for path in paths)
                    deliverable_note = (
                        "\nQuote the verified file content for the user:\n"
                        f"{files_block}\n"
                        "Do not invent content or claim success without showing the text."
                    )
            elif intent.wants_file_creation:
                planned = plan_deliverable_files(user_content, intent)
                if planned:
                    files_block = "\n".join(f"- {path}" for path in planned)
                    deliverable_note = (
                        "\nOnly claim success when these files exist on disk:\n"
                        f"{files_block}\n"
                        "Do not invent paths or results."
                    )
            return (
                "Final synthesis requested.\n"
                + "\n".join(transcript)
                + "\n\nProvide the final result for the user."
                + (
                    " Mention which files were written to disk."
                    if intent.wants_file_creation
                    else (
                        " Show the requested file content verbatim."
                        if intent.wants_file_read
                        else ""
                    )
                )
                + deliverable_note
            )

        role_note = self._role_multi_discussion_note(role.id, intent, task_state)
        parallel_note = ""
        if task_state and self._is_parallel_role(role.id):
            parallel_note = (
                "\n\nParallel specialist turn: rely on the shared task board facts "
                "in the system prompt, not only the discussion transcript."
            )
        return (
            f"User request: {user_content}\n\nDiscussion so far:\n"
            + "\n".join(transcript[-8:])
            + f"\n\nRespond as {role.name}. Be concise and actionable."
            + workspace_note
            + task_board_note
            + role_note
            + parallel_note
        )

    @staticmethod
    def _role_multi_discussion_note(
        role_id: str,
        intent: WorkspaceIntent | None = None,
        task_state: TaskState | None = None,
    ) -> str:
        """
        Return role-specific instructions for multi-agent discussion turns.

        :param role_id: Agent role identifier
        :param intent: Parsed workspace intent
        :param task_state: Shared task board for the current run
        :return: Additional prompt guidance
        """
        schema = ""
        if task_state:
            schema = format_role_output_schema(role_id, task_state.task_type)
        if role_id == "reviewer":
            return (
                "\n\nReview the existing discussion only. Do not generate full HTML, "
                "PHP, or complete implementations. Give brief, actionable feedback."
                + schema
            )
        if role_id == "developer":
            return (
                "\n\nIf you use read_file, quote the file content for the team. "
                "If you use write_file or run_command, summarize what you changed."
                + schema
            )
        if role_id in {"software_tester", "security"}:
            return (
                "\n\nAnalyze and report findings only. Do not replace the Developer "
                "by outputting full implementations."
                + schema
            )
        if role_id == "project_manager":
            return schema
        return schema


    async def _emit_agent_end(
        self,
        on_event: Callable | None,
        agent_id: str,
        agent_name: str,
        round_num: int | None = None,
    ) -> None:
        """
        Emit a WebSocket event when an agent finishes its active turn.

        :param on_event: Optional WebSocket event callback
        :param agent_id: Agent role identifier
        :param agent_name: Agent display name
        :param round_num: Optional round index for multi-agent runs
        """
        if on_event:
            payload: dict[str, Any] = {
                "type": "agent_end",
                "agent_id": agent_id,
                "agent_name": agent_name,
            }
            if round_num is not None:
                payload["round"] = round_num
            await on_event(payload)


    async def _run_multi_role_turn(
        self,
        chat_id: str,
        role: AgentRole,
        round_num: int,
        user_content: str,
        transcript: list[str],
        memory_context: str,
        tools: ToolRegistry,
        memory_scope: str,
        on_event: Callable | None,
        intervention_queue: asyncio.Queue[str] | None,
        workspace_intent: WorkspaceIntent | None = None,
        path_context: str = "",
        task_state: TaskState | None = None,
    ) -> tuple[str, dict, AgentMessage]:
        """
        Execute one role turn in multi-agent mode.

        :param chat_id: Chat session ID
        :param role: Current role instance
        :param round_num: Zero-based round index
        :param user_content: Original user request
        :param transcript: Discussion transcript (or frozen snapshot)
        :param memory_context: Persistent memory context
        :param tools: Full tool registry
        :param memory_scope: Memory scope label
        :param on_event: Optional WebSocket event callback
        :param intervention_queue: Optional live user input queue
        :param workspace_intent: Parsed workspace file/command intent
        :return: Tuple of (content, routing metadata, discussion message)
        """
        intent = workspace_intent or detect_workspace_intent(user_content)
        prompt = self._build_multi_prompt(
            role,
            round_num,
            user_content,
            transcript,
            workspace_intent=intent,
            task_state=task_state,
        )
        tools_enabled = (
            (role.id in self.FULL_TOOL_ROLES and (
                role.id == "developer"
                or intent.wants_file_creation
                or intent.wants_file_read
            ))
            or (intent.wants_file_read and role.id in self.READ_EXECUTE_TOOL_ROLES)
        )
        system = self._build_system_prompt(
            role,
            memory_context,
            tools_enabled=tools_enabled,
            workspace_intent=intent,
            path_context=path_context,
            task_state=task_state,
        )
        agent_tools = self._tools_for_multi_role(
            role.id,
            chat_id,
            memory_scope,
            tools,
            intent,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

        if on_event:
            await on_event({
                "type": "agent_start",
                "agent_id": role.id,
                "agent_name": role.name,
                "round": round_num + 1,
            })

        content, routing = await self._agent_loop(
            chat_id,
            role.id,
            role.name,
            messages,
            agent_tools,
            memory_scope,
            on_event,
            user_content=user_content,
            role_id=role.id,
            intervention_queue=intervention_queue,
            workspace_intent=intent,
            task_state=task_state,
            round_num=round_num,
            mode_multi=True,
        )
        if (
            task_state
            and intent.requires_tools
            and self._is_weak_discussion_content(content)
            and role.id != "project_manager"
            and not check_completion(task_state).complete
        ):
            retries = increment_weak_retry(task_state, role.id)
            if retries >= MAX_WEAK_RETRIES:
                completion = check_completion(task_state)
                content = (
                    "[ASK_USER] "
                    + build_escalation_message(
                        task_state,
                        role.id,
                        reason=completion.reason,
                    )
                )
        await self._emit_agent_end(
            on_event,
            role.id,
            role.name,
            round_num=round_num + 1,
        )
        discussion = AgentMessage(
            from_agent=role.name,
            to_agent="team",
            content=content,
            timestamp=datetime.now(timezone.utc),
        )
        return content, routing, discussion


    async def _run_multi(
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
        """Multi-agent discussion with project manager synthesis."""
        if not role_ids:
            role_ids = ["project_manager", "developer", "reviewer"]

        prefetched_reads = prefetched_reads or {}

        roles = [role for role in role_registry.get_roles(role_ids) if role is not None]
        if not roles:
            fallback = role_registry.get_role("developer")
            if fallback is None:
                raise RuntimeError("Default developer role is not registered.")
            roles = [fallback]

        discussions: list[AgentMessage] = []
        display_request = (
            prompt_normalization.original
            if prompt_normalization is not None
            else user_content
        )
        transcript: list[str] = [f"User request: {display_request}"]
        if prompt_normalization and prompt_normalization.changed:
            normalization_block = format_prompt_normalization_block(prompt_normalization)
            if normalization_block:
                transcript.append(normalization_block)
        outputs: list[MessageResponse] = []

        pm = role_registry.get_role("project_manager")
        if pm and pm.id not in [r.id for r in roles]:
            roles = [pm] + roles

        workspace_intent = workspace_intent or detect_workspace_intent(user_content)
        if task_state is None:
            task_state = build_task_state(user_content, workspace_intent)
        roles = self._order_roles_for_intent(roles, workspace_intent)
        max_multi_rounds = self._resolve_multi_rounds()
        transcript.append(f"Project Manager: Task plan:\n{format_task_plan_block(task_state)}")

        impl_content, impl_discussion = await self._ensure_requested_files(
            chat_id=chat_id,
            user_content=user_content,
            intent=workspace_intent,
            memory_context=memory_context,
            tools=tools,
            memory_scope=memory_scope,
            on_event=on_event,
            intervention_queue=intervention_queue,
            task_state=task_state,
        )
        developer_impl_done = impl_discussion is not None
        pipeline_summary, prefetched_reads = await self._execute_workspace_agenda_pipeline(
            chat_id,
            user_content,
            workspace_intent,
            task_state,
            on_event,
            prefetched_reads,
        )
        if pipeline_summary:
            transcript.append(f"System: {pipeline_summary}")
        compound_block = format_compound_plan_block(
            build_compound_plan(user_content, workspace_intent),
        )
        if compound_block:
            transcript.append(compound_block)

        if prefetched_reads:
            read_lines = [
                "System: Verified file content loaded from disk:",
            ]
            for relative_path, payload in prefetched_reads.items():
                if payload.startswith("[ERROR]"):
                    read_lines.append(f"- {relative_path}: {payload}")
                else:
                    preview = payload.replace("\n", " ")[:120]
                    read_lines.append(f"- {relative_path}: {preview}")
            transcript.append("\n".join(read_lines))

        if impl_discussion and impl_content:
            discussions.append(impl_discussion)
            transcript.append(f"{impl_discussion.from_agent}: {impl_content}")
            if on_event:
                await on_event({
                    "type": "agent_message",
                    "discussion": impl_discussion.model_dump(mode="json"),
                    "routing": {"source": "implementation_phase"},
                })

        repetition_stalls = 0
        discussion_complete = False

        for round_num in range(max_multi_rounds):
            if discussion_complete:
                break
            await self._ensure_not_cancelled()
            await self._collect_interventions(transcript, intervention_queue, on_event)
            role_index = 0
            while role_index < len(roles):
                if discussion_complete:
                    break
                await self._ensure_not_cancelled()
                await self._collect_interventions(transcript, intervention_queue, on_event)
                role = roles[role_index]
                if developer_impl_done and role.id == "developer" and round_num == 0:
                    role_index += 1
                    continue
                if self._should_skip_multi_role_turn(role, round_num, max_multi_rounds):
                    role_index += 1
                    continue
                can_parallelize = (
                    self._is_parallel_round(
                        effective_strategy,
                        round_num,
                        max_multi_rounds,
                        workspace_intent=workspace_intent,
                    )
                    and self._is_parallel_role(role.id)
                )

                if can_parallelize:
                    batch: list[AgentRole] = []
                    while role_index < len(roles) and self._is_parallel_role(
                        roles[role_index].id
                    ):
                        batch.append(roles[role_index])
                        role_index += 1
                    frozen_transcript = list(transcript)
                    results = await asyncio.gather(*[
                        self._run_multi_role_turn(
                            chat_id=chat_id,
                            role=batch_role,
                            round_num=round_num,
                            user_content=user_content,
                            transcript=frozen_transcript,
                            memory_context=memory_context,
                            tools=tools,
                            memory_scope=memory_scope,
                            on_event=on_event,
                            intervention_queue=None,
                            workspace_intent=workspace_intent,
                            path_context=path_context,
                            task_state=task_state,
                        )
                        for batch_role in batch
                    ])
                    for batch_role, (content, routing, discussion) in zip(batch, results):
                        if discussion_entry_is_repeat(batch_role.name, content, transcript):
                            repetition_stalls += 1
                            if repetition_stalls >= MAX_REPETITION_STALLS:
                                discussion_complete = True
                                break
                            continue

                        discussions.append(discussion)
                        transcript.append(f"{batch_role.name}: {content}")

                        if on_event:
                            await on_event({
                                "type": "agent_message",
                                "discussion": discussion.model_dump(mode="json"),
                                "routing": routing,
                            })

                        if content.startswith("[ASK_USER]"):
                            return await self._build_user_input_response(
                                chat_id=chat_id,
                                role=batch_role,
                                content=content,
                                outputs=outputs,
                                discussions=discussions,
                                effective_strategy=effective_strategy,
                            )
                    if discussion_complete:
                        break
                    continue

                role_index += 1
                content, routing, discussion = await self._run_multi_role_turn(
                    chat_id=chat_id,
                    role=role,
                    round_num=round_num,
                    user_content=user_content,
                    transcript=transcript,
                    memory_context=memory_context,
                    tools=tools,
                    memory_scope=memory_scope,
                    on_event=on_event,
                    intervention_queue=intervention_queue,
                    workspace_intent=workspace_intent,
                    path_context=path_context,
                    task_state=task_state,
                )
                if discussion_entry_is_repeat(role.name, content, transcript):
                    repetition_stalls += 1
                    if repetition_stalls >= MAX_REPETITION_STALLS:
                        discussion_complete = True
                        break
                    role_index += 1
                    continue

                discussions.append(discussion)
                transcript.append(f"{role.name}: {content}")

                if on_event:
                    await on_event({
                        "type": "agent_message",
                        "discussion": discussion.model_dump(mode="json"),
                        "routing": routing,
                    })

                if content.startswith("[ASK_USER]"):
                    if workspace_intent.wants_file_creation:
                        guarantee = await self._guarantee_workspace_deliverables(
                            chat_id,
                            user_content,
                            workspace_intent,
                            on_event=on_event,
                        )
                        if guarantee:
                            discussions.append(AgentMessage(
                                from_agent="Developer",
                                to_agent="team",
                                content=guarantee,
                                timestamp=datetime.now(timezone.utc),
                            ))
                            transcript.append(f"Developer: {guarantee}")
                            role_index += 1
                            continue
                    return await self._build_user_input_response(
                        chat_id=chat_id,
                        role=role,
                        content=content,
                        outputs=outputs,
                        discussions=discussions,
                        effective_strategy=effective_strategy,
                    )

            if check_completion(task_state).complete:
                break

        final_role = role_registry.get_role("project_manager") or roles[-1]
        guarantee = await self._guarantee_workspace_deliverables(
            chat_id,
            user_content,
            workspace_intent,
            on_event=on_event,
        )
        if guarantee:
            discussions.append(AgentMessage(
                from_agent="Developer",
                to_agent="team",
                content=guarantee,
                timestamp=datetime.now(timezone.utc),
            ))
            transcript.append(f"Developer: {guarantee}")

        self._seed_created_write_facts(
            task_state,
            user_content,
            workspace_intent,
            agent_id="developer",
            round_num=max_multi_rounds,
        )

        if task_state and task_state.task_type == WorkspaceTaskType.WORKFLOW:
            retry_summary, prefetched_reads = await self._execute_workspace_agenda_pipeline(
                chat_id,
                user_content,
                workspace_intent,
                task_state,
                on_event,
                prefetched_reads,
            )
            if retry_summary:
                transcript.append(f"System: {retry_summary}")
        else:
            prefetched_reads = await self._refresh_reads_after_writes(
                chat_id,
                user_content,
                workspace_intent,
                task_state,
                on_event,
                prefetched_reads,
            )

        completion = check_completion(task_state)
        verification = build_pm_verification_block(task_state, completion)
        pm_name = final_role.name if final_role else "Project Manager"
        verification_discussion = AgentMessage(
            from_agent=pm_name,
            to_agent="team",
            content=verification,
            timestamp=datetime.now(timezone.utc),
        )
        discussions.append(verification_discussion)
        transcript.append(f"{pm_name}: {verification}")

        deliverable_summary = build_deliverable_status_summary(
            user_content,
            workspace_intent,
        )
        task_board_summary = build_final_response_from_task_state(task_state)
        if task_board_summary:
            final_content = task_board_summary
        elif workspace_intent.wants_file_read:
            read_summary = build_read_task_summary(
                user_content,
                workspace_intent,
                prefetched_reads,
            )
            final_content = read_summary or deliverable_summary or (
                transcript[-1].split(": ", 1)[-1] if transcript else "No result"
            )
        elif deliverable_summary and workspace_intent.wants_file_creation:
            final_content = deliverable_summary
        else:
            final_content = transcript[-1].split(": ", 1)[-1] if transcript else "No result"
            if guarantee and guarantee not in final_content:
                final_content = f"{guarantee}\n\n{final_content}"
        if not completion.complete and task_board_summary:
            final_content = task_board_summary
        if final_role:
            final_msg = await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                final_content,
                agent_id=final_role.id,
                agent_name=final_role.name,
                metadata={
                    "synthesis": True,
                    "task_complete": completion.complete,
                    "task_type": task_state.task_type.value,
                    "pm_verification": verification,
                },
            )
            outputs.append(final_msg)

        return OrchestrationResponse(
            chat_id=chat_id,
            messages=outputs,
            agent_discussions=discussions,
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
        )

