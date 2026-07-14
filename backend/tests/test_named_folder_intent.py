"""Tests for named-folder workspace intent and deliverable planning."""

from pathlib import Path

import pytest

from agentforge.agents.workspace_executor import (
    build_deliverable_status_summary,
    fallback_file_content,
    missing_requested_files,
    plan_deliverable_files,
)
from agentforge.agents.workspace_intent import detect_workspace_intent, extract_named_folder
from agentforge.config import settings


USER_PROMPT = (
    "erstelle mir im verzeichnis\n"
    "/home/joruf/Dokumente/GitHub\n"
    "einen Ordner mit dem Namen. Test123\n"
    "darin eine Datei mit dem Namen test.txt\n"
    'in der test.txt schreibst du den Text "Hello World"'
)


def test_extract_named_folder_from_german_prompt() -> None:
    """Folder names are extracted from German create-directory requests."""
    assert extract_named_folder(USER_PROMPT) == "Test123"


def test_detect_workspace_intent_includes_named_subfolder() -> None:
    """Target directories include the requested subfolder name."""
    intent = detect_workspace_intent(USER_PROMPT)
    assert intent.wants_file_creation is True
    assert "GitHub/Test123" in intent.target_dirs


def test_plan_deliverable_files_for_named_subfolder() -> None:
    """Explicit filenames resolve under the requested subfolder."""
    intent = detect_workspace_intent(USER_PROMPT)
    assert plan_deliverable_files(USER_PROMPT, intent) == ["GitHub/Test123/test.txt"]


def test_fallback_txt_uses_quoted_literal() -> None:
    """TXT fallback content uses quoted text from the user request."""
    content = fallback_file_content("GitHub/Test123/test.txt", USER_PROMPT)
    assert content.strip() == "Hello World"


@pytest.mark.asyncio
async def test_missing_requested_files_for_named_subfolder(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Missing deliverables are detected when only the wrong path exists."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    (tmp_path / "GitHub").mkdir()
    (tmp_path / "GitHub" / "test.txt").write_text("old content", encoding="utf-8")
    prompt = (
        f"erstelle mir im verzeichnis\n"
        f"{tmp_path}/GitHub\n"
        "einen Ordner mit dem Namen. Test123\n"
        "darin eine Datei mit dem Namen test.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )

    intent = detect_workspace_intent(prompt)
    missing = missing_requested_files(prompt, intent)
    assert missing == ["GitHub/Test123/test.txt"]


def test_build_deliverable_status_summary_reports_missing(monkeypatch, tmp_path: Path) -> None:
    """Status summary reports missing deliverables honestly."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    prompt = (
        f"erstelle mir im verzeichnis\n"
        f"{tmp_path}/GitHub\n"
        "einen Ordner mit dem Namen. Test123\n"
        "darin eine Datei mit dem Namen test.txt\n"
        'in der test.txt schreibst du den Text "Hello World"'
    )
    intent = detect_workspace_intent(prompt)
    summary = build_deliverable_status_summary(prompt, intent)
    assert "Missing: GitHub/Test123/test.txt" in summary
