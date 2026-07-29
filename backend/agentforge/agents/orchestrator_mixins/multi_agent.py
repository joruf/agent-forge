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
from agentforge.agents.user_clarification import ClarificationKind, is_clarification_pending, should_skip_clarification_escalation
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
    collect_required_write_paths,
    discussion_entry_is_repeat,
    format_role_output_schema,
    format_task_board_block,
    format_task_plan_block,
    increment_verdict_retry,
    increment_weak_retry,
    MAX_REPETITION_STALLS,
    MAX_VERDICT_RETRIES,
    parse_reviewer_verdict,
    parse_tester_severity,
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
from agentforge.agents.compound_planner import (
    build_compound_plan,
    format_compound_plan_block,
    is_compound_request,
)
from agentforge.agents.workspace_agenda import AgendaAction, build_workspace_agenda
from agentforge.agents.action_requirement import (
    ActionCategory,
    ActionRequirementResult,
    analyze_action_requirement,
)
from agentforge.agents.workspace_intent import (
    WorkspaceIntent,
    detect_workspace_intent,
)
from agentforge.agents.workspace_executor import (
    apply_file_text_replacement,
    build_deliverable_status_summary,
    build_implementation_prompt,
    build_materialization_prompt,
    build_read_task_summary,
    collect_non_runnable_implementation_paths,
    collect_placeholder_implementation_paths,
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
    OrchestrationMode,
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


# Every other multi-agent turn bounds its transcript (transcript[-8:]/transcript[-10:]);
# the PM final-synthesis turn used the full, unbounded transcript. Durable facts (files
# read/written, task status) still reach it via the task board block in the system prompt.
FINAL_SYNTHESIS_TRANSCRIPT_TAIL = 16

_NO_ACTION_SYSTEM_PROMPTS: dict[ActionCategory, str] = {
    ActionCategory.CONVERSATIONAL: (
        "You are the Project Manager in AgentForge, a multi-agent coding assistant. "
        "The user sent a casual or simple message without a workspace task. "
        "Reply naturally, warmly, and briefly in the user's language. "
        "Do not ask clarifying questions for simple greetings. "
        "Do not mention tools, task plans, team coordination, or JSON. "
        "Offer help in one short sentence if appropriate."
    ),
    ActionCategory.ACKNOWLEDGMENT: (
        "You are the Project Manager in AgentForge. "
        "The user sent a brief acknowledgment or thanks. "
        "Reply warmly and briefly in the user's language. "
        "Do not mention tools, task plans, or team coordination."
    ),
    ActionCategory.INFORMATIONAL: (
        "You are the Project Manager in AgentForge. "
        "The user asked an informational question that does not require workspace tools. "
        "Give a short, direct answer in the user's language. "
        "Do not invoke developers, reviewers, tools, or task plans."
    ),
    ActionCategory.EMPTY: (
        "You are the Project Manager in AgentForge. "
        "The user message was empty. "
        "Reply briefly that you did not receive a request and offer help."
    ),
}


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
                "Write complete, runnable code — no placeholders, TODO stubs, or "
                "'implement here' comments. "
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
                + "\n".join(transcript[-FINAL_SYNTHESIS_TRANSCRIPT_TAIL:])
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
        if role_id == "developer":
            return (
                "\n\nIf you use read_file, quote the file content for the team. "
                "If you use write_file or run_command, summarize what you changed. "
                "For software creation tasks, write complete runnable implementations — "
                "never placeholders or TODO-only stubs."
                + schema
            )
        if role_id == "reviewer":
            return (
                "\n\nReview the existing discussion only. Do not generate full HTML, "
                "PHP, or complete implementations. Give brief, actionable feedback. "
                "VERDICT must be fail when deliverables are placeholders or missing "
                "real implementation."
                + schema
            )
        if role_id in {"software_tester", "security"}:
            return (
                "\n\nAnalyze and report findings only. Do not replace the Developer "
                "by outputting full implementations. When run_command is available, "
                "run `python -m py_compile` on Python deliverables and include the "
                "result in FINDINGS."
                + schema
            )
        if role_id == "project_manager":
            return schema
        return schema

    async def _maybe_fix_verdict_failure(
        self,
        *,
        chat_id: str,
        role: AgentRole,
        content: str,
        round_num: int,
        user_content: str,
        transcript: list[str],
        discussions: list[AgentMessage],
        memory_context: str,
        tools: ToolRegistry,
        memory_scope: str,
        on_event: Callable | None,
        intervention_queue: asyncio.Queue[str] | None,
        workspace_intent: WorkspaceIntent,
        path_context: str,
        task_state: TaskState | None,
    ) -> str:
        """
        Run one bounded developer fix-and-reverify cycle after a failing verdict.

        A reviewer "VERDICT: fail" or a software_tester/security "SEVERITY: high"
        triggers an immediate developer turn (fed by the failure text already in
        the transcript) followed by a re-run of the same verifying role, bounded
        by MAX_VERDICT_RETRIES. Stops early if the re-verification repeats a
        prior message (unfixable task, e.g. a missing file) rather than looping.

        :param role: The role whose turn just produced a verdict
        :param content: That role's turn content
        :return: The latest content for this role's turn (post-fix when a retry ran)
        """
        verdict_failed = (
            role.id == "reviewer" and parse_reviewer_verdict(content) == "fail"
        ) or (
            role.id in {"software_tester", "security"}
            and parse_tester_severity(content) == "high"
        )
        if not (verdict_failed and task_state is not None):
            return content
        if increment_verdict_retry(task_state, role.id) > MAX_VERDICT_RETRIES:
            return content
        developer_role = role_registry.get_role("developer")
        if developer_role is None:
            return content

        transcript.append(
            f"System: {role.name} found blocking issues above. "
            "Developer, fix them directly before continuing."
        )
        latest_content = content
        for fix_role in (developer_role, role):
            fix_content, fix_routing, fix_discussion = await self._run_multi_role_turn(
                chat_id=chat_id,
                role=fix_role,
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
            if discussion_entry_is_repeat(fix_role.name, fix_content, transcript):
                break
            discussions.append(fix_discussion)
            transcript.append(f"{fix_role.name}: {fix_content}")
            if on_event:
                await on_event({
                    "type": "agent_message",
                    "discussion": fix_discussion.model_dump(mode="json"),
                    "routing": fix_routing,
                })
            latest_content = fix_content
        return latest_content

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
        needs_tools = intent.requires_tools or self._prompt_needs_tools(
            user_content,
            role.id,
        )
        prompt = self._build_multi_prompt(
            role,
            round_num,
            user_content,
            transcript,
            workspace_intent=intent,
            task_state=task_state,
        )
        tools_enabled = (
            (role.id in self.FULL_TOOL_ROLES and needs_tools and (
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
            user_content,
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
        if is_clarification_pending(content):
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
        if (
            task_state
            and intent.requires_tools
            and self._is_weak_discussion_content(content)
            and role.id != "project_manager"
            and not check_completion(task_state).complete
        ):
            retries = increment_weak_retry(task_state, role.id)
            if (
                retries >= MAX_WEAK_RETRIES
                and not should_skip_clarification_escalation(task_state, intent)
            ):
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


    def _no_action_system_prompt(self, category: ActionCategory) -> str:
        """
        Build the PM system prompt for a no-action gate category.

        :param category: Action gate category
        :return: System prompt text
        """
        return _NO_ACTION_SYSTEM_PROMPTS.get(
            category,
            _NO_ACTION_SYSTEM_PROMPTS[ActionCategory.CONVERSATIONAL],
        )

    async def _run_no_action_multi(
        self,
        chat_id: str,
        user_content: str,
        memory_context: str,
        effective_strategy: ExecutionStrategy,
        on_event: Callable | None,
        intervention_queue: asyncio.Queue[str] | None = None,
        action_gate: ActionRequirementResult | None = None,
    ) -> OrchestrationResponse:
        """
        Reply without full multi-agent tool orchestration when the action gate skips work.

        :param chat_id: Chat session ID
        :param user_content: Original user message
        :param memory_context: Persistent memory context
        :param effective_strategy: Resolved execution strategy
        :param on_event: Optional WebSocket event callback
        :param intervention_queue: Optional live user input queue
        :param action_gate: Gate decision driving prompt and metadata
        :return: Orchestration response with a single PM reply
        """
        gate = action_gate or analyze_action_requirement(
            user_content,
            mode=OrchestrationMode.MULTI,
        )
        pm = role_registry.get_role("project_manager")
        pm_name = pm.name if pm else "Project Manager"
        pm_id = pm.id if pm else "project_manager"

        await self._ensure_not_cancelled()
        llm, routing = await self._resolve_llm(user_content, role_id=pm_id, mode_single=False)

        if on_event:
            await on_event({
                "type": "agent_start",
                "agent_id": pm_id,
                "agent_name": pm_name,
                "round": 1,
            })
            await on_event({
                "type": "model_selected",
                "agent_id": pm_id,
                "agent_name": pm_name,
                "routing": routing,
            })

        system = self._no_action_system_prompt(gate.category)
        if self._ambient_context:
            system += f"\n\n{self._ambient_context}"
        if memory_context:
            system += f"\n\n{memory_context}"

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
            content, model_used, _tool_calls, _is_error = await self._stream_llm_complete(
                llm,
                messages,
                on_event,
            )
            if intervention_queue is None or intervention_queue.empty():
                break
            messages.append({"role": "assistant", "content": content})

        routing["model"] = model_used
        await self._emit_agent_end(on_event, pm_id, pm_name, round_num=1)

        discussion = AgentMessage(
            from_agent=pm_name,
            to_agent="team",
            content=content,
            timestamp=datetime.now(timezone.utc),
        )
        if on_event:
            await on_event({
                "type": "agent_message",
                "discussion": discussion.model_dump(mode="json"),
                "routing": routing,
            })

        gate_metadata = {
            "requires_action": gate.requires_action,
            "category": gate.category.value,
            "reason": gate.reason,
        }
        metadata: dict[str, Any] = {
            "routing": routing,
            "no_action_multi": True,
            "action_gate": gate_metadata,
        }
        if gate.category == ActionCategory.CONVERSATIONAL:
            metadata["conversational_multi"] = True

        msg = await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            content,
            metadata=metadata,
        )
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=[msg],
            agent_discussions=[discussion],
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
        )


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
            role_ids = ["project_manager", "architect", "developer", "reviewer"]

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
        action_gate = analyze_action_requirement(
            user_content,
            intent=workspace_intent,
            mode=OrchestrationMode.MULTI,
        )
        if not action_gate.requires_action:
            if on_event:
                await on_event({
                    "type": "action_gate_decision",
                    "requires_action": action_gate.requires_action,
                    "category": action_gate.category.value,
                    "reason": action_gate.reason,
                })
            return await self._run_no_action_multi(
                chat_id=chat_id,
                user_content=user_content,
                memory_context=memory_context,
                effective_strategy=effective_strategy,
                on_event=on_event,
                intervention_queue=intervention_queue,
                action_gate=action_gate,
            )
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
        pipeline_summary, prefetched_reads, pipeline_paused = await self._execute_workspace_agenda_pipeline(
            chat_id,
            user_content,
            workspace_intent,
            task_state,
            on_event,
            prefetched_reads,
        )
        if pipeline_paused:
            return OrchestrationResponse(
                chat_id=chat_id,
                messages=outputs,
                agent_discussions=discussions,
                pending_approvals=approval_manager.list_pending(chat_id),
                effective_execution_strategy=effective_strategy,
            )
        if pipeline_summary:
            transcript.append(f"System: {pipeline_summary}")
        compound_block = format_compound_plan_block(
            build_compound_plan(user_content, workspace_intent),
        )
        if compound_block:
            transcript.append(compound_block)

        agenda = build_workspace_agenda(user_content, workspace_intent)
        skip_discussion_loop = (
            task_state.task_type == WorkspaceTaskType.WORKFLOW
            and (len(agenda) >= 2 or is_compound_request(user_content))
        )
        if skip_discussion_loop:
            transcript.append(
                "System: Deterministic workspace agenda pipeline completed; "
                "skipping multi-agent discussion rounds.",
            )

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
        discussion_complete = skip_discussion_loop

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

                        content = await self._maybe_fix_verdict_failure(
                            chat_id=chat_id,
                            role=batch_role,
                            content=content,
                            round_num=round_num,
                            user_content=user_content,
                            transcript=transcript,
                            discussions=discussions,
                            memory_context=memory_context,
                            tools=tools,
                            memory_scope=memory_scope,
                            on_event=on_event,
                            intervention_queue=None,
                            workspace_intent=workspace_intent,
                            path_context=path_context,
                            task_state=task_state,
                        )

                        if is_clarification_pending(content):
                            return await self._build_clarification_pause_response(
                                chat_id,
                                effective_strategy,
                                outputs=outputs,
                                discussions=discussions,
                            )

                        if content.startswith("[ASK_USER]"):
                            return await self._build_user_input_response(
                                chat_id=chat_id,
                                role=batch_role,
                                content=content,
                                outputs=outputs,
                                discussions=discussions,
                                effective_strategy=effective_strategy,
                                user_content=user_content,
                                task_state=task_state,
                                workspace_intent=workspace_intent,
                                on_event=on_event,
                                role_ids=role_ids,
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

                content = await self._maybe_fix_verdict_failure(
                    chat_id=chat_id,
                    role=role,
                    content=content,
                    round_num=round_num,
                    user_content=user_content,
                    transcript=transcript,
                    discussions=discussions,
                    memory_context=memory_context,
                    tools=tools,
                    memory_scope=memory_scope,
                    on_event=on_event,
                    intervention_queue=intervention_queue,
                    workspace_intent=workspace_intent,
                    path_context=path_context,
                    task_state=task_state,
                )

                if is_clarification_pending(content):
                    return await self._build_clarification_pause_response(
                        chat_id,
                        effective_strategy,
                        outputs=outputs,
                        discussions=discussions,
                    )

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
                        user_content=user_content,
                        task_state=task_state,
                        workspace_intent=workspace_intent,
                        on_event=on_event,
                        role_ids=role_ids,
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
            retry_summary, prefetched_reads, retry_paused = await self._execute_workspace_agenda_pipeline(
                chat_id,
                user_content,
                workspace_intent,
                task_state,
                on_event,
                prefetched_reads,
            )
            if retry_paused:
                return OrchestrationResponse(
                    chat_id=chat_id,
                    messages=outputs,
                    agent_discussions=discussions,
                    pending_approvals=approval_manager.list_pending(chat_id),
                    effective_execution_strategy=effective_strategy,
                )
            if retry_summary:
                transcript.append(f"System: {retry_summary}")
            completion = check_completion(task_state)
            if (
                not completion.complete
                and completion.missing
                and not approval_manager.list_pending(chat_id)
            ):
                return await self._request_agent_clarification(
                    chat_id=chat_id,
                    kind=ClarificationKind.WORKFLOW_INCOMPLETE,
                    question=(
                        f"The workflow could not be completed: {completion.reason} "
                        f"Missing: {', '.join(completion.missing)}."
                    ),
                    role=None,
                    user_content=user_content,
                    outputs=outputs,
                    discussions=discussions,
                    effective_strategy=effective_strategy,
                    task_state=task_state,
                    workspace_intent=workspace_intent,
                    on_event=on_event,
                    context={
                        "reason": completion.reason,
                        "missing": completion.missing,
                    },
                    role_ids=role_ids,
                )
        else:
            prefetched_reads = await self._refresh_reads_after_writes(
                chat_id,
                user_content,
                workspace_intent,
                task_state,
                on_event,
                prefetched_reads,
            )

        placeholder_paths = collect_placeholder_implementation_paths(
            user_content,
            workspace_intent,
            collect_required_write_paths(task_state),
        )
        non_runnable_paths = collect_non_runnable_implementation_paths(
            user_content,
            workspace_intent,
            collect_required_write_paths(task_state),
        )
        rematerialize_paths = list(dict.fromkeys(placeholder_paths + non_runnable_paths))
        if rematerialize_paths:
            rematerialized = await self._materialize_missing_files(
                user_content,
                rematerialize_paths,
                role_id="developer",
            )
            if rematerialized:
                transcript.append(f"Developer: {rematerialized}")
                self._seed_created_write_facts(
                    task_state,
                    user_content,
                    workspace_intent,
                    agent_id="developer",
                    round_num=max_multi_rounds,
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

