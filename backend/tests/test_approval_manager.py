"""Tests for approval manager state handling."""

from agentforge.agents.approval_manager import ApprovalManager
from agentforge.models.schemas import ApprovalResumeState


def test_resume_state_roundtrip() -> None:
    """Resume state can be stored and popped by approval ID."""
    manager = ApprovalManager()
    manager.set_resume_state(
        "approval-1",
        {
            "chat_id": "chat-1",
            "agent_id": "developer",
            "agent_name": "Developer",
            "role_id": "developer",
            "user_content": "Continue",
            "mode_single": True,
            "memory_scope": "chat",
            "routing": {"model": "ollama/mock"},
            "messages": [],
            "tool_call_id": "call_1",
        },
    )
    state = manager.pop_resume_state("approval-1")
    assert isinstance(state, ApprovalResumeState)
    assert state.agent_id == "developer"
    assert state.tool_call_id == "call_1"
    assert manager.pop_resume_state("approval-1") is None
