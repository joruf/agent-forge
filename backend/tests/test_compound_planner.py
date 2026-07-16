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

SIX_STEP_H2_INSERT_PROMPT = (
    f"erstelle mir einen Ordner mit dem Namen Test12\n"
    f"im Verzeichnis\n{WORKSPACE}\n"
    f"darin eine Datei mit dem Namen index.html\n"
    f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
    f"lese danach die Datei {WORKSPACE}/index.html aus und gebe den Inhalt hier im Prompt aus.\n"
    f"erstelle danach in der datei {WORKSPACE}/index.html unter dem H1 Tag einen H2 Tag "
    f'mit der Beschriftung "Hello Bot".\n'
    "erstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
    "H2-Tag der erstellten HTML-Datei und hat die Dateiendung .txt"
)

SEVEN_STEP_H3_AND_1TXT_PROMPT = (
    f"erstelle mir Verzeichnis mit dem Namen Test12\n"
    f"im Ordner {WORKSPACE}\n"
    f"dort eine Datei mit dem Namen index.html erstellen\n"
    f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
    f"lese danach die Datei {WORKSPACE}/index.html aus und geb den Inhalt hier im Prompt aus.\n"
    f"erstelle danach in der datei {WORKSPACE}/index.html unter dem H1 Tag einen H3 Tag "
    f'mit der Beschriftung "Hello Bot".\n'
    "erstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
    "H3-Tag der erstellten HTML-Datei und hat die Dateiendung .txt\n"
    "erstelle danach die txt datei 1.txt und schreibe den text vom H1 Tag des HTML Datei rein"
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


def test_compound_plan_h2_insert_and_h2_derived_agenda() -> None:
    """H2 insertion is an edit step; derived txt uses H2 naming source."""
    intent = detect_workspace_intent(SIX_STEP_H2_INSERT_PROMPT)
    agenda = build_workspace_agenda(SIX_STEP_H2_INSERT_PROMPT, intent)

    assert len(agenda) == 5
    assert agenda[3].action == AgendaAction.EDIT_FILE
    assert agenda[3].insert_heading_text == "Hello Bot"
    assert agenda[3].insert_after_heading == 1
    assert agenda[3].insert_heading_level == 2
    assert agenda[4].action == AgendaAction.WRITE_DERIVED_FILE
    assert agenda[4].naming_source == "h2"


def test_seven_step_prompt_with_explicit_1txt_and_h3_derived() -> None:
    """Seven-step workflow yields ordered steps without duplicate index.html writes."""
    intent = detect_workspace_intent(SEVEN_STEP_H3_AND_1TXT_PROMPT)
    agenda = build_workspace_agenda(SEVEN_STEP_H3_AND_1TXT_PROMPT, intent)

    assert len(agenda) == 6
    assert agenda[0].action == AgendaAction.CREATE_DIRECTORY
    assert agenda[0].path == "GitHub/Test12"
    assert agenda[1].action == AgendaAction.WRITE_FILE
    assert agenda[1].path == "GitHub/Test12/index.html"
    assert agenda[2].action == AgendaAction.READ_FILE
    assert agenda[2].path == "GitHub/Test12/index.html"
    assert agenda[3].action == AgendaAction.EDIT_FILE
    assert agenda[3].insert_heading_level == 3
    assert agenda[3].insert_heading_text == "Hello Bot"
    assert agenda[4].action == AgendaAction.WRITE_DERIVED_FILE
    assert agenda[4].naming_source == "h3"
    assert agenda[5].action == AgendaAction.WRITE_FILE
    assert agenda[5].path == "GitHub/Test12/1.txt"
    assert agenda[5].content_from_heading == "h1"
    assert agenda[5].content_source_path == "GitHub/Test12/index.html"

    write_steps = [step for step in agenda if step.action == AgendaAction.WRITE_FILE]
    assert len(write_steps) == 2
    assert write_steps[0].path == "GitHub/Test12/index.html"
    assert write_steps[1].path == "GitHub/Test12/1.txt"
