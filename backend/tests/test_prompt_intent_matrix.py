"""Parametrized prompt-to-intent quality tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentforge.agents.task_state import TaskType, build_task_state, classify_task_type
from agentforge.agents.workspace_executor import plan_deliverable_files, resolve_read_file_paths
from agentforge.agents.workspace_intent import detect_workspace_intent, extract_named_folder


@dataclass(frozen=True)
class PromptIntentCase:
    """One prompt and its expected workspace intent classification."""

    id: str
    prompt: str
    task_type: TaskType
    wants_read: bool = False
    wants_write: bool = False
    wants_list: bool = False
    wants_command: bool = False
    target_paths: tuple[str, ...] = ()
    target_dirs: tuple[str, ...] = ()
    planned_files: tuple[str, ...] = ()
    read_paths: tuple[str, ...] = ()
    named_folder: str | None = None
    requires_tools: bool = False


WORKSPACE = "/home/joruf/Dokumente/GitHub"


PROMPT_INTENT_CASES: tuple[PromptIntentCase, ...] = (
    PromptIntentCase(
        id="read_german_absolute_txt",
        prompt=(
            "lese den dateiinhalt von /home/joruf/Dokumente/GitHub/Test12/test12345.txt "
            "und liste mir den inhalt hier auf"
        ),
        task_type=TaskType.READ_AND_DISPLAY,
        wants_read=True,
        target_paths=("GitHub/Test12/test12345.txt",),
        read_paths=("GitHub/Test12/test12345.txt",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="read_english_show_content",
        prompt="Read /home/joruf/Dokumente/GitHub/agent-forge/README.md and show the content.",
        task_type=TaskType.READ_AND_DISPLAY,
        wants_read=True,
        target_paths=("GitHub/agent-forge/README.md",),
        read_paths=("GitHub/agent-forge/README.md",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="read_must_not_be_write",
        prompt="Lese die Datei /home/joruf/Dokumente/GitHub/Demo/test.txt",
        task_type=TaskType.READ_AND_DISPLAY,
        wants_read=True,
        target_paths=("GitHub/Demo/test.txt",),
        read_paths=("GitHub/Demo/test.txt",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="write_php_project",
        prompt=(
            "Erstelle index.php mit Header, Menü, Content und Footer und speichere unter "
            f"{WORKSPACE}/Test"
        ),
        task_type=TaskType.WRITE_FILES,
        wants_write=True,
        target_dirs=("GitHub/Test",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="write_named_folder_txt",
        prompt=(
            f"erstelle mir im verzeichnis\n{WORKSPACE}\n"
            "einen Ordner mit dem Namen. Test123\n"
            "darin eine Datei mit dem Namen test.txt\n"
            'in der test.txt schreibst du den Text "Hello World"'
        ),
        task_type=TaskType.WRITE_FILES,
        wants_write=True,
        target_dirs=("GitHub/Test123",),
        planned_files=("GitHub/Test123/test.txt",),
        named_folder="Test123",
        requires_tools=True,
    ),
    PromptIntentCase(
        id="write_test12_folder",
        prompt=(
            f"erstelle einen Ordner mit dem Namen Test12\nim Verzeichnis\n{WORKSPACE}\n"
            "darin eine Datei mit dem Namen test12345.txt\n"
            'in der test.txt schreibst du den Text "Hello World"'
        ),
        task_type=TaskType.WRITE_FILES,
        wants_write=True,
        target_dirs=("GitHub/Test12",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="list_directory_german",
        prompt="Dateien im Ordner /home/joruf/Dokumente/GitHub/agent-forge anzeigen",
        task_type=TaskType.LIST_DIRECTORY,
        wants_list=True,
        target_dirs=("GitHub/agent-forge",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="list_directory_english",
        prompt="List directory /home/joruf/Dokumente/GitHub/RecoverScope",
        task_type=TaskType.LIST_DIRECTORY,
        wants_list=True,
        target_dirs=("GitHub/RecoverScope",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="run_command_german",
        prompt="Führe den Befehl `ls -la GitHub` im Workspace aus",
        task_type=TaskType.RUN_COMMAND,
        wants_command=True,
        requires_tools=True,
    ),
    PromptIntentCase(
        id="run_command_english",
        prompt="Run command ls -la in workspace",
        task_type=TaskType.RUN_COMMAND,
        wants_command=True,
        requires_tools=True,
    ),
    PromptIntentCase(
        id="general_question",
        prompt="Erkläre mir den Unterschied zwischen REST und GraphQL.",
        task_type=TaskType.GENERAL,
    ),
    PromptIntentCase(
        id="write_html_css_js_bundle",
        prompt=(
            "Erstelle eine kleine Website mit index.html, styles.css und app.js "
            f"unter {WORKSPACE}/DemoSite"
        ),
        task_type=TaskType.WRITE_FILES,
        wants_write=True,
        target_dirs=("GitHub/DemoSite",),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="read_multiple_paths_in_prompt",
        prompt=(
            "Lese /home/joruf/Dokumente/GitHub/a.txt und "
            "/home/joruf/Dokumente/GitHub/b.txt"
        ),
        task_type=TaskType.READ_AND_DISPLAY,
        wants_read=True,
        target_paths=("GitHub/a.txt", "GitHub/b.txt"),
        requires_tools=True,
    ),
    PromptIntentCase(
        id="list_beats_generic_create_without_save",
        prompt=f"List directory {WORKSPACE}",
        task_type=TaskType.LIST_DIRECTORY,
        wants_list=True,
        wants_write=False,
        target_dirs=("GitHub",),
        requires_tools=True,
    ),
)


@pytest.mark.parametrize("case", PROMPT_INTENT_CASES, ids=lambda case: case.id)
def test_prompt_intent_classification(case: PromptIntentCase) -> None:
    """Each representative prompt maps to the expected workspace intent flags."""
    intent = detect_workspace_intent(case.prompt)

    assert intent.wants_file_read is case.wants_read
    assert intent.wants_file_creation is case.wants_write
    assert intent.wants_list_directory is case.wants_list
    assert intent.wants_command_execution is case.wants_command
    assert intent.requires_tools is case.requires_tools

    for path in case.target_paths:
        assert path in intent.target_paths, f"Missing target path {path}"

    for directory in case.target_dirs:
        assert directory in intent.target_dirs, f"Missing target dir {directory}"

    assert classify_task_type(intent) == case.task_type

    state = build_task_state(case.prompt, intent)
    assert state.task_type == case.task_type


@pytest.mark.parametrize("case", PROMPT_INTENT_CASES, ids=lambda case: case.id)
def test_prompt_task_plan_matches_task_type(case: PromptIntentCase) -> None:
    """Task board plans use the action that fits the classified task type."""
    intent = detect_workspace_intent(case.prompt)
    state = build_task_state(case.prompt, intent)
    actions = {step.action for step in state.plan_steps}

    if case.task_type == TaskType.READ_AND_DISPLAY:
        assert "read_file" in actions
    elif case.task_type == TaskType.WRITE_FILES:
        assert "write_file" in actions
    elif case.task_type == TaskType.LIST_DIRECTORY:
        assert "list_directory" in actions
    elif case.task_type == TaskType.RUN_COMMAND:
        assert "run_command" in actions


@pytest.mark.parametrize(
    "case",
    [item for item in PROMPT_INTENT_CASES if item.planned_files],
    ids=lambda case: case.id,
)
def test_prompt_planned_deliverables(case: PromptIntentCase) -> None:
    """Write prompts resolve to the expected deliverable file paths."""
    intent = detect_workspace_intent(case.prompt)
    planned = plan_deliverable_files(case.prompt, intent)
    assert planned == list(case.planned_files)


@pytest.mark.parametrize(
    "case",
    [item for item in PROMPT_INTENT_CASES if item.read_paths],
    ids=lambda case: case.id,
)
def test_prompt_read_path_resolution(case: PromptIntentCase) -> None:
    """Read prompts resolve to the expected workspace-relative file paths."""
    intent = detect_workspace_intent(case.prompt)
    paths = resolve_read_file_paths(case.prompt, intent)
    for path in case.read_paths:
        assert path in paths


@pytest.mark.parametrize(
    "case",
    [item for item in PROMPT_INTENT_CASES if item.named_folder],
    ids=lambda case: case.id,
)
def test_prompt_named_folder_extraction(case: PromptIntentCase) -> None:
    """Named-folder write prompts expose the requested folder name."""
    assert extract_named_folder(case.prompt) == case.named_folder
