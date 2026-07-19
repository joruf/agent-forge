"""Tests for workspace intent detection."""

from pathlib import Path

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.workspace_intent import (
    PATH_AFTER_KEYWORD,
    _extract_paths,
    detect_workspace_intent,
)
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


def test_detect_file_creation_with_home_relative_github_path(monkeypatch) -> None:
    """Absolute paths under home that omit the workspace root segment are mapped."""
    from pathlib import Path

    home = Path.home()
    monkeypatch.setattr(settings, "workspace_root", home / "Dokumente")
    prompt = (
        f"Create a PHP email program at {home / 'GitHub' / 'emailsender'} "
        "named SimpleEmailSender.php"
    )
    intent = detect_workspace_intent(prompt)

    assert intent.wants_file_creation is True
    assert "GitHub/emailsender" in intent.target_dirs
    assert "GitHub/emailsender/SimpleEmailSender.php" in intent.target_paths


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


def test_path_after_keyword_ignores_embedded_partial_paths() -> None:
    """Partial path fragments inside longer paths must not be extracted."""
    text = "GitHub/emailsender/for/index.php"
    matches = [match.group(1) for match in PATH_AFTER_KEYWORD.finditer(text)]
    assert matches == []


def test_extract_paths_prefers_full_absolute_target(monkeypatch) -> None:
    """Absolute user targets win over embedded partial path fragments."""
    home = Path.home()
    monkeypatch.setattr(settings, "workspace_root", home / "Dokumente")
    prompt = (
        f"erstelle mir ein Programm unter {home / 'Dokumente' / 'GitHub' / 'emailsender'}\n"
        "GitHub/emailsender/for/index.php"
    )
    extracted = _extract_paths(prompt)
    assert f"{home / 'Dokumente' / 'GitHub' / 'emailsender'}" in extracted
    assert "/emailsender/for/index.php" not in extracted


def test_emailsender_prompt_maps_to_canonical_directory(monkeypatch) -> None:
    """Email sender requests resolve to GitHub/emailsender without extra segments."""
    home = Path.home()
    monkeypatch.setattr(settings, "workspace_root", home / "Dokumente")
    prompt = (
        f"erstelle mir ein Programm unter {home / 'Dokumente' / 'GitHub' / 'emailsender'}/ "
        "das programm soll in php eine email an einen empfänger senden"
    )
    intent = detect_workspace_intent(prompt)

    assert "GitHub/emailsender" in intent.target_dirs
    assert "GitHub/emailsender/for" not in intent.target_dirs
