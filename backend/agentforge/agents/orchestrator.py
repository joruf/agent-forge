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
from agentforge.agents.workspace_intent import WorkspaceIntent, detect_workspace_intent
from agentforge.agents.workspace_executor import (
    build_deliverable_status_summary,
    build_implementation_prompt,
    build_materialization_prompt,
    fallback_file_content,
    file_exists_in_workspace,
    missing_requested_files,
    plan_deliverable_files,
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
    execute_shell_command,
    record_command,
    serialize_shell_command_entry,
)
from agentforge.tools.shell_security import classify_shell_command, run_shell_command


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


class AgentOrchestrator:
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
                if workspace_intent and workspace_intent.wants_file_creation:
                    return self.max_tool_rounds
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
        if mode in (OrchestrationMode.SINGLE, OrchestrationMode.QUICK):
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
        if workspace_intent and workspace_intent.wants_file_creation:
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
        if not intent.wants_file_creation:
            return roles

        priority = {
            "developer": 0,
            "architect": 1,
            "devops": 2,
            "documentation": 3,
            "project_manager": 90,
        }
        return sorted(roles, key=lambda role: (priority.get(role.id, 50), role.id))

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

    def _build_multi_prompt(
        self,
        role: AgentRole,
        round_num: int,
        user_content: str,
        transcript: list[str],
        workspace_intent: WorkspaceIntent | None = None,
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
        if intent.wants_file_creation and role.id in self.FULL_TOOL_ROLES:
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
            if intent.wants_file_creation:
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
            if intent.wants_file_creation:
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
                    else ""
                )
                + deliverable_note
            )

        role_note = self._role_multi_discussion_note(role.id)
        return (
            f"User request: {user_content}\n\nDiscussion so far:\n"
            + "\n".join(transcript[-8:])
            + f"\n\nRespond as {role.name}. Be concise and actionable."
            + workspace_note
            + role_note
        )

    @staticmethod
    def _role_multi_discussion_note(role_id: str) -> str:
        """
        Return role-specific instructions for multi-agent discussion turns.

        :param role_id: Agent role identifier
        :return: Additional prompt guidance
        """
        if role_id == "reviewer":
            return (
                "\n\nReview the existing discussion only. Do not generate full HTML, "
                "PHP, or complete implementations. Give brief, actionable feedback."
            )
        if role_id in {"software_tester", "security"}:
            return (
                "\n\nAnalyze and report findings only. Do not replace the Developer "
                "by outputting full implementations."
            )
        if role_id == "developer":
            return (
                "\n\nIf you use write_file or run_command, summarize what you created "
                "or changed in plain language for the team."
            )
        return ""

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
        )
        tools_enabled = role.id in self.FULL_TOOL_ROLES and (
            role.id == "developer" or intent.wants_file_creation
        )
        system = self._build_system_prompt(
            role,
            memory_context,
            tools_enabled=tools_enabled,
            workspace_intent=intent,
            path_context=path_context,
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
            mode_multi=True,
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
        :return: Tuple of implementation summary and discussion message
        """
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

        discussion = AgentMessage(
            from_agent=developer.name,
            to_agent="team",
            content=content,
            timestamp=datetime.now(timezone.utc),
        )
        return content, discussion

    async def _build_user_input_response(
        self,
        chat_id: str,
        role: AgentRole,
        content: str,
        outputs: list[MessageResponse],
        discussions: list[AgentMessage],
        effective_strategy: ExecutionStrategy,
    ) -> OrchestrationResponse:
        """
        Persist a [ASK_USER] message and return a partial orchestration response.

        :param chat_id: Chat session ID
        :param role: Requesting role
        :param content: Role output containing [ASK_USER]
        :param outputs: Output messages collected so far
        :param discussions: Agent discussions collected so far
        :param effective_strategy: Effective execution strategy for this run
        :return: Orchestration response waiting for user input
        """
        ask_msg = await conversation_store.add_message(
            chat_id,
            MessageRole.AGENT,
            content.replace("[ASK_USER]", "").strip(),
            agent_id=role.id,
            agent_name=role.name,
            metadata={"needs_user_input": True},
        )
        outputs.append(ask_msg)
        return OrchestrationResponse(
            chat_id=chat_id,
            messages=outputs,
            agent_discussions=discussions,
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
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

        if record_user_message:
            await conversation_store.add_message(
                chat_id, MessageRole.USER, user_content
            )

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

        workspace_intent = detect_workspace_intent(user_content)
        path_context = build_workspace_path_context(workspace_intent)
        process_context = path_context
        if workspace_intent.requires_tools:
            process_context = "\n".join(
                part for part in (path_context, workspace_intent.build_prompt_addon()) if part
            )
        self._ambient_context = await context_registry.build_for_message(
            user_content,
            chat_id,
            on_event=on_event,
            process_context=process_context,
            client_ip=client_ip,
        )

        if mode == OrchestrationMode.MULTI:
            result = await self._run_multi(
                chat_id,
                user_content,
                role_ids,
                memory_context,
                tools,
                memory_settings.memory_scope,
                effective_strategy,
                on_event,
                intervention_queue,
                workspace_intent=workspace_intent,
                path_context=path_context,
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
                user_content,
                role_ids,
                memory_context,
                tools,
                memory_settings.memory_scope,
                effective_strategy,
                on_event,
                intervention_queue,
                workspace_intent=workspace_intent,
                path_context=path_context,
            )
            if result.resolved_role_id:
                await conversation_store.update_chat(
                    chat_id,
                    ChatUpdate(role_ids=[result.resolved_role_id]),
                )

        return result

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
    ) -> OrchestrationResponse:
        """Single agent with a selected software-development role."""
        await self._ensure_not_cancelled()
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
        )
        await self._emit_agent_end(on_event, role_id, role.name, round_num=1)

        still_missing = missing_requested_files(user_content, workspace_intent)
        if still_missing:
            fallback = await self._guarantee_workspace_deliverables(
                chat_id,
                user_content,
                workspace_intent,
                role_id=role_id,
                on_event=on_event,
            )
            if fallback:
                content = (
                    fallback
                    if self._is_weak_discussion_content(content)
                    else f"{content}\n\n{fallback}"
                )
        else:
            fallback = await self._guarantee_workspace_deliverables(
                chat_id,
                user_content,
                workspace_intent,
                role_id=role_id,
                on_event=on_event,
            )
            if fallback and self._is_weak_discussion_content(content):
                content = fallback

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
    ) -> OrchestrationResponse:
        """Multi-agent discussion with project manager synthesis."""
        if not role_ids:
            role_ids = ["project_manager", "developer", "reviewer"]

        roles = [role for role in role_registry.get_roles(role_ids) if role is not None]
        if not roles:
            fallback = role_registry.get_role("developer")
            if fallback is None:
                raise RuntimeError("Default developer role is not registered.")
            roles = [fallback]

        discussions: list[AgentMessage] = []
        transcript: list[str] = [f"User request: {user_content}"]
        outputs: list[MessageResponse] = []

        pm = role_registry.get_role("project_manager")
        if pm and pm.id not in [r.id for r in roles]:
            roles = [pm] + roles

        workspace_intent = workspace_intent or detect_workspace_intent(user_content)
        roles = self._order_roles_for_intent(roles, workspace_intent)
        max_multi_rounds = self._resolve_multi_rounds()

        impl_content, impl_discussion = await self._ensure_requested_files(
            chat_id=chat_id,
            user_content=user_content,
            intent=workspace_intent,
            memory_context=memory_context,
            tools=tools,
            memory_scope=memory_scope,
            on_event=on_event,
            intervention_queue=intervention_queue,
        )
        developer_impl_done = impl_discussion is not None
        if impl_discussion and impl_content:
            discussions.append(impl_discussion)
            transcript.append(f"{impl_discussion.from_agent}: {impl_content}")
            if on_event:
                await on_event({
                    "type": "agent_message",
                    "discussion": impl_discussion.model_dump(mode="json"),
                    "routing": {"source": "implementation_phase"},
                })

        for round_num in range(max_multi_rounds):
            await self._ensure_not_cancelled()
            await self._collect_interventions(transcript, intervention_queue, on_event)
            role_index = 0
            while role_index < len(roles):
                await self._ensure_not_cancelled()
                await self._collect_interventions(transcript, intervention_queue, on_event)
                role = roles[role_index]
                if developer_impl_done and role.id == "developer" and round_num == 0:
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
                        )
                        for batch_role in batch
                    ])
                    for batch_role, (content, routing, discussion) in zip(batch, results):
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
                )
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

        deliverable_summary = build_deliverable_status_summary(
            user_content,
            workspace_intent,
        )
        if deliverable_summary and workspace_intent.wants_file_creation:
            final_content = deliverable_summary
        else:
            final_content = transcript[-1].split(": ", 1)[-1] if transcript else "No result"
            if guarantee and guarantee not in final_content:
                final_content = f"{guarantee}\n\n{final_content}"
        if final_role:
            final_msg = await conversation_store.add_message(
                chat_id,
                MessageRole.ASSISTANT,
                final_content,
                agent_id=final_role.id,
                agent_name=final_role.name,
                metadata={"synthesis": True},
            )
            outputs.append(final_msg)

        return OrchestrationResponse(
            chat_id=chat_id,
            messages=outputs,
            agent_discussions=discussions,
            pending_approvals=approval_manager.list_pending(chat_id),
            effective_execution_strategy=effective_strategy,
        )

    def _build_system_prompt(
        self,
        role: AgentRole | None,
        memory_context: str,
        tools_enabled: bool = True,
        workspace_intent: WorkspaceIntent | None = None,
        path_context: str = "",
    ) -> str:
        """Compose system prompt with optional workspace and memory info."""
        base = role.system_prompt if role else "You are a helpful AI assistant."
        workspace = ""
        web_search = ""
        if tools_enabled:
            workspace = (
                f"\n\nWorkspace root: {settings.workspace_root}\n"
                "All file paths are relative to this directory.\n"
                "When the user asks to create, save, or write files, you MUST use "
                "write_file — never paste file contents in chat only.\n"
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
        ambient = f"\n\n{self._ambient_context}" if self._ambient_context else ""
        memory = f"\n\n{memory_context}" if memory_context else ""
        return base + workspace + web_search + ambient + memory

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

    JSON_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
    KNOWN_TOOLS = frozenset({
        "write_file",
        "edit_file",
        "read_file",
        "search_files",
        "list_directory",
        "run_command",
        "remember",
        "web_search",
    })
    CODE_OUTPUT = re.compile(
        r"```|<!DOCTYPE|<\?php|<html[\s>]|function\s+\w+\(",
        re.IGNORECASE,
    )
    TOOL_USE_NUDGE = (
        "You responded with code or JSON text instead of using tools. "
        "Use the write_file tool now to create each file on disk. "
        "Do not reply with pasted code."
    )
    EMPTY_RESPONSE_NUDGE = (
        "Your last reply was empty or unusable for the team discussion. "
        "Use write_file to create the requested files, then summarize what you wrote."
    )

    @classmethod
    def _is_weak_discussion_content(cls, content: str) -> bool:
        """
        Detect assistant replies that are empty placeholders.

        :param content: Assistant message text
        :return: True when the content should not be shown in team discussion
        """
        text = (content or "").strip()
        if not text:
            return True
        if text in ("{}", "[]", "null", "undefined"):
            return True
        if text.startswith("{") and len(text) <= 120:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return False
            if not parsed:
                return True
            if isinstance(parsed, dict) and not parsed.get("function") and not parsed.get("name"):
                if set(parsed.keys()).issubset({"arguments", "parameters", "tool", "content"}):
                    nested = parsed.get("arguments") or parsed.get("parameters") or {}
                    if not nested:
                        return True
        return False

    @classmethod
    def _finalize_agent_content(cls, content: str, tool_summaries: list[str]) -> str:
        """
        Replace weak assistant text with a summary of successful tool actions.

        :param content: Raw assistant message text
        :param tool_summaries: Human-readable tool action summaries
        :return: Content suitable for team discussion
        """
        if not cls._is_weak_discussion_content(content):
            return content
        if tool_summaries:
            return "Completed workspace actions:\n- " + "\n- ".join(tool_summaries)
        return content.strip() or "No output produced."

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

    @staticmethod
    def _summarize_tool_call(name: str, arguments: str, output: str) -> str | None:
        """
        Build a short summary line for a successful tool call.

        :param name: Tool name
        :param arguments: JSON-encoded tool arguments
        :param output: Tool execution output
        :return: Summary line or None
        """
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            parsed_arguments = {}

        if name == "write_file":
            path = parsed_arguments.get("path")
            if path:
                return f"Created/updated file: {path}"
        if name == "edit_file":
            path = parsed_arguments.get("path")
            match_id = parsed_arguments.get("match_id")
            if path and match_id:
                return f"Edited file: {path} at {match_id}"
            if path:
                return f"Edited file: {path}"
        if name == "search_files":
            query = parsed_arguments.get("query")
            if query:
                return f"Searched files for: {query}"
        if name == "run_command":
            command = parsed_arguments.get("command")
            if command:
                return f"Ran command: {command}"
        if output:
            return f"{name}: {output[:120]}"
        return None

    @classmethod
    def _looks_like_code_only_output(cls, content: str) -> bool:
        """
        Detect assistant replies that contain code but no tool execution.

        :param content: Assistant message text
        :return: True when output looks like file content instead of tool use
        """
        text = (content or "").strip()
        if not text:
            return False
        if cls.CODE_OUTPUT.search(text):
            return True
        if text.startswith("{") and '"content"' in text and '"function"' not in text:
            return True
        return False

    @staticmethod
    def _normalize_tool_call_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        """
        Normalize one embedded tool-call dict.

        :param payload: Parsed JSON object
        :return: Normalized tool call dict or None
        """
        name = payload.get("function") or payload.get("name") or payload.get("tool")
        if not isinstance(name, str) or not name:
            return None
        if name not in AgentOrchestrator.KNOWN_TOOLS:
            return None

        arguments = payload.get("arguments") or payload.get("parameters") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        return {
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "name": name,
            "arguments": json.dumps(arguments),
        }

    @classmethod
    def _extract_tool_calls_from_payload(cls, payload: Any) -> list[dict[str, Any]]:
        """
        Extract tool calls from parsed JSON payload.

        :param payload: Parsed JSON value
        :return: Normalized tool call dicts
        """
        if isinstance(payload, list):
            calls: list[dict[str, Any]] = []
            for item in payload:
                if isinstance(item, dict):
                    normalized = cls._normalize_tool_call_payload(item)
                    if normalized:
                        calls.append(normalized)
            return calls

        if not isinstance(payload, dict):
            return []

        normalized = cls._normalize_tool_call_payload(payload)
        return [normalized] if normalized else []

    @classmethod
    def _parse_content_tool_calls(cls, content: str) -> list[dict[str, Any]]:
        """
        Parse tool calls embedded in assistant text.

        Some Ollama models return JSON tool instructions in content instead of
        structured tool_calls.

        :param content: Assistant message text
        :return: Normalized tool call dicts
        """
        stripped = (content or "").strip()
        if not stripped:
            return []

        candidates = [match.group(1).strip() for match in cls.JSON_FENCE.finditer(stripped)]
        candidates.append(stripped)

        parsed_calls: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if not candidate.startswith("{") and not candidate.startswith("["):
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            for call in cls._extract_tool_calls_from_payload(payload):
                key = (call["name"], call["arguments"])
                if key in seen:
                    continue
                seen.add(key)
                parsed_calls.append(call)
        return parsed_calls

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
                    if (
                        intent.wants_file_creation
                        and tool_schemas
                        and code_output_nudges < 2
                        and (
                            self._looks_like_code_only_output(content)
                            or self._is_weak_discussion_content(content)
                        )
                    ):
                        messages.append({"role": "assistant", "content": content})
                        nudge = (
                            self.EMPTY_RESPONSE_NUDGE
                            if self._is_weak_discussion_content(content)
                            else self.TOOL_USE_NUDGE
                        )
                        messages.append({"role": "user", "content": nudge})
                        code_output_nudges += 1
                        continue

                    routing["model"] = result.get("model", routing.get("model"))
                    return self._finalize_agent_content(content, tool_summaries), routing

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

    @staticmethod
    def _parse_run_command_arguments(arguments: str) -> tuple[str, str | None]:
        """
        Parse run_command tool arguments.

        :param arguments: JSON-encoded tool arguments
        :return: Command string and optional relative cwd
        """
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments.strip(), None
        command = str(parsed.get("command", "")).strip()
        cwd = parsed.get("cwd")
        return command, str(cwd).strip() if cwd else None

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
    ) -> MessageResponse | None:
        """Execute a previously approved shell command."""
        pending = approval_manager.list_pending(chat_id)
        target = next((p for p in pending if p.id == approval_id), None)
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
        classification = classify_shell_command(command)
        if not classification.allowed:
            tool_msg = await record_command(
                chat_id,
                command=command,
                cwd=cwd_value,
                status="blocked",
                success=False,
                exit_code=None,
                output=classification.reason,
                agent_id=None,
                agent_name=None,
                approval_id=approval_id,
            )
            approval_manager.pop_resume_state(approval_id)
            return tool_msg

        cwd = settings.workspace_root
        if cwd_value:
            cwd = _resolve_path(str(cwd_value))

        success, exit_code, formatted_output = await run_shell_command(command, cwd)
        command_status = "success" if success else "failed"

        tool_msg = await record_command(
            chat_id,
            command=command,
            cwd=cwd_value,
            status=command_status,
            success=success,
            exit_code=exit_code,
            output=formatted_output,
            agent_id=None,
            agent_name=None,
            approval_id=approval_id,
        )
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
