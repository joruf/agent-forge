"""Pydantic schemas for API and internal communication."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class OrchestrationMode(str, Enum):
    """Execution mode for agent orchestration."""

    SINGLE = "single"
    MULTI = "multi"
    QUICK = "quick"
    GRILL = "grill"


class ExecutionStrategy(str, Enum):
    """Execution strategy for multi-agent orchestration."""

    AUTO = "auto"
    SERIAL = "serial"
    PARALLEL = "parallel"
    HYBRID = "hybrid"


class MessageRole(str, Enum):
    """Message author role."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    AGENT = "agent"
    TOOL = "tool"


class AgentRole(BaseModel):
    """Definition of an AI agent role."""

    id: str
    name: str
    description: str
    system_prompt: str
    is_builtin: bool = True


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    model: str = "ollama/llama3"
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: str = ""
    openai_api_base: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    mistral_api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    auto_routing: bool = True


class ChatMemorySettings(BaseModel):
    """Per-chat memory configuration."""

    memory_tokens: int = 32000
    memory_scope: str = "chat"
    enabled: bool = True

    @field_validator("memory_scope")
    @classmethod
    def enforce_chat_scope(cls, value: str) -> str:
        """
        Force chat-scoped memory regardless of incoming payload.

        :param value: Requested memory scope
        :return: Normalized chat scope
        """
        return "chat"


class ChatCreate(BaseModel):
    """Request to create a new chat."""

    title: str = "New Chat"
    mode: OrchestrationMode = OrchestrationMode.SINGLE
    execution_strategy: ExecutionStrategy = ExecutionStrategy.AUTO
    role_ids: list[str] = Field(default_factory=list)
    memory: ChatMemorySettings = Field(default_factory=ChatMemorySettings)
    grill_enabled: bool = False


class ChatUpdate(BaseModel):
    """Request to update chat metadata."""

    title: str | None = None
    mode: OrchestrationMode | None = None
    execution_strategy: ExecutionStrategy | None = None
    role_ids: list[str] | None = None
    memory: ChatMemorySettings | None = None
    grill_enabled: bool | None = None


class ChatResponse(BaseModel):
    """Chat session response."""

    id: str
    title: str
    mode: OrchestrationMode
    execution_strategy: ExecutionStrategy
    role_ids: list[str]
    memory: ChatMemorySettings
    grill_enabled: bool = False
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    """User message submission."""

    content: str
    mode: OrchestrationMode | None = None
    role_ids: list[str] | None = None


class AgentMessage(BaseModel):
    """Inter-agent communication message."""

    from_agent: str
    to_agent: str | None
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class MessageResponse(BaseModel):
    """Stored message with optional agent metadata."""

    id: str
    chat_id: str
    role: MessageRole
    agent_id: str | None = None
    agent_name: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ToolCallResult(BaseModel):
    """Result of a tool execution."""

    tool: str
    success: bool
    output: str
    requires_approval: bool = False
    approval_id: str | None = None


class ApprovalRequest(BaseModel):
    """Pending approval for a sensitive action."""

    id: str
    chat_id: str
    action_type: str
    description: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalResponse(BaseModel):
    """User response to an approval request."""

    approved: bool
    comment: str = ""
    choice_id: str | None = None


class UserChoiceOption(BaseModel):
    """One selectable option in a user-choice dialog."""

    id: str
    label: str
    description: str = ""


class ApprovalResumeState(BaseModel):
    """Continuation state for resuming an agent after approval."""

    chat_id: str
    agent_id: str
    agent_name: str
    role_id: str
    user_content: str
    mode_single: bool = False
    memory_scope: str = "chat"
    routing: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str


class AgendaResumeState(BaseModel):
    """Continuation state for resuming a workspace agenda pipeline."""

    chat_id: str
    user_content: str
    intent: dict[str, Any]
    task_state_snapshot: dict[str, Any] | None = None
    step_index: int
    step_path: str
    requested_tag: str
    content_source_path: str
    prefetched_reads: dict[str, str] = Field(default_factory=dict)


class OrchestrationResumeState(BaseModel):
    """Continuation state for resuming orchestration after user clarification."""

    kind: str
    chat_id: str
    user_content: str
    context: dict[str, Any] = Field(default_factory=dict)
    task_state_snapshot: dict[str, Any] | None = None
    intent: dict[str, Any] | None = None
    mode: str = "multi"
    role_ids: list[str] = Field(default_factory=list)
    effective_strategy: str = "auto"
    source_role_id: str = ""
    source_role_name: str = ""
    question_text: str = ""


class GrillResumeState(BaseModel):
    """Continuation state for grill-mode clarification and plan review."""

    chat_id: str
    phase: str
    session_snapshot: dict[str, Any] = Field(default_factory=dict)
    pending_question: str = ""
    recommended_answer: str = ""


class CommandRequest(BaseModel):
    """Shell command execution request."""

    command: str
    cwd: str | None = None


class OrchestrationRequest(BaseModel):
    """Full orchestration request."""

    chat_id: str
    content: str
    mode: OrchestrationMode = OrchestrationMode.SINGLE
    role_ids: list[str] = Field(default_factory=list)
    llm: LLMConfig | None = None


class OrchestrationResponse(BaseModel):
    """Orchestration result."""

    chat_id: str
    messages: list[MessageResponse]
    agent_discussions: list[AgentMessage]
    pending_approvals: list[ApprovalRequest]
    title: str | None = None
    resolved_role_id: str | None = None
    effective_execution_strategy: ExecutionStrategy | None = None
