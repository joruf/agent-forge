"""Tests for workspace file execution helpers."""

from pathlib import Path

import pytest

from agentforge.agents.workspace_executor import (
    build_implementation_prompt,
    fallback_file_content,
    infer_requested_files,
    missing_requested_files,
    plan_deliverable_files,
    strip_code_fences,
    write_file_direct,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.config import settings


def test_infer_requested_files_for_php_prompt() -> None:
    """Explicit filenames and target folders resolve to workspace paths."""
    prompt = (
        "Erstelle index.php mit Header, Menü, Content und Footer und speichere unter "
        "/home/joruf/Dokumente/GitHub/Test"
    )
    intent = detect_workspace_intent(prompt)
    files = infer_requested_files(prompt, intent)
    assert files == ["GitHub/Test/index.php"]


def test_plan_deliverable_files_for_html_without_filename() -> None:
    """HTML file type without explicit filename resolves to index.html."""
    prompt = (
        "Erstelle html Datei mit Header, Menü, Content und Footer und speichere unter "
        "/home/joruf/Dokumente/GitHub/Test2"
    )
    intent = detect_workspace_intent(prompt)
    assert intent.wants_file_creation is True
    assert intent.target_dirs == ["GitHub/Test2"]
    files = plan_deliverable_files(prompt, intent)
    assert files == ["GitHub/Test2/index.html"]


@pytest.mark.asyncio
async def test_missing_requested_files_for_html_without_filename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """HTML requests without explicit filenames are tracked as missing deliverables."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    prompt = (
        "Erstelle html Datei mit Header, Menü, Content und Footer und speichere unter "
        f"{tmp_path}/GitHub/Test2"
    )
    intent = detect_workspace_intent(prompt)
    missing = missing_requested_files(prompt, intent)
    assert missing == ["GitHub/Test2/index.html"]


def test_fallback_file_content_html_scaffold() -> None:
    """HTML fallback content includes header, nav, main, and footer."""
    content = fallback_file_content("GitHub/Test2/index.html", "Create html page")
    assert "<header>" in content
    assert "<nav>" in content
    assert "<main>" in content
    assert "<footer>" in content


def test_build_implementation_prompt_lists_targets() -> None:
    """Implementation prompt names each required file path."""
    prompt = build_implementation_prompt(
        "Create index.php",
        ["GitHub/Test/index.php"],
    )
    assert "write_file" in prompt
    assert "GitHub/Test/index.php" in prompt


def test_strip_code_fences() -> None:
    """Generated content is cleaned before writing."""
    raw = "```php\n<?php echo 'ok';\n```"
    assert strip_code_fences(raw) == "<?php echo 'ok';"


@pytest.mark.asyncio
async def test_write_file_direct_creates_nested_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Direct writes create parent directories inside the workspace."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    success, output = await write_file_direct(
        "GitHub/Test/index.php",
        "<?php echo 'Hello World';",
    )
    target = tmp_path / "GitHub" / "Test" / "index.php"
    assert success is True
    assert target.is_file()
    assert "Hello World" in target.read_text(encoding="utf-8")
    assert "Written:" in output


@pytest.mark.asyncio
async def test_missing_requested_files_detects_absent_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Missing requested files are returned when they do not exist yet."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    prompt = (
        "Erstelle index.php mit Header, Menü, Content und Footer und speichere unter "
        f"{tmp_path}/GitHub/Test"
    )
    intent = detect_workspace_intent(prompt)
    missing = missing_requested_files(prompt, intent)
    assert missing == ["GitHub/Test/index.php"]
