"""Tests for workspace file search and position-aware editing."""

import pytest

from agentforge.tools.file_search import (
    FileAnchorStore,
    apply_file_edit,
    format_search_results,
    search_workspace_files,
)
from agentforge.tools.registry import EditFileTool, ReadFileTool, SearchFilesTool, WriteFileTool


@pytest.mark.asyncio
async def test_search_files_finds_exact_positions(temp_workspace) -> None:
    """search_files returns line, column, and byte positions."""
    target = temp_workspace / "sample.py"
    target.write_text("alpha = 1\nbeta = alpha + 2\n", encoding="utf-8")

    anchor_store = FileAnchorStore()
    tool = SearchFilesTool(anchor_store)
    result = await tool.execute({
        "query": "alpha",
        "path": "sample.py",
    })

    assert result.success is True
    assert "m1" in result.output
    assert "sample.py:1:" in result.output
    assert "bytes" in result.output
    anchor = anchor_store.get("m1")
    assert anchor is not None
    assert anchor["matched_text"] == "alpha"


@pytest.mark.asyncio
async def test_edit_file_with_match_id(temp_workspace) -> None:
    """edit_file replaces text at a remembered search position."""
    target = temp_workspace / "sample.js"
    target.write_text("const value = 'old';\n", encoding="utf-8")

    anchor_store = FileAnchorStore()
    search_tool = SearchFilesTool(anchor_store)
    search_result = await search_tool.execute({
        "query": "'old'",
        "path": "sample.js",
    })
    assert search_result.success is True

    edit_tool = EditFileTool(anchor_store)
    edit_result = await edit_tool.execute({
        "match_id": "m1",
        "new_text": "'new'",
    })
    assert edit_result.success is True
    assert target.read_text(encoding="utf-8") == "const value = 'new';\n"


@pytest.mark.asyncio
async def test_edit_file_with_old_string(temp_workspace) -> None:
    """edit_file can replace unique old_string values."""
    target = temp_workspace / "app.txt"
    target.write_text("one two three\n", encoding="utf-8")

    edit_tool = EditFileTool()
    result = await edit_tool.execute({
        "path": "app.txt",
        "old_string": "two",
        "new_text": "TOO",
    })

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "one TOO three\n"


@pytest.mark.asyncio
async def test_edit_file_rejects_ambiguous_old_string(temp_workspace) -> None:
    """edit_file requires replace_all when old_string matches multiple times."""
    target = temp_workspace / "dup.txt"
    target.write_text("foo bar foo\n", encoding="utf-8")

    edit_tool = EditFileTool()
    result = await edit_tool.execute({
        "path": "dup.txt",
        "old_string": "foo",
        "new_text": "baz",
    })

    assert result.success is False
    assert "multiple" in result.output.lower()


@pytest.mark.asyncio
async def test_read_file_supports_line_range(temp_workspace) -> None:
    """read_file can return numbered partial content."""
    write_tool = WriteFileTool()
    await write_tool.execute({
        "path": "lines.txt",
        "content": "first\nsecond\nthird\n",
    })

    read_tool = ReadFileTool()
    result = await read_tool.execute({
        "path": "lines.txt",
        "start_line": 2,
        "end_line": 2,
    })

    assert result.success is True
    assert "2 | second" in result.output


def test_search_workspace_respects_glob(temp_workspace) -> None:
    """Glob filters limit searched files."""
    (temp_workspace / "keep.py").write_text("needle\n", encoding="utf-8")
    (temp_workspace / "skip.txt").write_text("needle\n", encoding="utf-8")

    matches = search_workspace_files(
        "needle",
        relative_path=".",
        glob_pattern="*.py",
        workspace_root=temp_workspace,
    )

    assert len(matches) == 1
    assert matches[0].path == "keep.py"


def test_apply_file_edit_by_line_range(temp_workspace) -> None:
    """Manual line/column ranges can be edited directly."""
    target = temp_workspace / "range.php"
    target.write_text("<?php echo 'hi'; ?>\n", encoding="utf-8")

    message, count = apply_file_edit(
        target,
        new_text="Hello World",
        start_line=1,
        start_column=15,
        end_line=1,
        end_column=17,
    )

    assert count == 1
    assert "Hello World" in target.read_text(encoding="utf-8")
    assert "Updated" in message


def test_format_search_results_registers_anchors() -> None:
    """Search formatting stores anchors for later edits."""
    from agentforge.tools.file_search import FileMatch

    store = FileAnchorStore()
    matches = [
        FileMatch(
            path="a.txt",
            line=1,
            column=1,
            end_line=1,
            end_column=4,
            byte_start=0,
            byte_end=3,
            matched_text="abc",
            line_content="abc",
        )
    ]
    output = format_search_results(matches, store)
    assert "m1" in output
    assert store.get("m1") is not None
