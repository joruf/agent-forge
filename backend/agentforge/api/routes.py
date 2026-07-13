"""FastAPI route handlers."""

import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.role_registry import role_registry
from agentforge.config import settings
from agentforge.llm.model_catalog import model_catalog
from agentforge.llm.task_types import TaskType
from agentforge.llm.model_router import model_router
from agentforge.memory.store import memory_store
from agentforge.storage.model_store import model_store
from agentforge.models.schemas import (
    AgentRole,
    ApprovalResponse,
    ChatCreate,
    ChatMemorySettings,
    ChatUpdate,
    LLMConfig,
    MessageCreate,
    MessageRole,
    OrchestrationMode,
)
from agentforge.llm.cloud_providers import cloud_key_flags
from agentforge.services.setup_service import run_all_tests, run_model_access_tests
from agentforge.storage.conversation_store import conversation_store
from agentforge.storage.setup_store import setup_store

router = APIRouter()
_CHAT_SOCKETS: dict[str, set[WebSocket]] = defaultdict(set)
DEFAULT_CHAT_TITLE = "New Chat"


def _fallback_chat_title(user_content: str) -> str:
    """
    Build a readable fallback title from the first user message.

    :param user_content: Initial user message
    :return: Short title string
    """
    text = " ".join((user_content or "").split())
    return text[:60] if text else DEFAULT_CHAT_TITLE


def _chat_needs_generated_title(title: str, retry: bool) -> bool:
    """
    Decide whether a chat still needs an AI-generated title.

    :param title: Current chat title
    :param retry: Whether this send is a retry of a failed prompt
    :return: True when title generation should run
    """
    return title == DEFAULT_CHAT_TITLE and not retry


async def _generate_chat_title(
    chat_id: str,
    user_content: str,
    llm: dict[str, Any] | None = None,
) -> str:
    """
    Generate and persist a chat title without blocking the main response.

    :param chat_id: Chat session ID
    :param user_content: Initial user message used for title generation
    :param llm: Optional runtime LLM configuration payload
    :return: Generated or fallback title
    """
    fallback = _fallback_chat_title(user_content)
    try:
        orchestrator = AgentOrchestrator(
            LLMConfig(**llm) if llm else None,
        )
        title = await orchestrator.llm.generate_title(user_content)
        if not title or title == DEFAULT_CHAT_TITLE:
            title = fallback
        await conversation_store.update_chat(chat_id, ChatUpdate(title=title))
        await _broadcast_chat_event(chat_id, {"type": "title_updated", "title": title})
        return title
    except Exception:
        try:
            await conversation_store.update_chat(chat_id, ChatUpdate(title=fallback))
            await _broadcast_chat_event(
                chat_id,
                {"type": "title_updated", "title": fallback},
            )
        except Exception:
            pass
        return fallback


async def _broadcast_chat_event(chat_id: str, event: dict[str, Any]) -> None:
    """
    Broadcast an event to all active WebSocket clients of a chat.

    :param chat_id: Chat session ID
    :param event: JSON-serializable event payload
    """
    clients = list(_CHAT_SOCKETS.get(chat_id, set()))
    if not clients:
        return
    message = json.dumps(event)
    stale: list[WebSocket] = []
    for socket in clients:
        try:
            await socket.send_text(message)
        except Exception:
            stale.append(socket)
    if stale:
        active = _CHAT_SOCKETS.get(chat_id, set())
        for socket in stale:
            active.discard(socket)
        if not active and chat_id in _CHAT_SOCKETS:
            del _CHAT_SOCKETS[chat_id]


class SettingsUpdate(BaseModel):
    """Runtime settings update payload."""

    workspace_root: str | None = None
    ollama_base_url: str | None = None
    openai_api_key: str | None = None
    openai_api_base: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    mistral_api_key: str | None = None
    default_model: str | None = None
    default_memory_tokens: int | None = None
    llm_auto_routing: bool | None = None
    ui_language: str | None = None
    command_whitelist: list[str] | None = None
    command_blacklist: list[str] | None = None


class RoleCreate(BaseModel):
    """Custom role creation payload."""

    id: str
    name: str
    description: str
    system_prompt: str


class UserModelCreate(BaseModel):
    """Create a user-managed model entry."""

    ollama_tag: str
    display_name: str | None = None
    assigned_tasks: list[str] | None = None
    enabled: bool = True
    notes: str = ""
    auto_suggest: bool = True


class UserModelUpdate(BaseModel):
    """Update a user-managed model entry."""

    ollama_tag: str | None = None
    display_name: str | None = None
    assigned_tasks: list[str] | None = None
    enabled: bool | None = None
    notes: str | None = None


class RoutingUpdate(BaseModel):
    """Update routing for one or more tasks."""

    routing: dict[str, str]


class SetupStepUpdate(BaseModel):
    """Update setup wizard step."""

    step: str


class SetupTestRequest(BaseModel):
    """Run setup tests with optional overrides."""

    ollama_base_url: str | None = None
    workspace_root: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    mistral_api_key: str | None = None
    test_generate: bool = True


class ModelAccessTestRequest(BaseModel):
    """Run model access tests from settings."""

    ollama_base_url: str | None = None
    default_model: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    mistral_api_key: str | None = None
    test_inference: bool = True


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "app": settings.app_name}


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    """Return current application settings."""
    return {
        "workspace_root": str(settings.workspace_root),
        "ollama_base_url": settings.ollama_base_url,
        "default_model": settings.default_model,
        "default_memory_tokens": settings.default_memory_tokens,
        "llm_auto_routing": settings.llm_auto_routing,
        "llm_request_timeout": settings.llm_request_timeout,
        "override_model": settings.override_model.strip() or None,
        "command_whitelist": settings.command_whitelist,
        "command_blacklist": settings.command_blacklist,
        **cloud_key_flags(),
        "ui_language": settings.ui_language,
    }


@router.patch("/settings")
async def update_settings(data: SettingsUpdate) -> dict[str, Any]:
    """Update runtime settings."""
    if data.workspace_root is not None:
        settings.workspace_root = data.workspace_root
    if data.ollama_base_url is not None:
        settings.ollama_base_url = data.ollama_base_url
    if data.openai_api_key is not None:
        settings.openai_api_key = data.openai_api_key
    if data.openai_api_base is not None:
        settings.openai_api_base = data.openai_api_base
    if data.anthropic_api_key is not None:
        settings.anthropic_api_key = data.anthropic_api_key
    if data.gemini_api_key is not None:
        settings.gemini_api_key = data.gemini_api_key
    if data.groq_api_key is not None:
        settings.groq_api_key = data.groq_api_key
    if data.mistral_api_key is not None:
        settings.mistral_api_key = data.mistral_api_key
    if data.default_model is not None:
        settings.default_model = data.default_model
    if data.llm_auto_routing is not None:
        settings.llm_auto_routing = data.llm_auto_routing
    if data.default_memory_tokens is not None:
        settings.default_memory_tokens = data.default_memory_tokens
    if data.command_whitelist is not None:
        settings.command_whitelist = data.command_whitelist
    if data.command_blacklist is not None:
        settings.command_blacklist = data.command_blacklist
    if data.ui_language is not None:
        settings.ui_language = data.ui_language
    return await get_settings()


@router.get("/setup/status")
async def get_setup_status() -> dict[str, Any]:
    """Return first-run setup wizard status."""
    return setup_store.get_status()


@router.post("/setup/resume")
async def resume_setup() -> dict[str, Any]:
    """Resume setup wizard after skip."""
    return setup_store.resume()


@router.post("/setup/skip")
async def skip_setup() -> dict[str, Any]:
    """Skip setup wizard and use app immediately."""
    return setup_store.mark_skipped()


@router.post("/setup/complete")
async def complete_setup() -> dict[str, Any]:
    """Mark setup wizard as completed."""
    return setup_store.mark_completed()


@router.patch("/setup/step")
async def update_setup_step(data: SetupStepUpdate) -> dict[str, Any]:
    """Update current setup wizard step."""
    try:
        return setup_store.update_step(data.step)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/setup/test")
async def run_setup_tests(data: SetupTestRequest) -> dict[str, Any]:
    """Run setup connectivity and configuration tests."""
    if data.ollama_base_url:
        settings.ollama_base_url = data.ollama_base_url
    if data.workspace_root:
        settings.workspace_root = data.workspace_root
    if data.openai_api_key is not None:
        settings.openai_api_key = data.openai_api_key
    if data.anthropic_api_key is not None:
        settings.anthropic_api_key = data.anthropic_api_key
    if data.gemini_api_key is not None:
        settings.gemini_api_key = data.gemini_api_key
    if data.groq_api_key is not None:
        settings.groq_api_key = data.groq_api_key
    if data.mistral_api_key is not None:
        settings.mistral_api_key = data.mistral_api_key

    results = await run_all_tests(
        ollama_url=data.ollama_base_url or settings.ollama_base_url,
        workspace_path=data.workspace_root or str(settings.workspace_root),
        test_generate=data.test_generate,
    )
    setup_store.save_test_results(results)
    return results


@router.post("/settings/test-models")
async def test_model_access(data: ModelAccessTestRequest) -> dict[str, Any]:
    """Test Ollama and configured model access from settings."""
    return await run_model_access_tests(
        ollama_url=data.ollama_base_url,
        default_model=data.default_model,
        openai_api_key=data.openai_api_key,
        anthropic_api_key=data.anthropic_api_key,
        gemini_api_key=data.gemini_api_key,
        groq_api_key=data.groq_api_key,
        mistral_api_key=data.mistral_api_key,
        test_inference=data.test_inference,
    )


@router.post("/setup/sync-models")
async def setup_sync_models() -> dict[str, Any]:
    """Import Ollama models during setup."""
    installed = await model_router.list_installed_models(force_refresh=True)
    added = model_store.sync_from_ollama(installed)
    return {"added": added, "count": len(added), "total": len(model_store.list_models())}


@router.get("/roles")
async def list_roles() -> list[AgentRole]:
    """List all available agent roles."""
    return role_registry.list_roles_localized()


@router.get("/llm/models")
async def list_ollama_models(refresh: bool = False) -> dict[str, Any]:
    """List models installed on the configured Ollama server."""
    models = await model_router.list_installed_models(force_refresh=refresh)
    return {
        "ollama_base_url": settings.ollama_base_url,
        "models": models,
        "count": len(models),
    }


@router.get("/llm/routing")
async def get_llm_routing() -> dict[str, Any]:
    """Return model routing overview with user overrides."""
    model_store.reload()
    installed = await model_router.list_installed_models()
    return {
        "auto_routing": settings.llm_auto_routing,
        "default_model": settings.default_model,
        "override_model": settings.override_model.strip() or None,
        "installed": installed,
        "tasks": model_store.routing_overview(installed),
        "routing": model_store.get_routing(),
        "models": model_store.list_models(),
    }


@router.get("/llm/catalog")
async def get_model_catalog() -> dict[str, Any]:
    """Return static model knowledge base."""
    return {
        "tasks": model_catalog.task_definitions(),
        "entries": model_catalog.entries(),
    }


@router.get("/llm/registry")
async def list_user_models() -> list[dict[str, Any]]:
    """List user-managed models."""
    model_store.reload()
    return model_store.list_models()


@router.post("/llm/registry/suggest")
async def suggest_model(data: UserModelCreate) -> dict[str, Any]:
    """Suggest metadata for a model tag from catalog."""
    return model_catalog.suggest_for_tag(data.ollama_tag)


@router.post("/llm/registry")
async def create_user_model(data: UserModelCreate) -> dict[str, Any]:
    """Add a user-managed model."""
    try:
        return model_store.add_model(
            ollama_tag=data.ollama_tag,
            display_name=data.display_name,
            assigned_tasks=data.assigned_tasks,
            enabled=data.enabled,
            notes=data.notes,
            auto_suggest=data.auto_suggest,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/llm/registry/{model_id}")
async def update_user_model(model_id: str, data: UserModelUpdate) -> dict[str, Any]:
    """Update a user-managed model."""
    try:
        return model_store.update_model(model_id, data.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/llm/registry/{model_id}")
async def delete_user_model(model_id: str) -> dict[str, bool]:
    """Delete a user-managed model."""
    try:
        model_store.delete_model(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.patch("/llm/routing")
async def update_routing(data: RoutingUpdate) -> dict[str, Any]:
    """Update per-task model routing overrides."""
    try:
        model_store.set_routing_bulk(data.routing)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    installed = await model_router.list_installed_models()
    return {
        "routing": model_store.get_routing(),
        "tasks": model_store.routing_overview(installed),
    }


@router.post("/llm/registry/sync")
async def sync_ollama_models() -> dict[str, Any]:
    """Import models from Ollama with catalog suggestions."""
    installed = await model_router.list_installed_models(force_refresh=True)
    added = model_store.sync_from_ollama(installed)
    return {"added": added, "count": len(added), "models": model_store.list_models()}


@router.post("/roles")
async def create_role(data: RoleCreate) -> AgentRole:
    """Create a custom agent role."""
    role = AgentRole(**data.model_dump(), is_builtin=False)
    return role_registry.add_role(role)


@router.get("/chats")
async def list_chats():
    """List all chat sessions."""
    return await conversation_store.list_chats()


@router.post("/chats")
async def create_chat(data: ChatCreate):
    """Create a new chat session."""
    return await conversation_store.create_chat(data)


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: str):
    """Get chat by ID."""
    try:
        return await conversation_store.get_chat(chat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found")


@router.patch("/chats/{chat_id}")
async def update_chat(chat_id: str, data: ChatUpdate):
    """Update chat metadata."""
    try:
        return await conversation_store.update_chat(chat_id, data)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found")


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str) -> dict[str, bool]:
    """Delete a chat session."""
    try:
        await conversation_store.get_chat(chat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found")
    await conversation_store.delete_chat(chat_id)
    return {"deleted": True}


@router.get("/chats/{chat_id}/messages")
async def list_messages(chat_id: str):
    """List messages for a chat."""
    try:
        await conversation_store.get_chat(chat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found")
    return await conversation_store.list_messages(chat_id)


class SendMessageRequest(BaseModel):
    """Send message with orchestration options."""

    content: str
    mode: OrchestrationMode | None = None
    role_ids: list[str] | None = None
    llm: LLMConfig | None = None
    retry: bool = False


@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, data: SendMessageRequest):
    """Send a user message and run orchestration."""
    try:
        chat = await conversation_store.get_chat(chat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found")

    mode = data.mode or chat.mode
    role_ids = data.role_ids if data.role_ids is not None else chat.role_ids
    orchestrator = AgentOrchestrator(data.llm)
    needs_title = _chat_needs_generated_title(chat.title, data.retry)
    llm_payload = data.llm.model_dump() if data.llm else None
    title_task = (
        asyncio.create_task(_generate_chat_title(chat_id, data.content, llm_payload))
        if needs_title
        else None
    )
    result = await orchestrator.run(
        chat_id,
        data.content,
        mode,
        role_ids,
        record_user_message=not data.retry,
    )
    if title_task is not None:
        result.title = await title_task
    return result


@router.get("/chats/{chat_id}/approvals")
async def list_approvals(chat_id: str):
    """List pending approvals for a chat."""
    return approval_manager.list_pending(chat_id)


@router.post("/chats/{chat_id}/approvals/{approval_id}")
async def respond_approval(chat_id: str, approval_id: str, data: ApprovalResponse):
    """Approve or deny a pending action."""
    orchestrator = AgentOrchestrator()
    msg = await orchestrator.execute_approved_command(chat_id, approval_id, data)
    await _broadcast_chat_event(
        chat_id,
        {
            "type": "approval_result",
            "approval_id": approval_id,
            "approved": data.approved,
            "message": json.loads(msg.model_dump_json()) if msg else None,
        },
    )
    return {"approved": data.approved, "message": msg}


@router.get("/chats/{chat_id}/memory")
async def get_memory(chat_id: str):
    """List memory entries for a chat."""
    return await memory_store.list_entries(chat_id)


@router.websocket("/ws/chats/{chat_id}")
async def chat_websocket(websocket: WebSocket, chat_id: str) -> None:
    """WebSocket for real-time orchestration events."""
    await websocket.accept()
    _CHAT_SOCKETS[chat_id].add(websocket)
    try:
        await conversation_store.get_chat(chat_id)
    except KeyError:
        _CHAT_SOCKETS[chat_id].discard(websocket)
        if not _CHAT_SOCKETS[chat_id]:
            del _CHAT_SOCKETS[chat_id]
        await websocket.close(code=4004)
        return

    intervention_queue: asyncio.Queue[str] = asyncio.Queue()
    current_task: asyncio.Task[None] | None = None

    async def on_event(event: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(event))

    async def execute_run(payload: dict[str, Any]) -> None:
        """Run orchestration for one user message."""
        try:
            chat = await conversation_store.get_chat(chat_id)
            needs_title = _chat_needs_generated_title(
                chat.title,
                bool(payload.get("retry")),
            )
            orchestrator = AgentOrchestrator(
                LLMConfig(**payload["llm"]) if payload.get("llm") else None
            )
            title_task = (
                asyncio.create_task(
                    _generate_chat_title(
                        chat_id,
                        payload["content"],
                        payload.get("llm"),
                    )
                )
                if needs_title
                else None
            )
            result = await orchestrator.run(
                chat_id,
                payload["content"],
                OrchestrationMode(payload.get("mode", chat.mode.value)),
                payload.get("role_ids", chat.role_ids),
                on_event=on_event,
                intervention_queue=intervention_queue,
                record_user_message=not bool(payload.get("retry")),
            )
            if title_task is not None:
                result.title = await title_task
            await websocket.send_text(json.dumps({
                "type": "complete",
                "result": json.loads(result.model_dump_json()),
            }))
        except asyncio.CancelledError:
            await websocket.send_text(json.dumps({"type": "stopped"}))
        except Exception as exc:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": str(exc),
            }))

    try:
        while True:
            raw = await websocket.receive_text()
            payload = json.loads(raw)
            msg_type = payload.get("type", "message")
            if msg_type == "stop":
                if current_task is not None and not current_task.done():
                    current_task.cancel()
                continue

            if msg_type not in ("message", "intervention"):
                continue

            content = str(payload.get("content", "")).strip()
            if not content:
                continue

            chat = await conversation_store.get_chat(chat_id)

            if current_task is not None and not current_task.done():
                await conversation_store.add_message(
                    chat_id,
                    MessageRole.USER,
                    content,
                )
                await intervention_queue.put(content)
                await websocket.send_text(json.dumps({
                    "type": "user_message",
                    "content": content,
                }))
                continue

            while not intervention_queue.empty():
                intervention_queue.get_nowait()

            current_task = asyncio.create_task(execute_run(payload))
    except WebSocketDisconnect:
        if current_task is not None and not current_task.done():
            current_task.cancel()
    finally:
        if chat_id in _CHAT_SOCKETS:
            _CHAT_SOCKETS[chat_id].discard(websocket)
            if not _CHAT_SOCKETS[chat_id]:
                del _CHAT_SOCKETS[chat_id]
