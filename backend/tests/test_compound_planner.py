"""Tests for compound workspace prompt planning."""

from __future__ import annotations

from agentforge.agents.compound_planner import (
    ClauseAction,
    build_compound_plan,
    is_compound_request,
    split_into_clauses,
)
from agentforge.agents.workspace_agenda import AgendaAction, build_workspace_agenda
from agentforge.agents.workspace_intent import detect_workspace_intent

WORKSPACE = "/home/joruf/Dokumente/GitHub"

SIX_STEP_PROMPT = (
    f"erstelle mir einen Ordner mit dem Namen Test12\n"
    f"im Verzeichnis\n{WORKSPACE}\n"
    f"darin eine Datei mit dem Namen index.html\n"
    f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
    f"lese danach die Datei {WORKSPACE}/index.html aus und gebe den Inhalt hier im Prompt aus.\n"
    f'bearbeite danach die {WORKSPACE}/index.html und tausche "Hello World" aus gegen "Hello Bot".\n'
    "erstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
    "H1-Tag der erstellten HTML-Datei und hat die Dateiendung .txt"
)


def test_split_into_clauses_preserves_order() -> None:
    """Temporal markers and newlines produce ordered clauses."""
    clauses = split_into_clauses(SIX_STEP_PROMPT)
    assert len(clauses) >= 5
    assert "Ordner" in clauses[0]
    assert any("index.html" in clause for clause in clauses)
    assert any("lese" in clause.lower() for clause in clauses)
    assert any("bearbeite" in clause.lower() for clause in clauses)
    assert "H1-Tag" in clauses[-1]


def test_is_compound_request_detects_multi_step_prompt() -> None:
    """Long prompts with multiple actions are treated as compound."""
    assert is_compound_request(SIX_STEP_PROMPT) is True
    assert is_compound_request("lese die Datei GitHub/demo.txt") is False


def test_compound_plan_links_created_html_reference() -> None:
    """The derived txt clause references the created HTML artifact."""
    intent = detect_workspace_intent(SIX_STEP_PROMPT)
    plan = build_compound_plan(SIX_STEP_PROMPT, intent)

    derived = [clause for clause in plan.clauses if clause.action == ClauseAction.WRITE_DERIVED_FILE]
    assert len(derived) == 1
    assert derived[0].references_created_html is True
    assert derived[0].source_path == "GitHub/Test12/index.html"


def test_compound_plan_builds_five_step_agenda() -> None:
    """Compound planning yields the full create-write-read-edit-derived agenda."""
    intent = detect_workspace_intent(SIX_STEP_PROMPT)
    agenda = build_workspace_agenda(SIX_STEP_PROMPT, intent)

    assert len(agenda) == 5
    assert agenda[0].action == AgendaAction.CREATE_DIRECTORY
    assert agenda[1].action == AgendaAction.WRITE_FILE
    assert agenda[1].path == "GitHub/Test12/index.html"
    assert agenda[2].action == AgendaAction.READ_FILE
    assert agenda[2].path == "GitHub/Test12/index.html"
    assert agenda[3].action == AgendaAction.EDIT_FILE
    assert agenda[4].action == AgendaAction.WRITE_DERIVED_FILE
    assert agenda[4].source_path == "GitHub/Test12/index.html"
