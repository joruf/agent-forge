"""Tests for workspace intent detection."""

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.config import settings


def test_detect_file_creation_with_absolute_path(monkeypatch) -> None:
    """German save requests with absolute paths are detected."""
    monkeypatch.setattr(settings, "workspace_root", settings.workspace_root)
    prompt = (
        "ich benötige ein programm in php es soll ein header beinhalten mit menü, "
        "content und footer. erstellt mir einen entwurf und speichert den code ab "
        "unter: /home/joruf/Dokumente/GitHub/Test"
    )
    intent = detect_workspace_intent(prompt)

    assert intent.wants_file_creation is True
    assert intent.requires_tools is True
    assert "GitHub/Test" in intent.target_dirs
    assert "/home/joruf/Dokumente/GitHub/Test" in intent.raw_paths


def test_detect_command_intent() -> None:
    """Shell execution requests are detected."""
    intent = detect_workspace_intent("Führe den Befehl npm install im Terminal aus.")
    assert intent.wants_command_execution is True
    assert intent.requires_tools is True


def test_prompt_needs_tools_for_save_request() -> None:
    """Save-to-path requests enable tools even without English keywords."""
    prompt = "Erstelle index.php und speichere unter /home/joruf/Dokumente/GitHub/Test"
    assert AgentOrchestrator._prompt_needs_tools(prompt, "developer") is True


def test_parse_fenced_write_file_tool_call() -> None:
    """JSON tool calls inside markdown fences are parsed."""
    content = (
        'Here is the action:\n```json\n'
        '{"function": "write_file", "arguments": {"path": "GitHub/Test/index.php", '
        '"content": "<?php echo 1;"}}\n```'
    )
    calls = AgentOrchestrator._parse_content_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "write_file"
    assert "index.php" in calls[0]["arguments"]


def test_parse_ignores_template_json_without_tool_name() -> None:
    """Template JSON without a known tool name is not executed."""
    content = (
        '{"header": {"name": "default", "content": "<!DOCTYPE html><html></html>"}}'
    )
    calls = AgentOrchestrator._parse_content_tool_calls(content)
    assert calls == []


def test_looks_like_code_only_output() -> None:
    """Code pasted in chat is detected for tool-use nudging."""
    assert AgentOrchestrator._looks_like_code_only_output("```php\n<?php\n```") is True
    assert AgentOrchestrator._looks_like_code_only_output("All done.") is False
