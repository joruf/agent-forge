"""Tests for workspace path scanning."""

from pathlib import Path

import pytest

from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.agents.workspace_scanner import build_workspace_path_context
from agentforge.config import settings


@pytest.mark.asyncio
async def test_build_workspace_path_context_for_missing_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Missing directories are reported explicitly to agents."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    prompt = f"Erstelle index.php und speichere unter {tmp_path}/GitHub/NewProject"
    intent = detect_workspace_intent(prompt)
    context = await build_workspace_path_context(intent)
    assert "GitHub/NewProject" in context
    assert "does not exist yet" in context


@pytest.mark.asyncio
async def test_build_workspace_path_context_lists_existing_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Existing project files and README content are included in context."""
    project = tmp_path / "GitHub" / "Existing"
    project.mkdir(parents=True)
    (project / "README.md").write_text("# Existing Project\n", encoding="utf-8")
    (project / "index.php").write_text("<?php echo 'hello';", encoding="utf-8")
    (project / "styles.css").write_text("body {}", encoding="utf-8")

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    prompt = f"Erweitere das Projekt unter {tmp_path}/GitHub/Existing"
    intent = detect_workspace_intent(prompt)
    context = await build_workspace_path_context(intent)
    assert "GitHub/Existing" in context
    assert "[FILE] README.md" in context
    assert "[FILE] index.php" in context
    assert "# Existing Project" in context
    assert "<?php echo 'hello';" in context
