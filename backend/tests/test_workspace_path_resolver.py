"""Unit tests for detecting and correcting wrong workspace paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.agents.workspace_path_resolver import (
    PathResolutionContext,
    activate_path_resolution_context,
    build_path_resolution_context,
    collapsed_target_directory,
    deactivate_path_resolution_context,
    is_obvious_missing_named_folder,
    remap_missing_named_folder,
    remap_to_known_canonical,
    resolve_workspace_path,
)
from agentforge.config import settings
from agentforge.tools.registry import ReadFileTool, WriteFileTool, _resolve_path


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Provide an isolated workspace root for path resolver tests.

    :param tmp_path: Pytest temporary directory
    :param monkeypatch: Pytest monkeypatch fixture
    :return: Workspace root path
    """
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(settings, "workspace_root", root)
    return root


@pytest.fixture
def test12_prompt(workspace_root: Path) -> str:
    """
    Build the Test12 workflow prompt with wrong read/edit paths in user text.

    :param workspace_root: Active workspace root
    :return: User prompt text
    """
    base = workspace_root / "GitHub"
    return (
        f"erstelle mir einen Ordner mit dem Namen Test12\n"
        f"im Verzeichnis\n{base}\n"
        f"darin eine Datei mit dem Namen index.html\n"
        f'darin fügst du in html code den text "Hello World" hinzu.\n'
        f"lese danach die Datei {base}/index.html aus und geb den Inhalt hier im Prompt aus.\n"
        f'bearbeite danach die {base}/index.html und tausche "Hello World" aus gegen "Hello Bot".'
    )


@pytest.fixture
def test12_context(test12_prompt: str) -> PathResolutionContext:
    """
    Build path resolution context for the Test12 workflow prompt.

    :param test12_prompt: User prompt fixture
    :return: Active path resolution context
    """
    intent = detect_workspace_intent(test12_prompt)
    return build_path_resolution_context(test12_prompt, intent)


@pytest.fixture
def active_test12_context(test12_context: PathResolutionContext):
    """
    Activate Test12 path resolution context for the duration of a test.

    :param test12_context: Path resolution context fixture
    :yield: Active context instance
    """
    token = activate_path_resolution_context(test12_context)
    try:
        yield test12_context
    finally:
        deactivate_path_resolution_context(token)


@pytest.fixture
def test12_file(workspace_root: Path) -> Path:
    """
    Create the canonical Test12 deliverable file on disk.

    :param workspace_root: Active workspace root
    :return: Absolute path to GitHub/Test12/index.html
    """
    target = workspace_root / "GitHub" / "Test12" / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text("<h1>Hello World</h1>", encoding="utf-8")
    return target


WRONG_PATH_CASES: tuple[tuple[str, str], ...] = (
    ("GitHub/index.html", "GitHub/Test12/index.html"),
    ("index.html", "GitHub/Test12/index.html"),
)

UNCHANGED_PATH_CASES: tuple[str, ...] = (
    "GitHub/Test12/index.html",
    "GitHub/other-app/readme.md",
    "docs/manual.txt",
)


@pytest.mark.parametrize(("wrong_path", "expected_path"), WRONG_PATH_CASES)
def test_resolve_workspace_path_corrects_obvious_wrong_paths(
    wrong_path: str,
    expected_path: str,
    active_test12_context: PathResolutionContext,
    test12_file: Path,
) -> None:
    """Known wrong paths are remapped to the canonical deliverable path."""
    assert resolve_workspace_path(wrong_path) == expected_path


@pytest.mark.parametrize("correct_path", UNCHANGED_PATH_CASES)
def test_resolve_workspace_path_leaves_unrelated_paths_unchanged(
    correct_path: str,
    active_test12_context: PathResolutionContext,
    test12_file: Path,
) -> None:
    """Correct or unrelated paths must not be rewritten."""
    assert resolve_workspace_path(correct_path) == correct_path


def test_resolve_workspace_path_without_context_returns_normalized_path(
    workspace_root: Path,
) -> None:
    """Without orchestration context only normalization is applied."""
    absolute = str(workspace_root / "GitHub" / "index.html")
    assert resolve_workspace_path(absolute) == "GitHub/index.html"


def test_resolve_workspace_path_absolute_wrong_path_is_corrected(
    workspace_root: Path,
    test12_prompt: str,
    test12_file: Path,
) -> None:
    """Absolute wrong paths under the workspace root are corrected with context."""
    wrong_absolute = str(workspace_root / "GitHub" / "index.html")
    context = build_path_resolution_context(
        test12_prompt,
        detect_workspace_intent(test12_prompt),
    )
    token = activate_path_resolution_context(context)
    try:
        assert resolve_workspace_path(wrong_absolute) == "GitHub/Test12/index.html"
        assert _resolve_path(wrong_absolute) == test12_file.resolve()
    finally:
        deactivate_path_resolution_context(token)


def test_build_path_resolution_context_collects_canonical_paths(
    test12_prompt: str,
) -> None:
    """Canonical deliverable and target paths are available to the resolver."""
    intent = detect_workspace_intent(test12_prompt)
    context = build_path_resolution_context(test12_prompt, intent)

    assert "GitHub/Test12/index.html" in context.canonical_paths
    assert "GitHub/Test12" in context.canonical_paths


@pytest.mark.parametrize(
    ("wrong_path", "named_folder", "target_dirs", "expected"),
    [
        ("GitHub/index.html", "Test12", ["GitHub/Test12"], "GitHub/Test12/index.html"),
        ("index.html", "Test12", ["GitHub/Test12"], "GitHub/Test12/index.html"),
        ("GitHub/test.txt", "Test123", ["GitHub/Test123"], "GitHub/Test123/test.txt"),
        ("GitHub/Test12/index.html", "Test12", ["GitHub/Test12"], None),
        ("GitHub/other/index.html", "Test12", ["GitHub/Test12"], None),
    ],
)
def test_remap_missing_named_folder_cases(
    wrong_path: str,
    named_folder: str,
    target_dirs: list[str],
    expected: str | None,
) -> None:
    """Missing named-folder segments are inserted only for obvious wrong paths."""
    assert remap_missing_named_folder(wrong_path, named_folder, target_dirs) == expected


@pytest.mark.parametrize(
    ("wrong_path", "named_folder", "target_dirs", "expected"),
    [
        ("GitHub/index.html", "Test12", ["GitHub/Test12"], True),
        ("index.html", "Test12", ["GitHub/Test12"], True),
        ("GitHub/Test12/index.html", "Test12", ["GitHub/Test12"], False),
        ("GitHub/other/index.html", "Test12", ["GitHub/Test12"], False),
    ],
)
def test_is_obvious_missing_named_folder_cases(
    wrong_path: str,
    named_folder: str,
    target_dirs: list[str],
    expected: bool,
) -> None:
    """Obvious directory errors are detected independently from remapping."""
    assert (
        is_obvious_missing_named_folder(wrong_path, named_folder, target_dirs)
        is expected
    )


@pytest.mark.parametrize(
    ("wrong_path", "canonical_paths", "expected"),
    [
        ("GitHub/index.html", ["GitHub/Test12/index.html"], "GitHub/Test12/index.html"),
        ("index.html", ["GitHub/Test12/index.html"], "GitHub/Test12/index.html"),
        (
            "GitHub/test.txt",
            ["GitHub/Test123/test.txt"],
            "GitHub/Test123/test.txt",
        ),
        ("GitHub/Test12/index.html", ["GitHub/Test12/index.html"], None),
        ("GitHub/other.html", ["GitHub/Test12/index.html"], None),
        ("GitHub/index.html", ["GitHub/Test12/output.txt"], None),
    ],
)
def test_remap_to_known_canonical_cases(
    wrong_path: str,
    canonical_paths: list[str],
    expected: str | None,
) -> None:
    """Canonical remapping requires the same basename and ordered path overlap."""
    assert remap_to_known_canonical(wrong_path, canonical_paths) == expected


def test_collapsed_target_directory_removes_named_folder() -> None:
    """Named folder segments are removed from the target directory."""
    assert collapsed_target_directory("GitHub/Test12", "Test12") == "GitHub"
    assert collapsed_target_directory("GitHub/Test123", "Test123") == "GitHub"
    assert collapsed_target_directory("Test12", "Test12") is None


@pytest.mark.asyncio
async def test_read_file_tool_corrects_wrong_path(
    workspace_root: Path,
    test12_prompt: str,
    test12_file: Path,
) -> None:
    """read_file succeeds when agents pass the wrong user-mentioned path."""
    test12_file.write_text("Hello Bot", encoding="utf-8")
    context = build_path_resolution_context(
        test12_prompt,
        detect_workspace_intent(test12_prompt),
    )
    token = activate_path_resolution_context(context)
    try:
        result = await ReadFileTool().execute(
            {"path": str(workspace_root / "GitHub" / "index.html")},
        )
        assert result.success is True
        assert "Hello Bot" in result.output
    finally:
        deactivate_path_resolution_context(token)


@pytest.mark.asyncio
async def test_write_file_tool_corrects_wrong_path(
    workspace_root: Path,
    test12_prompt: str,
    test12_file: Path,
) -> None:
    """write_file updates the canonical file even when given a shortened path."""
    context = build_path_resolution_context(
        test12_prompt,
        detect_workspace_intent(test12_prompt),
    )
    token = activate_path_resolution_context(context)
    try:
        result = await WriteFileTool().execute(
            {
                "path": str(workspace_root / "GitHub" / "index.html"),
                "content": "<h1>Hello Bot</h1>",
            },
        )
        assert result.success is True
        assert test12_file.read_text(encoding="utf-8") == "<h1>Hello Bot</h1>"
        assert not (workspace_root / "GitHub" / "index.html").exists()
    finally:
        deactivate_path_resolution_context(token)


@pytest.mark.asyncio
async def test_read_file_without_context_does_not_invent_nested_path(
    workspace_root: Path,
    test12_file: Path,
) -> None:
    """Without context a wrong path fails instead of silently guessing a nested target."""
    result = await ReadFileTool().execute({"path": "GitHub/index.html"})
    assert result.success is False
    assert "File not found" in result.output


def test_intent_maps_user_read_path_to_canonical_target(test12_prompt: str) -> None:
    """Intent parsing already stores the corrected deliverable path from user text."""
    intent = detect_workspace_intent(test12_prompt)

    assert intent.target_paths == ["GitHub/Test12/index.html"]
    assert intent.target_dirs == ["GitHub/Test12"]
