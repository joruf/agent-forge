"""Tests for FastAPI HTTP routes."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.role_registry import RoleRegistry
from agentforge.config import settings
from agentforge.main import app
from agentforge.storage.conversation_store import conversation_store
from agentforge.storage.model_store import model_store
from agentforge.storage.setup_store import setup_store


@pytest.fixture
def api_client(temp_data_dir, temp_workspace, monkeypatch):
    """Test client with isolated storage paths."""
    db_path = temp_data_dir / "api.db"
    monkeypatch.setattr(settings, "data_dir", temp_data_dir)
    monkeypatch.setattr(settings, "workspace_root", temp_workspace)
    monkeypatch.setattr(settings, "ui_language", "en")
    monkeypatch.setattr(conversation_store, "db_path", str(db_path))
    monkeypatch.setattr(setup_store, "path", temp_data_dir / "setup_state.json")
    monkeypatch.setattr(model_store, "config_path", temp_data_dir / "model_config.json")
    model_store.reload()
    asyncio.run(conversation_store.initialize())

    with TestClient(app) as client:
        yield client


def test_health(api_client: TestClient) -> None:
    """Health endpoint returns ok status."""
    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness(api_client: TestClient, monkeypatch) -> None:
    """Readiness endpoint returns chat readiness report."""

    async def fake_readiness(**kwargs):
        return {
            "chat_ready": True,
            "active_model": "ollama/llama3.1:8b",
            "results": [],
            "summary": "Models ready for chat",
            "blocking_message": None,
            "blocking_id": None,
        }

    monkeypatch.setattr("agentforge.api.routes.run_readiness_check", fake_readiness)
    response = api_client.get("/api/readiness")
    assert response.status_code == 200
    assert response.json()["chat_ready"] is True


def test_settings_get_and_patch(api_client: TestClient) -> None:
    """Settings can be read and updated."""
    get_resp = api_client.get("/api/settings")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert "workspace_root" in body
    assert "has_openai_key" in body
    assert "has_anthropic_key" in body
    assert "has_gemini_key" in body

    patch_resp = api_client.patch(
        "/api/settings",
        json={"default_memory_tokens": 64000, "ui_language": "de", "anthropic_api_key": "sk-ant-test"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["default_memory_tokens"] == 64000
    assert patched["ui_language"] == "de"
    assert patched["has_anthropic_key"] is True


def test_setup_workflow(api_client: TestClient) -> None:
    """Setup status, skip, and resume endpoints work."""
    status = api_client.get("/api/setup/status").json()
    assert "should_show_wizard" in status

    skipped = api_client.post("/api/setup/skip").json()
    assert skipped["skipped"] is True

    resumed = api_client.post("/api/setup/resume").json()
    assert resumed["skipped"] is False


def test_roles_list(api_client: TestClient) -> None:
    """Roles endpoint returns built-in roles."""
    response = api_client.get("/api/roles")
    assert response.status_code == 200
    roles = response.json()
    assert any(role["id"] == "developer" for role in roles)


def test_roles_crud(api_client: TestClient, temp_data_dir, monkeypatch) -> None:
    """Custom roles can be created, updated, and deleted via API."""
    roles_dir = temp_data_dir / "roles"
    roles_dir.mkdir()
    isolated_registry = RoleRegistry(roles_dir=roles_dir)
    monkeypatch.setattr("agentforge.api.routes.role_registry", isolated_registry)

    create = api_client.post(
        "/api/roles",
        json={
            "id": "api_custom_role",
            "name": "API Custom",
            "description": "Created via API",
            "system_prompt": "You are a custom API role.",
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["id"] == "api_custom_role"
    assert created["is_builtin"] is False

    duplicate = api_client.post(
        "/api/roles",
        json={
            "id": "api_custom_role",
            "name": "Duplicate",
            "description": "Duplicate",
            "system_prompt": "Duplicate",
        },
    )
    assert duplicate.status_code == 409

    update = api_client.put(
        "/api/roles/api_custom_role",
        json={
            "name": "API Custom Updated",
            "description": "Updated via API",
            "system_prompt": "Updated prompt.",
        },
    )
    assert update.status_code == 200
    assert update.json()["name"] == "API Custom Updated"

    delete = api_client.delete("/api/roles/api_custom_role")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True

    missing = api_client.delete("/api/roles/api_custom_role")
    assert missing.status_code == 404


def test_roles_builtin_protected(api_client: TestClient, temp_data_dir, monkeypatch) -> None:
    """Built-in roles cannot be updated or deleted."""
    roles_dir = temp_data_dir / "roles"
    roles_dir.mkdir()
    isolated_registry = RoleRegistry(roles_dir=roles_dir)
    monkeypatch.setattr("agentforge.api.routes.role_registry", isolated_registry)

    update = api_client.put(
        "/api/roles/developer",
        json={
            "name": "Hacker",
            "description": "No",
            "system_prompt": "No",
        },
    )
    assert update.status_code == 400

    delete = api_client.delete("/api/roles/developer")
    assert delete.status_code == 400


def test_chat_crud(api_client: TestClient) -> None:
    """Chat create, list, update, delete lifecycle."""
    create = api_client.post(
        "/api/chats",
        json={
            "title": "API Chat",
            "mode": "single",
            "execution_strategy": "auto",
            "role_ids": [],
        },
    )
    assert create.status_code == 200
    created_chat = create.json()
    chat_id = created_chat["id"]
    assert created_chat["execution_strategy"] == "auto"

    listed = api_client.get("/api/chats").json()
    assert any(chat["id"] == chat_id for chat in listed)

    updated = api_client.patch(
        f"/api/chats/{chat_id}",
        json={"title": "Renamed", "execution_strategy": "hybrid"},
    ).json()
    assert updated["title"] == "Renamed"
    assert updated["execution_strategy"] == "hybrid"

    deleted = api_client.delete(f"/api/chats/{chat_id}").json()
    assert deleted["deleted"] is True


def test_model_registry_crud(api_client: TestClient, monkeypatch) -> None:
    """User model registry supports add and delete."""

    async def fake_installed(force_refresh=False):
        return ["test-api:7b"]

    monkeypatch.setattr(
        "agentforge.api.routes.model_router.list_installed_models",
        fake_installed,
    )

    created = api_client.post(
        "/api/llm/registry",
        json={"ollama_tag": "test-api:7b", "auto_suggest": False},
    )
    assert created.status_code == 200
    model_id = created.json()["id"]

    listed = api_client.get("/api/llm/registry").json()
    assert any(model["id"] == model_id for model in listed)

    deleted = api_client.delete(f"/api/llm/registry/{model_id}").json()
    assert deleted["deleted"] is True


def test_llm_catalog(api_client: TestClient) -> None:
    """Model catalog endpoint exposes tasks and entries."""
    response = api_client.get("/api/llm/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert "tasks" in payload
    assert "entries" in payload
    assert "coding" in payload["tasks"]


def test_settings_model_access(api_client: TestClient, monkeypatch) -> None:
    """Settings model access test endpoint returns a report."""

    async def fake_model_access(**kwargs):
        return {
            "all_required_ok": True,
            "results": [{"id": "ollama", "label": "Ollama", "ok": True, "message": "ok"}],
            "summary": "Model access OK — all checks passed",
            "optional_issues": 0,
        }

    monkeypatch.setattr(
        "agentforge.api.routes.run_model_access_tests",
        fake_model_access,
    )
    response = api_client.post(
        "/api/settings/test-models",
        json={"ollama_base_url": "http://fake:11434", "test_inference": False},
    )
    assert response.status_code == 200
    assert response.json()["all_required_ok"] is True


def test_approval_resume_single_agent_flow(api_client: TestClient, monkeypatch) -> None:
    """Approved command resumes single-agent flow and stores assistant output."""

    async def fake_resume(self, state, command_output):
        assert state.agent_id == "developer"
        assert "resumed-single" in command_output
        return "Single flow resumed.", {"model": "ollama/mock-single"}

    monkeypatch.setattr(AgentOrchestrator, "_resume_after_approval", fake_resume)

    create = api_client.post(
        "/api/chats",
        json={"title": "Resume Single", "mode": "single", "role_ids": ["developer"]},
    )
    assert create.status_code == 200
    chat_id = create.json()["id"]

    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo resumed-single",
            {"command": "echo resumed-single", "cwd": None},
        )
    )
    approval_manager.set_resume_state(
        approval_id,
        {
            "chat_id": chat_id,
            "agent_id": "developer",
            "agent_name": "Developer",
            "role_id": "developer",
            "user_content": "Fix this issue",
            "mode_single": True,
            "memory_scope": "chat",
            "routing": {"model": "ollama/mock-single"},
            "messages": [],
            "tool_call_id": "call_single",
        },
    )

    response = api_client.post(
        f"/api/chats/{chat_id}/approvals/{approval_id}",
        json={"approved": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["approved"] is True
    assert payload["message"]["role"] == "assistant"
    assert payload["message"]["agent_id"] == "developer"
    assert payload["message"]["content"] == "Single flow resumed."
    assert payload["message"]["metadata"]["resumed_from_approval"] is True
    assert payload["message"]["metadata"]["approval_id"] == approval_id

    messages = api_client.get(f"/api/chats/{chat_id}/messages").json()
    assert any(
        msg["role"] == "tool"
        and msg["metadata"].get("approval_id") == approval_id
        for msg in messages
    )
    assert any(
        msg["role"] == "assistant"
        and msg["metadata"].get("resumed_from_approval") is True
        and msg["agent_id"] == "developer"
        for msg in messages
    )
    assert approval_manager.list_pending(chat_id) == []


def test_approval_resume_multi_agent_flow(api_client: TestClient, monkeypatch) -> None:
    """Approved command resumes multi-agent role flow and stores assistant output."""

    async def fake_resume(self, state, command_output):
        assert state.agent_id == "reviewer"
        assert "resumed-multi" in command_output
        return "Multi flow resumed.", {"model": "ollama/mock-multi"}

    monkeypatch.setattr(AgentOrchestrator, "_resume_after_approval", fake_resume)

    create = api_client.post(
        "/api/chats",
        json={
            "title": "Resume Multi",
            "mode": "multi",
            "role_ids": ["project_manager", "reviewer"],
        },
    )
    assert create.status_code == 200
    chat_id = create.json()["id"]

    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo resumed-multi",
            {"command": "echo resumed-multi", "cwd": None},
        )
    )
    approval_manager.set_resume_state(
        approval_id,
        {
            "chat_id": chat_id,
            "agent_id": "reviewer",
            "agent_name": "Reviewer",
            "role_id": "reviewer",
            "user_content": "Review current patch",
            "mode_single": False,
            "memory_scope": "chat",
            "routing": {"model": "ollama/mock-multi"},
            "messages": [],
            "tool_call_id": "call_multi",
        },
    )

    response = api_client.post(
        f"/api/chats/{chat_id}/approvals/{approval_id}",
        json={"approved": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["approved"] is True
    assert payload["message"]["role"] == "assistant"
    assert payload["message"]["agent_id"] == "reviewer"
    assert payload["message"]["content"] == "Multi flow resumed."
    assert payload["message"]["metadata"]["resumed_from_approval"] is True
    assert payload["message"]["metadata"]["approval_id"] == approval_id

    messages = api_client.get(f"/api/chats/{chat_id}/messages").json()
    assert any(
        msg["role"] == "tool"
        and msg["metadata"].get("approval_id") == approval_id
        for msg in messages
    )
    assert any(
        msg["role"] == "assistant"
        and msg["metadata"].get("resumed_from_approval") is True
        and msg["agent_id"] == "reviewer"
        for msg in messages
    )
    assert approval_manager.list_pending(chat_id) == []


def test_approval_response_broadcasts_websocket_event(api_client: TestClient) -> None:
    """Approval response emits an approval_result event to chat websocket clients."""
    create = api_client.post(
        "/api/chats",
        json={"title": "Approval WS", "mode": "single", "role_ids": ["developer"]},
    )
    assert create.status_code == 200
    chat_id = create.json()["id"]

    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo ws-approval",
            {"command": "echo ws-approval", "cwd": None},
        )
    )

    with api_client.websocket_connect(f"/api/ws/chats/{chat_id}") as websocket:
        response = api_client.post(
            f"/api/chats/{chat_id}/approvals/{approval_id}",
            json={"approved": False},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["approved"] is False

        event = websocket.receive_json()
        assert event["type"] == "approval_result"
        assert event["approval_id"] == approval_id
        assert event["approved"] is False
        assert event["message"] is not None
        assert event["message"]["metadata"]["status"] == "denied"


def test_approval_response_broadcasts_resumed_message_event(
    api_client: TestClient,
    monkeypatch,
) -> None:
    """Approval response emits approval_result with resumed assistant message."""

    async def fake_resume(self, state, command_output):
        return "WS resumed output.", {"model": "ollama/mock-ws"}

    monkeypatch.setattr(AgentOrchestrator, "_resume_after_approval", fake_resume)

    create = api_client.post(
        "/api/chats",
        json={"title": "Approval WS Resume", "mode": "single", "role_ids": ["developer"]},
    )
    assert create.status_code == 200
    chat_id = create.json()["id"]

    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo ws-resume",
            {"command": "echo ws-resume", "cwd": None},
        )
    )
    approval_manager.set_resume_state(
        approval_id,
        {
            "chat_id": chat_id,
            "agent_id": "developer",
            "agent_name": "Developer",
            "role_id": "developer",
            "user_content": "Resume websocket flow",
            "mode_single": True,
            "memory_scope": "chat",
            "routing": {"model": "ollama/mock-ws"},
            "messages": [],
            "tool_call_id": "call_ws",
        },
    )

    with api_client.websocket_connect(f"/api/ws/chats/{chat_id}") as websocket:
        response = api_client.post(
            f"/api/chats/{chat_id}/approvals/{approval_id}",
            json={"approved": True},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["approved"] is True

        event = websocket.receive_json()
        assert event["type"] == "approval_result"
        assert event["approval_id"] == approval_id
        assert event["approved"] is True
        assert event["message"] is not None
        assert event["message"]["role"] == "assistant"
        assert event["message"]["content"] == "WS resumed output."
        assert event["message"]["metadata"]["resumed_from_approval"] is True


def test_user_choice_approval_resumes_agenda_pipeline(
    api_client: TestClient,
    monkeypatch,
) -> None:
    """User-choice approval resumes the workspace agenda pipeline."""

    async def fake_resume(self, resume_state, response, on_event=None):
        from agentforge.models.schemas import MessageRole
        from agentforge.storage.conversation_store import conversation_store

        return await conversation_store.add_message(
            resume_state.chat_id,
            MessageRole.ASSISTANT,
            "Agenda pipeline:\n- Created `demo.txt` with h3 text from `index.html`",
            metadata={"kind": "agenda_user_choice", "choice_id": response.choice_id},
        )

    monkeypatch.setattr(AgentOrchestrator, "_resume_agenda_after_user_choice", fake_resume)

    create = api_client.post(
        "/api/chats",
        json={"title": "User Choice", "mode": "multi", "role_ids": ["developer"]},
    )
    assert create.status_code == 200
    chat_id = create.json()["id"]

    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "user_choice",
            "Could not create `demo.txt`: no `<h2>` found in `index.html`.",
            {
                "question": "Could not create `demo.txt`: no `<h2>` found in `index.html`.",
                "options": [
                    {"id": "use_h3", "label": "Use <h3> instead", "description": ""},
                    {"id": "skip", "label": "Skip this write step", "description": ""},
                    {"id": "abort", "label": "Abort remaining workflow", "description": ""},
                ],
                "context": {},
            },
        )
    )
    approval_manager.set_resume_state(
        approval_id,
        {
            "chat_id": chat_id,
            "user_content": "Create demo.txt from h2 in index.html",
            "intent": {"wants_file_creation": True, "target_paths": ["demo.txt"]},
            "task_state_snapshot": None,
            "step_index": 2,
            "step_path": "demo.txt",
            "requested_tag": "h2",
            "content_source_path": "index.html",
            "prefetched_reads": {},
        },
    )

    response = api_client.post(
        f"/api/chats/{chat_id}/approvals/{approval_id}",
        json={"approved": True, "choice_id": "use_h3"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["approved"] is True
    assert payload["message"]["metadata"]["kind"] == "agenda_user_choice"
    assert payload["message"]["metadata"]["choice_id"] == "use_h3"
    assert approval_manager.list_pending(chat_id) == []

