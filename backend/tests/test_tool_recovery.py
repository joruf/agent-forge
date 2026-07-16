"""Tests for tool-loop recovery when iteration limits are reached."""

from unittest.mock import AsyncMock

import pytest

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.workspace_intent import detect_workspace_intent


@pytest.mark.asyncio
async def test_recover_after_tool_limit_uses_tool_summaries(monkeypatch) -> None:
    """Recovery summarizes successful tool actions instead of showing a raw limit error."""
    orchestrator = AgentOrchestrator()
    llm = AsyncMock()
    llm.complete = AsyncMock(
        return_value={"content": "Finished the main structure.", "model": "ollama/mock"}
    )
    routing: dict = {"model": "ollama/mock"}
    intent = detect_workspace_intent("Fix index.php in GitHub/Test")

    monkeypatch.setattr(
        "agentforge.agents.orchestrator_mixins.tool_loop.missing_requested_files",
        lambda _content, _intent: [],
    )

    message = await orchestrator._recover_after_tool_limit(
        "recovery-chat",
        llm=llm,
        messages=[{"role": "user", "content": "Fix index.php"}],
        routing=routing,
        tool_summaries=["Created/updated file: GitHub/Test/index.php"],
        intent=intent,
        user_content="Fix index.php in GitHub/Test",
        role_id="developer",
    )

    assert "Reached maximum tool iterations" not in message
    assert "Created/updated file: GitHub/Test/index.php" in message
    assert "Finished the main structure." in message


@pytest.mark.asyncio
async def test_recover_after_tool_limit_materializes_missing_files(monkeypatch) -> None:
    """Recovery writes missing files when the tool loop stops too early."""
    orchestrator = AgentOrchestrator()
    llm = AsyncMock()
    routing: dict = {"model": "ollama/mock"}
    prompt = (
        "Erstelle index.php mit Header, Menü, Content und Footer und speichere unter "
        "/home/joruf/Dokumente/GitHub/Test"
    )
    intent = detect_workspace_intent(prompt)

    monkeypatch.setattr(
        "agentforge.agents.orchestrator_mixins.tool_loop.missing_requested_files",
        lambda _content, _intent: ["GitHub/Test/index.php"],
    )

    async def fake_guarantee(
        self,
        chat_id: str,
        user_content: str,
        intent,
        role_id: str = "developer",
        on_event=None,
    ) -> str:
        assert chat_id == "recovery-chat"
        return "Created files on disk:\n- GitHub/Test/index.php"

    monkeypatch.setattr(
        AgentOrchestrator,
        "_guarantee_workspace_deliverables",
        fake_guarantee,
    )

    message = await orchestrator._recover_after_tool_limit(
        "recovery-chat",
        llm=llm,
        messages=[{"role": "user", "content": prompt}],
        routing=routing,
        tool_summaries=[],
        intent=intent,
        user_content=prompt,
        role_id="developer",
    )

    assert "Created files on disk" in message
    assert "GitHub/Test/index.php" in message


def test_developer_file_creation_gets_full_tool_budget() -> None:
    """Developers receive the full tool budget when files must be written."""
    orchestrator = AgentOrchestrator()
    intent = detect_workspace_intent("Speichere index.php unter GitHub/Test")
    assert orchestrator._effective_tool_round_limit(
        "developer",
        False,
        True,
        workspace_intent=intent,
    ) == orchestrator.max_tool_rounds
