"""Agent orchestration for single and multi-agent modes."""

import asyncio
import copy
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.role_registry import role_registry
from agentforge.agents.role_router import AUTO_ROLE, resolve_single_role
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
    load_task_board_memory,
    MAX_REPETITION_STALLS,
    emit_task_board_update,
    persist_task_board,
    record_tool_result_as_fact,
    seed_read_facts,
    seed_write_facts,
    seed_edit_facts,
    seed_list_directory_facts,
    MAX_WEAK_RETRIES,
)
from agentforge.agents.prompt_normalizer import (
    PromptNormalizationResult,
    format_prompt_normalization_block,
    normalize_user_prompt,
    prompt_normalization_metadata,
)
from agentforge.agents.workspace_intent import WorkspaceIntent, detect_workspace_intent
from agentforge.agents.workspace_path_resolver import (
    activate_path_resolution_context,
    build_path_resolution_context,
    deactivate_path_resolution_context,
)
from agentforge.agents.workspace_executor import (
    apply_file_text_replacement,
    build_deliverable_status_summary,
    build_implementation_prompt,
    build_materialization_prompt,
    build_read_context_block,
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
from agentforge.agents.workspace_scanner import build_workspace_path_context
from agentforge.config import settings
from agentforge.context import context_registry
from agentforge.llm.model_router import TaskType, model_router
from agentforge.llm.provider import LLMProvider
from agentforge.memory.store import memory_store
from agentforge.models.schemas import (
    AgentMessage,
    AgentRole,
    ApprovalResponse,
    ApprovalResumeState,
    ChatMemorySettings,
    ChatUpdate,
    ExecutionStrategy,
    LLMConfig,
    MessageRole,
    MessageResponse,
    OrchestrationMode,
    OrchestrationResponse,
    ToolCallResult,
)
from agentforge.services.setup_service import run_readiness_check
from agentforge.storage.conversation_store import conversation_store
from agentforge.tools.registry import (
    EditFileTool,
    FileAnchorStore,
    ListDirectoryTool,
    ReadFileTool,
    RememberTool,
    SearchFilesTool,
    ShellTool,
    ToolRegistry,
    WebSearchTool,
    WriteFileTool,
    _resolve_path,
)
from agentforge.services.command_audit import (
    CommandAuditContext,
    audit_context,
    command_audit_scope,
    execute_approved_shell_command,
    execute_shell_command,
    record_command,
    serialize_shell_command_entry,
)


from agentforge.agents.orchestrator_mixins.clarification import ClarificationMixin
from agentforge.agents.orchestrator_mixins.deliverables import DeliverablesMixin
from agentforge.agents.orchestrator_mixins.grill import GrillMixin
from agentforge.agents.orchestrator_mixins.multi_agent import MultiAgentMixin
from agentforge.agents.orchestrator_mixins.parsing import ParsingMixin
from agentforge.agents.orchestrator_mixins.single_agent import SingleAgentMixin
from agentforge.agents.orchestrator_mixins.tool_loop import ToolLoopMixin
def _effective_chat_memory(memory: ChatMemorySettings) -> ChatMemorySettings:
    """
    Apply global defaults for per-chat memory settings.

    :param memory: Stored chat memory settings
    :return: Effective memory settings for orchestration
    """
    return memory.model_copy(
        update={
            "memory_tokens": settings.default_memory_tokens,
            "memory_scope": "chat",
        }
    )


class AgentOrchestrator(
    ParsingMixin,
    DeliverablesMixin,
    ClarificationMixin,
    GrillMixin,
    ToolLoopMixin,
    MultiAgentMixin,
    SingleAgentMixin,
):
    """Coordinates single-agent coding and multi-agent discussions."""

    FULL_TOOL_ROLES = frozenset({"developer", "architect", "devops"})
    READ_EXECUTE_TOOL_ROLES = frozenset({"reviewer", "software_tester", "security"})
    PARALLEL_RESEARCH_ROLES = frozenset({"researcher"})
    DOCS_TOOL_ROLES = frozenset({"documentation"})
    AUTO_ROLE = AUTO_ROLE
    DEFAULT_SINGLE_ROLE = "developer"
    QUICK_SYSTEM_PROMPT = (
        "You are a helpful assistant. Reply naturally and concisely."
    )
    TOOL_INTENT = re.compile(
        r"\b("
        r"file|read|write|edit|create|folder|directory|list|run|execute|command|"
        r"shell|terminal|git|npm|pip|install|search|web|browse|remember|memory|"
        r"debug|fix|implement|refactor|test|deploy|build|compile|sql|database|"
        r"schema|code|function|class|bug|script|python|php|typescript|javascript|"
        r"html|css|projekt|project|workspace"
        r")\b",
        re.IGNORECASE,
    )

    def __init__(self, llm_config: LLMConfig | None = None) -> None:
        """Initialize orchestrator with LLM configuration."""
        self.base_llm_config = llm_config or LLMConfig(
            model=settings.default_model,
            ollama_base_url=settings.ollama_base_url,
            openai_api_key=settings.openai_api_key,
            openai_api_base=settings.openai_api_base,
            anthropic_api_key=settings.anthropic_api_key,
            gemini_api_key=settings.gemini_api_key,
            groq_api_key=settings.groq_api_key,
            mistral_api_key=settings.mistral_api_key,
            auto_routing=settings.llm_auto_routing,
        )
        self.llm = LLMProvider(self.base_llm_config)
        self.max_tool_rounds = 8
        self._ambient_context = ""
        self.max_multi_rounds = settings.multi_agent_max_rounds

    @staticmethod
    def _uses_local_ollama() -> bool:
        """
        Return True when orchestration is configured for local/remote Ollama.

        :return: Whether the active model routing targets Ollama
        """
        model = settings.override_model.strip() or settings.default_model
        return model.startswith("ollama/")

    def _resolve_multi_rounds(self) -> int:
        """
        Resolve discussion rounds for multi-agent mode.

        :return: Number of multi-agent rounds to execute
        """
        if self._uses_local_ollama():
            return max(1, settings.multi_agent_max_rounds_ollama)
        return self.max_multi_rounds

    def _tools_for_multi_role(
        self,
        role_id: str,
        chat_id: str,
        memory_scope: str,
        full_tools: ToolRegistry,
        workspace_intent: WorkspaceIntent,
    ) -> ToolRegistry:
        """
        Resolve lightweight tool access for one multi-agent role.

        :param role_id: Agent role identifier
        :param chat_id: Chat session ID
        :param memory_scope: Memory scope label
        :param full_tools: Pre-built full workspace tool registry
        :param workspace_intent: Parsed workspace intent
        :return: Tool registry appropriate for this multi-agent turn
        """
        if role_id == "project_manager":
            return ToolRegistry()
        if workspace_intent.wants_file_read and not workspace_intent.wants_file_creation:
            if role_id in self.READ_EXECUTE_TOOL_ROLES or role_id == "developer":
                return self._build_read_execute_tools(chat_id, memory_scope)
            return ToolRegistry()
        if role_id == "reviewer":
            return ToolRegistry()
        if role_id == "developer":
            return self._tools_for_role(role_id, chat_id, memory_scope, full_tools)
        if role_id in self.FULL_TOOL_ROLES and workspace_intent.wants_file_creation:
            return self._tools_for_role(role_id, chat_id, memory_scope, full_tools)
        return ToolRegistry()

    def _effective_tool_round_limit(
        self,
        role_id: str | None,
        mode_single: bool,
        mode_multi: bool,
        workspace_intent: WorkspaceIntent | None = None,
    ) -> int:
        """
        Resolve the tool loop limit for the current agent turn.

        :param role_id: Effective role identifier
        :param mode_single: Whether single-agent mode is active
        :param mode_multi: Whether multi-agent mode is active
        :param workspace_intent: Parsed workspace intent
        :return: Maximum tool iterations for this turn
        """
        if mode_multi:
            if role_id == "developer":
                if workspace_intent and (
                    workspace_intent.wants_file_creation
                    or workspace_intent.wants_file_read
                ):
                    return self.max_tool_rounds if workspace_intent.wants_file_creation else min(
                        self.max_tool_rounds,
                        6,
                    )
                return min(self.max_tool_rounds, 6)
            return 1
        if workspace_intent and workspace_intent.wants_file_creation:
            return self.max_tool_rounds
        return self.max_tool_rounds

    @staticmethod
    def _resolve_execution_strategy(
        mode: OrchestrationMode,
        requested: ExecutionStrategy,
    ) -> ExecutionStrategy:
        """
        Resolve the effective execution strategy for this orchestration run.

        PR2 enables hybrid execution for read-only specialist roles in
        multi-agent mode. "parallel" maps to "hybrid" until full
        all-role parallel execution is supported.

        :param mode: Single or multi-agent mode
        :param requested: Requested strategy from chat settings
        :return: Effective strategy used by the orchestrator
        """
        if mode in (OrchestrationMode.SINGLE, OrchestrationMode.QUICK, OrchestrationMode.GRILL):
            return ExecutionStrategy.SERIAL
        if requested == ExecutionStrategy.AUTO:
            return ExecutionStrategy.HYBRID
        if requested == ExecutionStrategy.PARALLEL:
            return ExecutionStrategy.HYBRID
        return requested

    @classmethod
    def _is_parallel_role(cls, role_id: str) -> bool:
        """
        Check whether a role is safe for parallel execution.

        :param role_id: Agent role identifier
        :return: True when role is read-only specialist
        """
        return (
            role_id in cls.READ_EXECUTE_TOOL_ROLES
            or role_id in cls.PARALLEL_RESEARCH_ROLES
        )

    @staticmethod
    def _is_parallel_round(
        effective_strategy: ExecutionStrategy,
        round_num: int,
        max_rounds: int,
        workspace_intent: WorkspaceIntent | None = None,
    ) -> bool:
        """
        Decide whether the current round can run parallel specialist turns.

        :param effective_strategy: Resolved execution strategy
        :param round_num: Zero-based round index
        :param max_rounds: Total configured number of rounds
        :param workspace_intent: Parsed workspace intent for the user request
        :return: True when parallel specialist execution is enabled
        """
        if effective_strategy != ExecutionStrategy.HYBRID:
            return False
        if workspace_intent and (
            workspace_intent.wants_file_creation or workspace_intent.wants_file_read
        ):
            return False
        return round_num < max_rounds - 1

    async def _resolve_llm(
        self,
        user_content: str,
        role_id: str | None,
        mode_single: bool = False,
    ) -> tuple[LLMProvider, dict]:
        """Resolve LLM provider and routing metadata for a task."""
        task = model_router.detect_task(user_content, role_id, mode_single)
        routing = await model_router.resolve(task, fallback_model=self.base_llm_config.model)
        llm = self.llm.with_model(routing["model"])
        routing["role_id"] = role_id
        return llm, routing

    @classmethod
    def _prompt_needs_tools(cls, user_content: str, role_id: str) -> bool:
        """
        Decide whether workspace tools should be attached for a prompt.

        :param user_content: User message text
        :param role_id: Effective agent role identifier
        :return: True when tools are likely required
        """
        intent = detect_workspace_intent(user_content)
        if intent.requires_tools:
            return True
        if role_id in cls.FULL_TOOL_ROLES and intent.wants_file_creation:
            return True
        return cls.TOOL_INTENT.search(user_content) is not None

    @staticmethod
    def _order_roles_for_intent(
        roles: list[AgentRole],
        intent: WorkspaceIntent,
    ) -> list[AgentRole]:
        """
        Prioritize implementation roles when the user wants files on disk.

        :param roles: Selected agent roles
        :param intent: Parsed workspace intent
        :return: Reordered role list
        """
        if not intent.wants_file_creation and not intent.wants_file_read:
            return roles

        priority = {
            "developer": 0,
            "architect": 1,
            "devops": 2,
            "documentation": 3,
            "project_manager": 90,
        }
        return sorted(roles, key=lambda role: (priority.get(role.id, 50), role.id))

    @staticmethod
    def _should_skip_multi_role_turn(
        role: AgentRole,
        round_num: int,
        max_multi_rounds: int,
    ) -> bool:
        """
        Skip redundant coordinator turns in intermediate multi-agent rounds.

        :param role: Current role instance
        :param round_num: Zero-based round index
        :param max_multi_rounds: Total configured multi-agent rounds
        :return: True when the role turn should be skipped
        """
        if role.id == "project_manager" and 0 < round_num < max_multi_rounds - 1:
            return True
        return False

    def _build_tools(self, chat_id: str, memory_scope: str) -> ToolRegistry:
        """Build tool registry with chat-specific callbacks."""
        registry = ToolRegistry()

        async def approval_cb(action_type: str, description: str, payload: dict) -> str:
            return await approval_manager.request(chat_id, action_type, description, payload)

        async def memory_cb(scope: str, key: str, value: str) -> None:
            cid = chat_id if scope == "chat" else None
            await memory_store.set_entry(cid, scope, key, value)

        anchor_store = FileAnchorStore()
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(SearchFilesTool(anchor_store))
        registry.register(EditFileTool(anchor_store))
        registry.register(ListDirectoryTool())
        registry.register(ShellTool(approval_callback=approval_cb))
        registry.register(RememberTool(memory_callback=memory_cb))
        if settings.web_search_enabled:
            registry.register(WebSearchTool())
        return registry

    def _build_read_execute_tools(self, chat_id: str, memory_scope: str) -> ToolRegistry:
        """
        Build read and execute tools for review, QA, and security roles.

        :param chat_id: Chat session ID
        :param memory_scope: Memory scope label
        :return: Tool registry without write access
        """
        registry = ToolRegistry()

        async def approval_cb(action_type: str, description: str, payload: dict) -> str:
            return await approval_manager.request(chat_id, action_type, description, payload)

        async def memory_cb(scope: str, key: str, value: str) -> None:
            cid = chat_id if scope == "chat" else None
            await memory_store.set_entry(cid, scope, key, value)

        anchor_store = FileAnchorStore()
        registry.register(ReadFileTool())
        registry.register(SearchFilesTool(anchor_store))
        registry.register(ListDirectoryTool())
        registry.register(ShellTool(approval_callback=approval_cb))
        registry.register(RememberTool(memory_callback=memory_cb))
        if settings.web_search_enabled:
            registry.register(WebSearchTool())
        return registry

    def _build_docs_tools(self, chat_id: str, memory_scope: str) -> ToolRegistry:
        """
        Build documentation tools with file read/write but no shell.

        :param chat_id: Chat session ID
        :param memory_scope: Memory scope label
        :return: Tool registry for documentation work
        """
        registry = ToolRegistry()

        async def memory_cb(scope: str, key: str, value: str) -> None:
            cid = chat_id if scope == "chat" else None
            await memory_store.set_entry(cid, scope, key, value)

        anchor_store = FileAnchorStore()
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(SearchFilesTool(anchor_store))
        registry.register(EditFileTool(anchor_store))
        registry.register(ListDirectoryTool())
        registry.register(RememberTool(memory_callback=memory_cb))
        if settings.web_search_enabled:
            registry.register(WebSearchTool())
        return registry

    def _tools_for_role(
        self,
        role_id: str,
        chat_id: str,
        memory_scope: str,
        full_tools: ToolRegistry,
    ) -> ToolRegistry:
        """
        Resolve tool access for a software-development role.

        :param role_id: Agent role identifier
        :param chat_id: Chat session ID
        :param memory_scope: Memory scope label
        :param full_tools: Pre-built full workspace tool registry
        :return: Tool registry appropriate for the role
        """
        if role_id in self.FULL_TOOL_ROLES:
            return full_tools
        if role_id in self.READ_EXECUTE_TOOL_ROLES:
            return self._build_read_execute_tools(chat_id, memory_scope)
        if role_id in self.DOCS_TOOL_ROLES:
            return self._build_docs_tools(chat_id, memory_scope)
        return self._build_research_tools(chat_id, memory_scope)

    def _build_research_tools(self, chat_id: str, memory_scope: str) -> ToolRegistry:
        """
        Build lightweight tools for research-oriented roles.

        :param chat_id: Chat session ID
        :param memory_scope: Memory scope label
        :return: Tool registry with memory and web search
        """
        registry = ToolRegistry()

        async def memory_cb(scope: str, key: str, value: str) -> None:
            cid = chat_id if scope == "chat" else None
            await memory_store.set_entry(cid, scope, key, value)

        registry.register(RememberTool(memory_callback=memory_cb))
        if settings.web_search_enabled:
            registry.register(WebSearchTool())
        return registry

    async def _ensure_not_cancelled(self) -> None:
        """Yield control and abort when the orchestration task was cancelled."""
        await asyncio.sleep(0)
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise asyncio.CancelledError()

    async def _append_interventions_to_messages(
        self,
        messages: list[dict[str, Any]],
        intervention_queue: asyncio.Queue[str] | None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """
        Merge live user follow-ups into an in-flight single-agent conversation.

        :param messages: LLM message list for the active agent loop
        :param intervention_queue: Queue of user messages sent during orchestration
        :param on_event: Optional WebSocket event callback
        """
        if intervention_queue is None:
            return

        while True:
            try:
                content = intervention_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            messages.append({
                "role": "user",
                "content": f"User follow-up: {content}",
            })
            if on_event:
                await on_event({
                    "type": "user_intervention",
                    "content": content,
                })

    async def _collect_interventions(
        self,
        transcript: list[str],
        intervention_queue: asyncio.Queue[str] | None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """
        Drain live user messages into the multi-agent transcript.

        :param transcript: Running discussion transcript
        :param intervention_queue: Queue of user messages sent during orchestration
        :param on_event: Optional WebSocket event callback
        """
        if intervention_queue is None:
            return

        while True:
            try:
                content = intervention_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            line = f"User (live input): {content}"
            transcript.append(line)
            if on_event:
                await on_event({
                    "type": "user_intervention",
                    "content": content,
                })

    @staticmethod
    def _merge_context_blocks(*parts: str) -> str:
        """
        Join optional prompt context blocks.

        :param parts: Context fragments
        :return: Combined context or empty string
        """
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

    async def _build_user_input_response(
        self,
        chat_id: str,
        role: AgentRole,
        content: str,
        outputs: list[MessageResponse],
        discussions: list[AgentMessage],
        effective_strategy: ExecutionStrategy,
        *,
        user_content: str = "",
        task_state: TaskState | None = None,
        workspace_intent: WorkspaceIntent | None = None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        mode: OrchestrationMode = OrchestrationMode.MULTI,
        role_ids: list[str] | None = None,
    ) -> OrchestrationResponse:
        """
        Open a clarification dialog for [ASK_USER] agent output.

        :param chat_id: Chat session ID
        :param role: Requesting role
        :param content: Role output containing [ASK_USER]
        :param outputs: Output messages collected so far
        :param discussions: Agent discussions collected so far
        :param effective_strategy: Effective execution strategy for this run
        :param user_content: Original user prompt
        :param task_state: Active task board, if any
        :param workspace_intent: Parsed workspace intent, if any
        :param on_event: Optional WebSocket event callback
        :param mode: Orchestration mode for resume
        :param role_ids: Selected role IDs for resume
        :return: Orchestration response waiting for user choice
        """
        from agentforge.agents.user_clarification import ClarificationKind

        question = content.replace("[ASK_USER]", "").strip()
        return await self._request_agent_clarification(
            chat_id=chat_id,
            kind=ClarificationKind.AGENT_QUESTION,
            question=question,
            role=role,
            user_content=user_content,
            outputs=outputs,
            discussions=discussions,
            effective_strategy=effective_strategy,
            task_state=task_state,
            workspace_intent=workspace_intent,
            on_event=on_event,
            mode=mode,
            role_ids=role_ids,
        )

    async def run(
        self,
        chat_id: str,
        user_content: str,
        mode: OrchestrationMode,
        role_ids: list[str],
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        intervention_queue: asyncio.Queue[str] | None = None,
        record_user_message: bool = True,
        client_ip: str = "",
    ) -> OrchestrationResponse:
        """
        Execute orchestration for a user message.

        :param chat_id: Chat session ID
        :param user_content: User message text
        :param mode: Single or multi-agent mode
        :param role_ids: Selected role IDs
        :param on_event: Optional WebSocket event callback
        :param intervention_queue: Optional queue for live user input during orchestration
        :param record_user_message: Store the user message before orchestration
        :param client_ip: Optional client IP for location-aware context plugins
        :return: Orchestration result
        """
        chat = await conversation_store.get_chat(chat_id)
        requested_strategy = chat.execution_strategy
        effective_strategy = self._resolve_execution_strategy(mode, requested_strategy)
        memory_settings = _effective_chat_memory(chat.memory)
        memory_context = await memory_store.get_context(chat_id, memory_settings)
        tools = self._build_tools(chat_id, memory_settings.memory_scope)

        normalization = normalize_user_prompt(user_content)
        interpretation_content = normalization.normalized
        prompt_corrections = [
            {
                "original": correction.original,
                "corrected": correction.corrected,
                "reason": correction.reason,
            }
            for correction in normalization.corrections
        ]
        normalization_metadata = prompt_normalization_metadata(normalization)

        if record_user_message:
            user_message = await conversation_store.add_message(
                chat_id,
                MessageRole.USER,
                user_content,
                metadata=normalization_metadata or None,
            )
            if on_event and normalization_metadata:
                await on_event({
                    "type": "prompt_normalized",
                    "message_id": user_message.id,
                    "prompt_corrections": normalization_metadata["prompt_corrections"],
                    "interpreted_request": normalization_metadata["interpreted_request"],
                })

        readiness = await run_readiness_check(include_inference=False)
        if not readiness.get("chat_ready"):
            error_message = readiness.get("blocking_message") or readiness.get("summary") or (
                "Models are not ready for chat."
            )
            assistant = await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                error_message,
            )
            return OrchestrationResponse(
                chat_id=chat_id,
                messages=[assistant],
                agent_discussions=[],
                pending_approvals=approval_manager.list_pending(chat_id),
                effective_execution_strategy=effective_strategy,
            )

        from agentforge.agents.grill_mode import load_grill_session

        grill_session = await load_grill_session(chat_id)
        use_grill = (
            mode == OrchestrationMode.GRILL
            or chat.grill_enabled
            or grill_session is not None
        )
        if use_grill and mode != OrchestrationMode.QUICK:
            return await self._run_grill(
                chat_id,
                interpretation_content,
                role_ids,
                effective_strategy,
                on_event,
                intervention_queue,
            )

        workspace_intent = detect_workspace_intent(interpretation_content)
        prior_board = await load_task_board_memory(chat_id)
        task_state = build_task_state(
            user_content,
            workspace_intent,
            prior_board,
            interpreted_request=interpretation_content,
            prompt_corrections=prompt_corrections,
        )
        await emit_task_board_update(on_event, task_state)
        path_resolution = build_path_resolution_context(
            interpretation_content,
            workspace_intent,
        )
        path_context_token = activate_path_resolution_context(path_resolution)
        prefetched_reads: dict[str, str] = {}
        try:
            read_only = (
                workspace_intent.wants_file_read
                and not workspace_intent.wants_file_creation
            )
            async with command_audit_scope(chat_id, "system", "System", on_event):
                if read_only:
                    prefetched_reads = await prefetch_read_file_contents(
                        interpretation_content,
                        workspace_intent,
                    )
                    seed_read_facts(task_state, prefetched_reads)
                    path_context = build_read_context_block(prefetched_reads)
                else:
                    path_context = await build_workspace_path_context(workspace_intent)
                    if workspace_intent.wants_list_directory and path_context:
                        listing_targets = list(
                            workspace_intent.target_dirs or workspace_intent.target_paths
                        )
                        if listing_targets:
                            seed_list_directory_facts(
                                task_state,
                                listing_targets[0],
                                path_context,
                            )
            await emit_task_board_update(on_event, task_state)
            process_context = path_context
            if workspace_intent.requires_tools:
                process_context = "\n".join(
                    part for part in (path_context, workspace_intent.build_prompt_addon()) if part
                )
            if workspace_intent.requires_tools:
                self._ambient_context = ""
            else:
                self._ambient_context = await context_registry.build_for_message(
                    user_content,
                    chat_id,
                    on_event=on_event,
                    process_context=process_context,
                    client_ip=client_ip,
                    workspace_task_active=workspace_intent.requires_tools,
                )

            if mode == OrchestrationMode.MULTI:
                result = await self._run_multi(
                    chat_id,
                    interpretation_content,
                    role_ids,
                    memory_context,
                    tools,
                    memory_settings.memory_scope,
                    effective_strategy,
                    on_event,
                    intervention_queue,
                    workspace_intent=workspace_intent,
                    path_context=path_context,
                    task_state=task_state,
                    prefetched_reads=prefetched_reads,
                    prompt_normalization=normalization,
                )
            elif mode == OrchestrationMode.QUICK:
                result = await self._run_quick(
                    chat_id,
                    user_content,
                    memory_context,
                    memory_settings.enabled,
                    effective_strategy,
                    on_event,
                    intervention_queue,
                    path_context=path_context,
                )
            else:
                result = await self._run_single(
                    chat_id,
                    interpretation_content,
                    role_ids,
                    memory_context,
                    tools,
                    memory_settings.memory_scope,
                    effective_strategy,
                    on_event,
                    intervention_queue,
                    workspace_intent=workspace_intent,
                    path_context=path_context,
                    task_state=task_state,
                    prefetched_reads=prefetched_reads,
                    prompt_normalization=normalization,
                )
                if result.resolved_role_id:
                    await conversation_store.update_chat(
                        chat_id,
                        ChatUpdate(role_ids=[result.resolved_role_id]),
                    )

            await persist_task_board(chat_id, task_state, on_event=on_event)
            return result
        finally:
            deactivate_path_resolution_context(path_context_token)

    def _build_system_prompt(
        self,
        role: AgentRole | None,
        memory_context: str,
        tools_enabled: bool = True,
        workspace_intent: WorkspaceIntent | None = None,
        path_context: str = "",
        task_state: TaskState | None = None,
    ) -> str:
        """Compose system prompt with optional workspace and memory info."""
        base = role.system_prompt if role else "You are a helpful AI assistant."
        workspace = ""
        web_search = ""
        if tools_enabled:
            workspace = (
                f"\n\nWorkspace root: {settings.workspace_root}\n"
                "All file paths are relative to this directory.\n"
            )
            if workspace_intent and workspace_intent.wants_file_read:
                workspace += (
                    "When the user asks to read or list file content, you MUST use "
                    "read_file and quote the content verbatim in your answer.\n"
                    "Never invent file contents or reply with JSON status placeholders.\n"
                )
            else:
                workspace += (
                    "When the user asks to create, save, or write files, you MUST use "
                    "write_file — never paste file contents in chat only.\n"
                )
            workspace += (
                "To modify existing code, prefer search_files to locate exact positions, "
                "then edit_file with match_id or old_string/new_text. "
                "Use read_file with start_line/end_line to inspect numbered sections.\n"
                "Use tools when you need to read files, list directories, run commands, or save memory."
            )
            if settings.web_search_enabled:
                web_search = (
                    "\nOnline web search is available via the web_search tool "
                    "(DuckDuckGo and Wikipedia, no API key required). "
                    "Use it for current facts, documentation, news, and research."
                )
            if workspace_intent and workspace_intent.requires_tools:
                workspace += workspace_intent.build_prompt_addon()
            if path_context:
                workspace += f"\n\n{path_context}"
        elif path_context:
            workspace = f"\n\n{path_context}"
        task_board = ""
        if task_state:
            board_block = format_task_board_block(task_state)
            if board_block:
                task_board = f"\n\n{board_block}"
        ambient = f"\n\n{self._ambient_context}" if self._ambient_context else ""
        memory = f"\n\n{memory_context}" if memory_context else ""
        return base + workspace + web_search + task_board + ambient + memory

    async def _stream_llm_complete(
        self,
        llm: LLMProvider,
        messages: list[dict],
        on_event: Callable | None,
    ) -> tuple[str, str]:
        """
        Stream an LLM completion and emit incremental content events.

        :param llm: Resolved LLM provider instance
        :param messages: Conversation messages for the model
        :param on_event: Optional WebSocket event callback
        :return: Full assistant text and model identifier
        """
        parts: list[str] = []
        async for delta in llm.complete_stream(messages):
            await self._ensure_not_cancelled()
            parts.append(delta)
            if on_event:
                await on_event({
                    "type": "content_delta",
                    "content": delta,
                })
        return "".join(parts), llm.config.model
