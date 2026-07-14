"""Regression tests for prompt path and folder-name extraction."""

from pathlib import Path

import pytest

from agentforge.agents.workspace_executor import plan_deliverable_files
from agentforge.agents.workspace_intent import detect_workspace_intent, extract_named_folder
from agentforge.config import settings


def test_extract_named_folder_ignores_pytest_temp_path_segments() -> None:
    """Folder extraction must not capture pytest temp directory segments from paths."""
    prompt = (
        "erstelle mir im verzeichnis\n"
        "/tmp/pytest-of-user/pytest-1/test_write_named_folder_prompt0/workspace/GitHub\n"
        "einen Ordner mit dem Namen. Test123\n"
        "darin eine Datei mit dem Namen test.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )

    assert extract_named_folder(prompt) == "Test123"


@pytest.mark.asyncio
async def test_named_folder_prompt_with_temp_path_plans_correct_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Write planning stays stable when prompts include pytest temp directories."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspace")
    (tmp_path / "workspace" / "GitHub").mkdir(parents=True)

    workspace = tmp_path / "workspace"
    prompt = (
        f"erstelle mir im verzeichnis\n{workspace}/GitHub\n"
        "einen Ordner mit dem Namen. Test123\n"
        "darin eine Datei mit dem Namen test.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )
    intent = detect_workspace_intent(prompt)

    assert plan_deliverable_files(prompt, intent) == ["GitHub/Test123/test.txt"]
