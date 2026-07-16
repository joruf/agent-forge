"""Tests for workspace execution agendas."""

from __future__ import annotations

from agentforge.agents.task_state import TaskType, build_task_state, classify_task_type
from agentforge.agents.workspace_agenda import (
    AgendaAction,
    build_workspace_agenda,
    format_agenda_block,
)
from agentforge.agents.workspace_intent import detect_workspace_intent

WORKSPACE = "/home/joruf/Dokumente/GitHub"

TEST12_WORKFLOW_PROMPT = (
    f"erstelle mir einen Ordner mit dem Namen Test12\n"
    f"im Verzeichnis\n{WORKSPACE}\n"
    f"darin eine Datei mit dem Namen index.html\n"
    f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
    f"lese danach die Datei {WORKSPACE}/index.html aus und geb den Inhalt hier im Prompt aus.\n"
    f'bearbeite danach die {WORKSPACE}/index.html und tausche "Hello World" aus gegen "Hello Bot".'
)


def test_test12_workflow_agenda_order() -> None:
    """The Test12 prompt yields create, write, read, and edit steps in order."""
    intent = detect_workspace_intent(TEST12_WORKFLOW_PROMPT)
    agenda = build_workspace_agenda(TEST12_WORKFLOW_PROMPT, intent)

    assert len(agenda) == 4
    assert agenda[0].action == AgendaAction.CREATE_DIRECTORY
    assert agenda[0].path == "GitHub/Test12"
    assert agenda[1].action == AgendaAction.WRITE_FILE
    assert agenda[1].path == "GitHub/Test12/index.html"
    assert agenda[2].action == AgendaAction.READ_FILE
    assert agenda[2].path == "GitHub/Test12/index.html"
    assert agenda[3].action == AgendaAction.EDIT_FILE
    assert agenda[3].path == "GitHub/Test12/index.html"
    assert agenda[3].replace_from == "Hello World"
    assert agenda[3].replace_to == "Hello Bot"


DERIVED_TXT_PROMPT = (
    TEST12_WORKFLOW_PROMPT
    + "\nerstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
    "H1-Tag der erstellten HTML-Datei und hat die Dateiendung .txt"
)


def test_test12_workflow_with_derived_txt_agenda_has_fifth_step() -> None:
    """Compound prompts with H1-derived .txt append a write_derived_file step."""
    intent = detect_workspace_intent(DERIVED_TXT_PROMPT)
    agenda = build_workspace_agenda(DERIVED_TXT_PROMPT, intent)

    assert intent.wants_derived_file is True
    assert len(agenda) == 5
    assert agenda[4].action == AgendaAction.WRITE_DERIVED_FILE
    assert agenda[4].source_path == "GitHub/Test12/index.html"
    assert agenda[4].naming_source == "h1"
    assert agenda[4].derived_extension == ".txt"


def test_test12_workflow_classified_as_workflow_task() -> None:
    """Compound create-read-edit prompts map to the workflow task type."""
    intent = detect_workspace_intent(TEST12_WORKFLOW_PROMPT)

    assert intent.wants_file_creation is True
    assert intent.wants_file_read is True
    assert intent.wants_file_edit is True
    assert classify_task_type(intent) == TaskType.WORKFLOW


def test_task_state_plan_uses_numbered_agenda() -> None:
    """Task board plan steps mirror the numbered execution agenda."""
    intent = detect_workspace_intent(TEST12_WORKFLOW_PROMPT)
    state = build_task_state(TEST12_WORKFLOW_PROMPT, intent)

    assert state.task_type == TaskType.WORKFLOW
    assert len(state.plan_steps) == 4
    assert state.plan_steps[0].step_id == 1
    assert state.plan_steps[0].action == "create_directory"
    assert state.plan_steps[3].action == "edit_file"
    assert state.targets == [
        "GitHub/Test12",
        "GitHub/Test12/index.html",
    ]


def test_format_agenda_block_is_numbered() -> None:
    """Formatted agenda output uses explicit 1..N numbering."""
    intent = detect_workspace_intent(TEST12_WORKFLOW_PROMPT)
    agenda = build_workspace_agenda(TEST12_WORKFLOW_PROMPT, intent)
    block = format_agenda_block(agenda)

    assert "1. create_directory" in block
    assert "4. edit_file" in block
    assert "Hello Bot" in block
