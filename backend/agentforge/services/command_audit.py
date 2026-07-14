"""Central audit logging for shell and workspace command execution."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agentforge.config import settings
from agentforge.models.schemas import MessageResponse, MessageRole, ToolCallResult
from agentforge.storage.conversation_store import conversation_store
from agentforge.tools.shell_security import classify_shell_command, run_shell_command

ApprovalCallback = Callable[[str, str, dict[str, Any]], Awaitable[str]]
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class CommandAuditContext:
    """Runtime context for mandatory command audit logging."""

    chat_id: str
    agent_id: str | None = None
    agent_name: str | None = None
    on_event: EventCallback | None = None


audit_context: ContextVar[CommandAuditContext | None] = ContextVar(
    "command_audit_context",
    default=None,
)


def serialize_shell_command_entry(message: MessageResponse) -> dict[str, Any]:
    """
    Serialize a shell command message for WebSocket clients.

    :param message: Stored message response
    :return: JSON-serializable command entry
    """
    metadata = message.metadata or {}
    return {
        "id": message.id,
        "command": metadata.get("command", ""),
        "cwd": metadata.get("cwd"),
        "status": metadata.get("status", "success" if metadata.get("success") else "failed"),
        "success": bool(metadata.get("success")),
        "exit_code": metadata.get("exit_code"),
        "agent_id": message.agent_id,
        "agent_name": message.agent_name,
        "approval_id": metadata.get("approval_id"),
        "output": message.content,
        "timestamp": message.created_at.isoformat(),
        "source": metadata.get("source", "shell"),
    }


def shell_status_from_output(output: str, *, success: bool) -> tuple[str, int | None]:
    """
    Derive shell command status and exit code from tool output.

    :param output: Tool execution output
    :param success: Tool success flag
    :return: Status label and optional exit code
    """
    import re

    lowered = output.lower()
    if "is blocked" in lowered or lowered.startswith("invalid command"):
        return "blocked", None
    if lowered.startswith("awaiting approval") or lowered.startswith("approval required"):
        return "pending", None
    if re.match(r"^\[OK\]", output):
        return "success", 0
    exit_match = re.match(r"^\[Exit (\d+)\]", output)
    if exit_match:
        exit_code = int(exit_match.group(1))
        return ("success" if exit_code == 0 else "failed"), exit_code
    return ("success" if success else "failed"), None


def parents_to_create(relative_path: str) -> list[str]:
    """
    Return workspace-relative directories that do not exist yet for a file path.

    :param relative_path: Target workspace-relative file path
    :return: Ordered directory paths to create
    """
    from agentforge.tools.registry import _resolve_path

    root = settings.workspace_root.resolve()
    target = _resolve_path(relative_path)
    missing: list[str] = []
    current = target.parent
    while str(current).startswith(str(root)) and current != root:
        if not current.exists():
            missing.append(str(current.relative_to(root)))
        current = current.parent
    missing.reverse()
    return missing


async def record_command(
    chat_id: str,
    *,
    command: str,
    cwd: str | None,
    status: str,
    success: bool,
    exit_code: int | None,
    output: str,
    agent_id: str | None,
    agent_name: str | None,
    approval_id: str | None = None,
    source: str = "shell",
    on_event: EventCallback | None = None,
) -> MessageResponse:
    """
    Persist one command audit entry for the command history UI.

    :param chat_id: Chat session ID
    :param command: Command label shown in history
    :param cwd: Optional relative working directory
    :param status: Status label
    :param success: Whether execution succeeded
    :param exit_code: Optional process exit code
    :param output: Command output text
    :param agent_id: Agent role identifier
    :param agent_name: Agent display name
    :param approval_id: Optional approval identifier
    :param source: Command source label (shell, workspace)
    :param on_event: Optional WebSocket callback
    :return: Stored message response
    """
    metadata: dict[str, Any] = {
        "kind": "shell_command",
        "command": command,
        "status": status,
        "success": success,
        "source": source,
    }
    if cwd:
        metadata["cwd"] = cwd
    if exit_code is not None:
        metadata["exit_code"] = exit_code
    if approval_id:
        metadata["approval_id"] = approval_id

    message = await conversation_store.add_message(
        chat_id,
        MessageRole.TOOL,
        output[: settings.max_output_chars] if output else "",
        agent_id=agent_id,
        agent_name=agent_name,
        metadata=metadata,
    )
    if on_event:
        await on_event({
            "type": "shell_command_recorded",
            "entry": serialize_shell_command_entry(message),
        })
        if status == "pending" and approval_id:
            await on_event({
                "type": "shell_command_pending",
                "approval_id": approval_id,
                "command": command,
                "cwd": cwd,
                "agent_id": agent_id,
                "agent_name": agent_name,
                "timestamp": message.created_at.isoformat(),
            })
    return message


async def record_from_context(
    *,
    command: str,
    cwd: str | None,
    status: str,
    success: bool,
    exit_code: int | None,
    output: str,
    approval_id: str | None = None,
    source: str = "shell",
) -> MessageResponse | None:
    """
    Persist a command audit entry using the active audit context.

    :param command: Command label shown in history
    :param cwd: Optional relative working directory
    :param status: Status label
    :param success: Whether execution succeeded
    :param exit_code: Optional process exit code
    :param output: Command output text
    :param approval_id: Optional approval identifier
    :param source: Command source label
    :return: Stored message response or None when no audit context is active
    """
    ctx = audit_context.get()
    if ctx is None:
        return None
    return await record_command(
        ctx.chat_id,
        command=command,
        cwd=cwd,
        status=status,
        success=success,
        exit_code=exit_code,
        output=output,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        approval_id=approval_id,
        source=source,
        on_event=ctx.on_event,
    )


async def record_write_file(
    relative_path: str,
    *,
    output: str,
    success: bool,
    created_dirs: list[str] | None = None,
) -> None:
    """
    Log workspace file writes and created directories in command history.

    :param relative_path: Workspace-relative file path
    :param output: Tool output text
    :param success: Whether the write succeeded
    :param created_dirs: Directories created before the write
    """
    for directory in created_dirs or []:
        await record_from_context(
            command=f"mkdir -p {directory}",
            cwd=".",
            status="success" if success else "failed",
            success=success,
            exit_code=0 if success else 1,
            output=f"Created directory: {directory}",
            source="workspace",
        )
    await record_from_context(
        command=f"write_file {relative_path}",
        cwd=str(Path(relative_path).parent) if Path(relative_path).parent != Path(".") else ".",
        status="success" if success else "failed",
        success=success,
        exit_code=0 if success else 1,
        output=output,
        source="workspace",
    )


async def execute_shell_command(
    chat_id: str,
    *,
    command: str,
    cwd: str | None,
    agent_id: str | None,
    agent_name: str | None,
    approval_callback: ApprovalCallback | None,
    on_event: EventCallback | None,
) -> ToolCallResult:
    """
    Validate, execute, and audit one shell command.

    This is the only supported runtime path for shell execution.

    :param chat_id: Chat session ID
    :param command: Shell command string
    :param cwd: Optional workspace-relative working directory
    :param agent_id: Agent role identifier
    :param agent_name: Agent display name
    :param approval_callback: Optional approval request callback
    :param on_event: Optional WebSocket callback
    :return: Tool execution result
    """
    classification = classify_shell_command(command)
    if not classification.allowed:
        output = classification.reason
        await record_command(
            chat_id,
            command=command,
            cwd=cwd,
            status="blocked",
            success=False,
            exit_code=None,
            output=output,
            agent_id=agent_id,
            agent_name=agent_name,
            source="shell",
            on_event=on_event,
        )
        return ToolCallResult(tool="run_command", success=False, output=output)

    if classification.needs_approval:
        if approval_callback is None:
            output = f"Approval required: {classification.reason}"
            await record_command(
                chat_id,
                command=command,
                cwd=cwd,
                status="pending",
                success=False,
                exit_code=None,
                output=output,
                agent_id=agent_id,
                agent_name=agent_name,
                source="shell",
                on_event=on_event,
            )
            return ToolCallResult(
                tool="run_command",
                success=False,
                output=output,
                requires_approval=True,
            )

        from agentforge.i18n import t

        approval_id = await approval_callback(
            "command",
            t("tools.execute_command", command=command),
            {"command": command, "cwd": cwd},
        )
        output = f"Awaiting approval: {classification.reason}"
        await record_command(
            chat_id,
            command=command,
            cwd=cwd,
            status="pending",
            success=False,
            exit_code=None,
            output=output,
            agent_id=agent_id,
            agent_name=agent_name,
            approval_id=approval_id,
            source="shell",
            on_event=on_event,
        )
        return ToolCallResult(
            tool="run_command",
            success=False,
            output=output,
            requires_approval=True,
            approval_id=approval_id,
        )

    workspace_cwd = settings.workspace_root
    if cwd:
        from agentforge.tools.registry import _resolve_path

        workspace_cwd = _resolve_path(cwd)

    try:
        success, exit_code, formatted_output = await run_shell_command(command, workspace_cwd)
    except Exception as exc:
        await record_command(
            chat_id,
            command=command,
            cwd=cwd,
            status="failed",
            success=False,
            exit_code=None,
            output=str(exc),
            agent_id=agent_id,
            agent_name=agent_name,
            source="shell",
            on_event=on_event,
        )
        return ToolCallResult(tool="run_command", success=False, output=str(exc))

    status, parsed_exit = shell_status_from_output(formatted_output, success=success)
    await record_command(
        chat_id,
        command=command,
        cwd=cwd,
        status=status,
        success=success,
        exit_code=parsed_exit if parsed_exit is not None else exit_code,
        output=formatted_output,
        agent_id=agent_id,
        agent_name=agent_name,
        source="shell",
        on_event=on_event,
    )
    return ToolCallResult(tool="run_command", success=success, output=formatted_output)


@asynccontextmanager
async def command_audit_scope(
    chat_id: str,
    agent_id: str | None,
    agent_name: str | None,
    on_event: EventCallback | None,
):
    """
    Activate mandatory command audit logging for nested workspace operations.

    :param chat_id: Chat session ID
    :param agent_id: Agent role identifier
    :param agent_name: Agent display name
    :param on_event: Optional WebSocket callback
    :yield: Active audit context
    """
    token = audit_context.set(
        CommandAuditContext(
            chat_id=chat_id,
            agent_id=agent_id,
            agent_name=agent_name,
            on_event=on_event,
        ),
    )
    try:
        yield
    finally:
        audit_context.reset(token)
