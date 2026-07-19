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
from agentforge.agents.user_clarification import (
    ClarificationKind,
    clarification_pending_marker,
    request_clarification,
    should_skip_clarification_escalation,
)
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
    OrchestrationResumeState,
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


class ToolLoopMixin:
    """Mixin for AgentOrchestrator tool_loop."""

    async def _agent_loop(
        self,
        chat_id: str,
        agent_id: str,
        agent_name: str,
        messages: list[dict],
        tools: ToolRegistry,
        memory_scope: str,
        on_event: Callable | None,
        user_content: str = "",
        role_id: str | None = None,
        mode_single: bool = False,
        mode_multi: bool = False,
        intervention_queue: asyncio.Queue[str] | None = None,
        workspace_intent: WorkspaceIntent | None = None,
        task_state: TaskState | None = None,
        round_num: int = 0,
    ) -> tuple[str, dict]:
        """Run LLM with tool calling loop."""
        llm, routing = await self._resolve_llm(user_content, role_id, mode_single)
        intent = workspace_intent or detect_workspace_intent(user_content)
        return await self._run_agent_tool_loop(
            llm=llm,
            routing=routing,
            chat_id=chat_id,
            agent_id=agent_id,
            agent_name=agent_name,
            role_id=role_id,
            user_content=user_content,
            mode_single=mode_single,
            mode_multi=mode_multi,
            messages=messages,
            tools=tools,
            memory_scope=memory_scope,
            on_event=on_event,
            intervention_queue=intervention_queue,
            workspace_intent=intent,
            task_state=task_state,
            round_num=round_num,
        )


    async def _run_agent_tool_loop(
        self,
        llm: LLMProvider,
        routing: dict,
        chat_id: str,
        agent_id: str,
        agent_name: str,
        role_id: str | None,
        user_content: str,
        mode_single: bool,
        mode_multi: bool,
        messages: list[dict],
        tools: ToolRegistry,
        memory_scope: str,
        on_event: Callable | None,
        intervention_queue: asyncio.Queue[str] | None = None,
        workspace_intent: WorkspaceIntent | None = None,
        task_state: TaskState | None = None,
        round_num: int = 0,
    ) -> tuple[str, dict]:
        """
        Execute or continue an agent tool-calling loop.

        :param llm: Resolved LLM provider instance
        :param routing: Routing metadata
        :param chat_id: Chat session ID
        :param agent_id: Agent role identifier
        :param agent_name: Agent display name
        :param role_id: Effective role ID
        :param user_content: Original user prompt
        :param mode_single: Whether single-agent mode is active
        :param mode_multi: Whether multi-agent mode is active
        :param messages: Conversation messages for this agent
        :param tools: Role-specific tool registry
        :param memory_scope: Memory scope label
        :param on_event: Optional WebSocket event callback
        :param intervention_queue: Optional live user input queue
        :param workspace_intent: Parsed workspace file/command intent
        :return: Agent content and routing metadata
        """
        tool_schemas = tools.schemas() if tools.schemas() else None
        intent = workspace_intent or detect_workspace_intent(user_content)
        code_output_nudges = 0
        tool_summaries: list[str] = []
        tool_round_limit = self._effective_tool_round_limit(
            role_id,
            mode_single,
            mode_multi,
            workspace_intent=intent,
        )

        async def approval_cb(action_type: str, description: str, payload: dict) -> str:
            return await approval_manager.request(chat_id, action_type, description, payload)

        if on_event:
            await on_event({
                "type": "model_selected",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "routing": routing,
            })

        audit_token = audit_context.set(
            CommandAuditContext(
                chat_id=chat_id,
                agent_id=agent_id,
                agent_name=agent_name,
                on_event=on_event,
            ),
        )

        try:
            for _ in range(tool_round_limit):
                await self._ensure_not_cancelled()
                await self._append_interventions_to_messages(
                    messages,
                    intervention_queue,
                    on_event,
                )
                if not tool_schemas:
                    content, model_used = await self._stream_llm_complete(
                        llm,
                        messages,
                        on_event,
                    )
                    routing["model"] = model_used
                    return self._finalize_agent_content(content, tool_summaries), routing

                result = await llm.complete(
                    messages,
                    tools=tool_schemas,
                    max_tokens=512 if mode_multi and role_id != "developer" else None,
                )
                if result.get("error"):
                    routing["model"] = result.get("model", routing.get("model"))
                    return (
                        self._finalize_agent_content(
                            result.get("content") or "LLM request failed.",
                            tool_summaries,
                        ),
                        routing,
                    )
                tool_calls = result.get("tool_calls") or []
                if not tool_calls:
                    tool_calls = self._parse_content_tool_calls(result.get("content") or "")

                if not tool_calls:
                    content = result.get("content") or ""
                    needs_tool_nudge = (
                        tool_schemas
                        and code_output_nudges < 2
                        and (
                            self._looks_like_code_only_output(content)
                            or self._is_weak_discussion_content(content)
                        )
                    )
                    if needs_tool_nudge and (
                        intent.wants_file_creation or intent.wants_file_read
                    ):
                        messages.append({"role": "assistant", "content": content})
                        if intent.wants_file_read:
                            nudge = (
                                self.READ_EMPTY_RESPONSE_NUDGE
                                if self._is_weak_discussion_content(content)
                                else self.READ_TOOL_USE_NUDGE
                            )
                        else:
                            nudge = (
                                self.EMPTY_RESPONSE_NUDGE
                                if self._is_weak_discussion_content(content)
                                else self.TOOL_USE_NUDGE
                            )
                        messages.append({"role": "user", "content": nudge})
                        code_output_nudges += 1
                        if task_state and role_id:
                            increment_weak_retry(task_state, role_id)
                        continue

                    routing["model"] = result.get("model", routing.get("model"))
                    finalized = self._finalize_agent_content(content, tool_summaries)
                    if (
                        task_state
                        and intent.requires_tools
                        and self._is_weak_discussion_content(finalized)
                        and role_id
                        and not check_completion(task_state).complete
                        and task_state.weak_retry_counts.get(role_id, 0) >= MAX_WEAK_RETRIES
                        and not should_skip_clarification_escalation(task_state, intent)
                    ):
                        completion = check_completion(task_state)
                        question = build_escalation_message(
                            task_state,
                            role_id,
                            reason=completion.reason,
                        )
                        resume_state = OrchestrationResumeState(
                            kind=ClarificationKind.AGENT_BLOCKED.value,
                            chat_id=chat_id,
                            user_content=user_content,
                            context={
                                "role_id": role_id,
                                "reason": completion.reason,
                                "missing": completion.missing,
                            },
                            task_state_snapshot=task_state.to_persisted_payload(),
                            intent=asdict(intent),
                            source_role_id=role_id,
                            source_role_name=agent_name,
                            question_text=question,
                            mode="single" if mode_single else "multi",
                        )
                        await request_clarification(
                            chat_id,
                            ClarificationKind.AGENT_BLOCKED,
                            question,
                            None,
                            resume_state,
                            on_event,
                        )
                        return clarification_pending_marker(), routing
                    return finalized, routing

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": result.get("content") or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_msg)

                unknown_tool_count = 0
                for tc in tool_calls:
                    if on_event:
                        await on_event({
                            "type": "tool_call",
                            "agent_id": agent_id,
                            "tool": tc["name"],
                            "arguments": tc["arguments"],
                        })

                    tool_result = await self._execute_tool_call(
                        chat_id=chat_id,
                        tools=tools,
                        tool_call=tc,
                        approval_cb=approval_cb,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        on_event=on_event,
                    )

                    record_tool_result_as_fact(
                        task_state,
                        tc["name"],
                        tc["arguments"],
                        tool_result.output,
                        tool_result.success,
                        role_id or agent_id,
                        round_num,
                    )

                    if tool_result.success:
                        summary = self._summarize_tool_call(
                            tc["name"],
                            tc["arguments"],
                            tool_result.output,
                        )
                        if summary:
                            tool_summaries.append(summary)

                    if (
                        not tool_result.success
                        and tool_result.output.startswith("Unknown tool:")
                    ):
                        unknown_tool_count += 1

                    if tool_result.requires_approval:
                        approval_id = tool_result.approval_id
                        if approval_id:
                            approval_manager.set_resume_state(
                                approval_id,
                                ApprovalResumeState(
                                    chat_id=chat_id,
                                    agent_id=agent_id,
                                    agent_name=agent_name,
                                    role_id=role_id or self.DEFAULT_SINGLE_ROLE,
                                    user_content=user_content,
                                    mode_single=mode_single,
                                    memory_scope=memory_scope,
                                    routing=copy.deepcopy(routing),
                                    messages=copy.deepcopy(messages),
                                    tool_call_id=tc["id"],
                                ),
                            )
                        command_preview: str
                        try:
                            parsed_arguments = json.loads(tc["arguments"])
                            command_preview = str(
                                parsed_arguments.get("command", tc["arguments"])
                            )
                        except json.JSONDecodeError:
                            command_preview = tc["arguments"]
                        return (
                            f"I need your approval to run: "
                            f"{command_preview}. "
                            f"Please approve or deny in the approvals panel."
                        ), routing

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result.output,
                    })

                    if on_event:
                        await on_event({
                            "type": "tool_result",
                            "agent_id": agent_id,
                            "tool": tc["name"],
                            "success": tool_result.success,
                            "output": tool_result.output[:500],
                        })

                await emit_task_board_update(on_event, task_state)

                if unknown_tool_count == len(tool_calls):
                    messages.append({
                        "role": "user",
                        "content": (
                            "Those tools are not available. "
                            "Answer the user directly in plain text without calling tools."
                        ),
                    })
                    fallback = await llm.complete(messages, tools=None)
                    if fallback.get("error"):
                        routing["model"] = fallback.get("model", routing.get("model"))
                        return fallback.get("content") or "LLM request failed.", routing
                    routing["model"] = fallback.get("model", routing.get("model"))
                    return (
                        self._finalize_agent_content(
                            fallback.get("content") or "",
                            tool_summaries,
                        ),
                        routing,
                )

        finally:
            audit_context.reset(audit_token)

        recovery_content = await self._recover_after_tool_limit(
            chat_id=chat_id,
            llm=llm,
            messages=messages,
            routing=routing,
            tool_summaries=tool_summaries,
            intent=intent,
            user_content=user_content,
            role_id=role_id,
            on_event=on_event,
        )
        return recovery_content, routing

    async def _recover_after_tool_limit(
        self,
        chat_id: str,
        llm: LLMProvider,
        messages: list[dict],
        routing: dict,
        tool_summaries: list[str],
        intent: WorkspaceIntent,
        user_content: str,
        role_id: str | None,
        on_event: Callable | None = None,
    ) -> str:
        """
        Recover gracefully when an agent exhausts its tool iteration budget.

        :param llm: Resolved LLM provider instance
        :param messages: Conversation messages built so far
        :param routing: Routing metadata to update with the recovery model
        :param tool_summaries: Successful tool actions collected in the loop
        :param intent: Parsed workspace intent
        :param user_content: Original user request
        :param role_id: Effective role identifier
        :return: User-facing recovery message
        """
        summary = ""
        if tool_summaries:
            summary = "Completed workspace actions:\n- " + "\n- ".join(tool_summaries)

        still_missing = missing_requested_files(user_content, intent)
        if still_missing:
            fallback = await self._guarantee_workspace_deliverables(
                chat_id,
                user_content,
                intent,
                role_id=role_id or self.DEFAULT_SINGLE_ROLE,
                on_event=on_event,
            )
            if fallback:
                if summary:
                    return f"{summary}\n\n{fallback}"
                return fallback

        recovery_messages = list(messages)
        recovery_messages.append({
            "role": "user",
            "content": (
                "The tool limit was reached. Do not call any more tools. "
                "Summarize what you accomplished and what remains in plain language."
            ),
        })
        fallback = await llm.complete(recovery_messages, tools=None, max_tokens=768)
        routing["model"] = fallback.get("model", routing.get("model"))
        answer = strip_code_fences(fallback.get("content") or "").strip()
        if answer and not answer.startswith("LLM error"):
            if summary:
                return f"{summary}\n\n{answer}"
            return answer

        if summary:
            return (
                f"{summary}\n\n"
                "I reached the tool limit before finishing completely. "
                "The completed actions above are still valid."
            )
        return (
            "I reached the tool limit before finishing completely. "
            "Please retry with a smaller request or use Single Agent mode."
        )

    async def _execute_tool_call(
        self,
        chat_id: str,
        tools: ToolRegistry,
        tool_call: dict[str, Any],
        approval_cb: Callable[[str, str, dict], Awaitable[str]],
        agent_id: str,
        agent_name: str,
        on_event: Callable | None,
    ) -> ToolCallResult:
        """
        Execute one tool call through the central command audit gateway when needed.

        :param chat_id: Chat session ID
        :param tools: Tool registry
        :param tool_call: Tool call payload from the LLM
        :param approval_cb: Approval callback for shell commands
        :param agent_id: Agent role identifier
        :param agent_name: Agent display name
        :param on_event: Optional WebSocket callback
        :return: Tool execution result
        """
        if tool_call.get("name") == "run_command":
            command, cwd = self._parse_run_command_arguments(tool_call.get("arguments", ""))
            if not command:
                return ToolCallResult(
                    tool="run_command",
                    success=False,
                    output="Empty shell command.",
                )
            return await execute_shell_command(
                chat_id,
                command=command,
                cwd=cwd,
                agent_id=agent_id,
                agent_name=agent_name,
                approval_callback=approval_cb,
                on_event=on_event,
            )
        return await tools.execute(tool_call["name"], tool_call["arguments"])


    async def _resume_after_approval(
        self,
        state: ApprovalResumeState,
        command_output: str,
    ) -> tuple[str, dict]:
        """
        Resume an interrupted agent loop after an approved command.

        :param state: Stored continuation state
        :param command_output: Command stdout/stderr output
        :return: Agent content and routing metadata after resume
        """
        chat_id = state.chat_id
        role_id = state.role_id or self.DEFAULT_SINGLE_ROLE
        memory_scope = state.memory_scope
        agent_id = state.agent_id or role_id
        agent_name = state.agent_name or role_id
        user_content = state.user_content
        mode_single = state.mode_single
        routing = copy.deepcopy(state.routing)
        messages = copy.deepcopy(state.messages)
        tool_call_id = state.tool_call_id

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": command_output,
        })
        model_name = str(routing.get("model") or self.base_llm_config.model)
        llm = self.llm.with_model(model_name)
        full_tools = self._build_tools(chat_id, memory_scope)
        agent_tools = self._tools_for_role(role_id, chat_id, memory_scope, full_tools)

        return await self._run_agent_tool_loop(
            llm=llm,
            routing=routing,
            chat_id=chat_id,
            agent_id=agent_id,
            agent_name=agent_name,
            role_id=role_id,
            user_content=user_content,
            mode_single=mode_single,
            mode_multi=False,
            messages=messages,
            tools=agent_tools,
            memory_scope=memory_scope,
            on_event=None,
            intervention_queue=None,
        )


    async def execute_approved_command(
        self,
        chat_id: str,
        approval_id: str,
        response: ApprovalResponse,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> MessageResponse | None:
        """Execute a previously approved shell command or resume a user choice."""
        pending = approval_manager.list_pending(chat_id)
        target = next((p for p in pending if p.id == approval_id), None)
        if target and target.action_type == "user_choice":
            await approval_manager.respond(approval_id, response)
            try:
                resume_state = approval_manager.pop_resume_state(approval_id)
            except ValueError:
                return await conversation_store.add_message(
                    chat_id,
                    MessageRole.ASSISTANT,
                    (
                        "Your choice was recorded, but I could not resume the workflow because "
                        "the continuation state is invalid. Please resend your last request."
                    ),
                    metadata={
                        "approval_id": approval_id,
                        "resume_error": True,
                        "resume_error_type": "invalid_state",
                    },
                )
            if not resume_state:
                return None
            from agentforge.models.schemas import AgendaResumeState

            if isinstance(resume_state, AgendaResumeState):
                return await self._resume_agenda_after_user_choice(
                    resume_state,
                    response,
                    on_event=on_event,
                )
            if isinstance(resume_state, OrchestrationResumeState):
                return await self._resume_orchestration_after_clarification(
                    resume_state,
                    response,
                )
            return await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                "Your choice was recorded, but no agenda continuation state was available.",
                metadata={"approval_id": approval_id, "resume_error": True},
            )
        if not target or not response.approved:
            if target and target.action_type == "command":
                command = str(target.payload.get("command", "")).strip()
                denied = await record_command(
                    chat_id,
                    command=command,
                    cwd=target.payload.get("cwd"),
                    status="denied",
                    success=False,
                    exit_code=None,
                    output="Command denied by user.",
                    agent_id=None,
                    agent_name=None,
                    approval_id=approval_id,
                )
                await approval_manager.respond(approval_id, response)
                approval_manager.pop_resume_state(approval_id)
                return denied
            await approval_manager.respond(approval_id, response)
            approval_manager.pop_resume_state(approval_id)
            return None

        await approval_manager.respond(approval_id, response)
        command = str(target.payload.get("command", "")).strip()
        cwd_value = target.payload.get("cwd")
        tool_msg = await execute_approved_shell_command(
            chat_id,
            command=command,
            cwd=str(cwd_value).strip() if cwd_value else None,
            approval_id=approval_id,
            on_event=None,
        )
        formatted_output = tool_msg.content
        try:
            resume_state = approval_manager.pop_resume_state(approval_id)
        except ValueError:
            return await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                (
                    "The command was approved and executed, but I could not resume the previous "
                    "agent flow because the continuation state is invalid. "
                    "Please resend your last request."
                ),
                metadata={
                    "approval_id": approval_id,
                    "resume_error": True,
                    "resume_error_type": "invalid_state",
                },
            )
        if not resume_state:
            return tool_msg

        try:
            resumed_content, resumed_routing = await self._resume_after_approval(
                resume_state,
                formatted_output,
            )
        except Exception:
            return await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                (
                    "The command was approved and executed, but resuming the previous agent flow "
                    "failed unexpectedly. Please resend your last request."
                ),
                metadata={
                    "approval_id": approval_id,
                    "resume_error": True,
                    "resume_error_type": "resume_failed",
                },
            )
        return await conversation_store.add_message(
            chat_id,
            MessageRole.ASSISTANT,
            resumed_content,
            agent_id=resume_state.agent_id or None,
            agent_name=resume_state.agent_name or None,
            metadata={
                "routing": resumed_routing,
                "resumed_from_approval": True,
                "approval_id": approval_id,
            },
        )
