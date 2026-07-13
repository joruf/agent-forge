"""Workspace file search and position-aware editing."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agentforge.config import settings

SKIP_DIR_NAMES = frozenset({
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
})


@dataclass
class FileMatch:
    """One text match inside a workspace file."""

    path: str
    line: int
    column: int
    end_line: int
    end_column: int
    byte_start: int
    byte_end: int
    matched_text: str
    line_content: str


class FileAnchorStore:
    """Stores search match anchors for later position-aware edits."""

    def __init__(self) -> None:
        """Initialize an empty anchor store."""
        self._anchors: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def register(self, match: FileMatch) -> str:
        """
        Register a match and return a stable match identifier.

        :param match: Located file match
        :return: Match identifier (for example ``m1``)
        """
        self._counter += 1
        match_id = f"m{self._counter}"
        self._anchors[match_id] = asdict(match)
        return match_id

    def get(self, match_id: str) -> dict[str, Any] | None:
        """
        Return a stored match anchor.

        :param match_id: Match identifier from search_files
        :return: Anchor payload or None
        """
        return self._anchors.get(match_id)

    def remove(self, match_id: str) -> None:
        """
        Remove a stored match anchor.

        :param match_id: Match identifier
        """
        self._anchors.pop(match_id, None)


def _is_probably_binary(path: Path) -> bool:
    """
    Heuristically detect binary files.

    :param path: File path
    :return: True when the file should be skipped
    """
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return True
    return b"\x00" in sample


def _line_byte_offsets(text: str) -> list[int]:
    """
    Build byte offsets for each line start.

    :param text: Full file text
    :return: Byte offset for each line (0-based line index)
    """
    offsets = [0]
    index = 0
    while index < len(text):
        next_newline = text.find("\n", index)
        if next_newline == -1:
            break
        offsets.append(next_newline + 1)
        index = next_newline + 1
    return offsets


def _split_lines(text: str) -> list[str]:
    """
    Split file text into lines without trailing newline characters.

    :param text: Full file text
    :return: Line strings
    """
    if not text:
        return [""]
    lines = text.splitlines()
    if text.endswith("\n") and lines:
        return lines
    return lines or [""]


def _iter_search_files(
    root: Path,
    search_root: Path,
    glob_pattern: str | None,
) -> list[Path]:
    """
    Collect searchable files under a workspace path.

    :param root: Workspace root
    :param search_root: Resolved search directory or file
    :param glob_pattern: Optional glob filter
    :return: Candidate file paths
    """
    if search_root.is_file():
        return [search_root]

    files: list[Path] = []
    for path in search_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if glob_pattern and not fnmatch.fnmatch(relative, glob_pattern):
            continue
        files.append(path)
    return sorted(files)


def search_workspace_files(
    query: str,
    *,
    relative_path: str = ".",
    glob_pattern: str | None = None,
    case_insensitive: bool = False,
    use_regex: bool = False,
    max_results: int | None = None,
    context_lines: int = 0,
    workspace_root: Path | None = None,
) -> list[FileMatch]:
    """
    Search workspace files for a string or regex pattern.

    :param query: Text or regex pattern to find
    :param relative_path: Workspace-relative file or directory path
    :param glob_pattern: Optional glob filter (for example ``*.py``)
    :param case_insensitive: Match without case sensitivity
    :param use_regex: Treat query as regular expression
    :param max_results: Maximum number of matches to return
    :param context_lines: Reserved for future context expansion
    :param workspace_root: Workspace root override
    :return: Structured match list with exact positions
    """
    _ = context_lines
    root = (workspace_root or settings.workspace_root).resolve()
    search_root = (root / relative_path.lstrip("/")).resolve()
    if not str(search_root).startswith(str(root)):
        raise PermissionError("Path escapes workspace root")
    if not search_root.exists():
        raise FileNotFoundError(f"Path not found: {relative_path}")

    limit = max_results if max_results is not None else settings.max_search_results
    limit = max(1, min(limit, settings.max_search_results))
    max_file_size = settings.max_search_file_bytes

    if use_regex:
        flags = re.MULTILINE
        if case_insensitive:
            flags |= re.IGNORECASE
        pattern = re.compile(query, flags)
    else:
        escaped = re.escape(query)
        flags = re.MULTILINE
        if case_insensitive:
            flags |= re.IGNORECASE
        pattern = re.compile(escaped, flags)

    matches: list[FileMatch] = []
    for file_path in _iter_search_files(root, search_root, glob_pattern):
        if len(matches) >= limit:
            break
        try:
            if file_path.stat().st_size > max_file_size:
                continue
        except OSError:
            continue
        if _is_probably_binary(file_path):
            continue

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        relative = file_path.relative_to(root).as_posix()
        lines = _split_lines(text)
        line_offsets = _line_byte_offsets(text)

        for found in pattern.finditer(text):
            if len(matches) >= limit:
                break
            start = found.start()
            end = found.end()
            matched_text = found.group(0)

            line_index = 0
            for index, offset in enumerate(line_offsets):
                next_offset = (
                    line_offsets[index + 1]
                    if index + 1 < len(line_offsets)
                    else len(text)
                )
                if offset <= start < next_offset:
                    line_index = index
                    break
                if start >= next_offset:
                    line_index = index

            line_start = line_offsets[line_index]
            column = start - line_start + 1
            end_line_index = line_index
            for index, offset in enumerate(line_offsets):
                next_offset = (
                    line_offsets[index + 1]
                    if index + 1 < len(line_offsets)
                    else len(text)
                )
                if offset <= max(end - 1, start) < next_offset:
                    end_line_index = index
                    break
                if max(end - 1, start) >= next_offset:
                    end_line_index = index

            end_line_start = line_offsets[end_line_index]
            end_column = max(end - end_line_start, 1)

            matches.append(
                FileMatch(
                    path=relative,
                    line=line_index + 1,
                    column=column,
                    end_line=end_line_index + 1,
                    end_column=end_column,
                    byte_start=start,
                    byte_end=end,
                    matched_text=matched_text,
                    line_content=lines[line_index] if line_index < len(lines) else "",
                )
            )

    return matches


def format_search_results(matches: list[FileMatch], anchor_store: FileAnchorStore) -> str:
    """
    Format search matches for tool output and register anchors.

    :param matches: Located matches
    :param anchor_store: Anchor store for later edits
    :return: Human-readable multiline output
    """
    if not matches:
        return "No matches found."

    lines: list[str] = []
    for match in matches:
        match_id = anchor_store.register(match)
        lines.append(
            f"{match_id} | {match.path}:{match.line}:{match.column}"
            f"-{match.end_line}:{match.end_column}"
            f" | bytes {match.byte_start}-{match.byte_end}"
            f" | {match.matched_text!r}"
        )
        if match.line_content:
            lines.append(f"    {match.line_content}")
    lines.append(
        "\nUse edit_file with match_id to replace text at an exact position."
    )
    return "\n".join(lines)


def format_numbered_lines(text: str, start_line: int = 1) -> str:
    """
    Prefix each line with a line number.

    :param text: Text block
    :param start_line: First line number
    :return: Numbered text block
    """
    lines = _split_lines(text)
    width = len(str(start_line + len(lines) - 1))
    numbered = []
    for index, line in enumerate(lines):
        line_no = start_line + index
        numbered.append(f"{line_no:>{width}} | {line}")
    return "\n".join(numbered)


def _resolve_edit_span(
    text: str,
    *,
    anchor: dict[str, Any] | None,
    old_string: str | None,
    start_line: int | None,
    start_column: int | None,
    end_line: int | None,
    end_column: int | None,
    replace_all: bool,
) -> list[tuple[int, int]]:
    """
    Resolve one or more byte spans to replace.

    :param text: Current file contents
    :param anchor: Stored match anchor
    :param old_string: Literal text to replace
    :param start_line: 1-based start line
    :param start_column: 1-based start column
    :param end_line: 1-based end line
    :param end_column: 1-based end column
    :param replace_all: Replace every old_string occurrence
    :return: List of (byte_start, byte_end) spans
    """
    if anchor is not None:
        start = int(anchor["byte_start"])
        end = int(anchor["byte_end"])
        expected = str(anchor["matched_text"])
        actual = text[start:end]
        if actual == expected:
            return [(start, end)]
        fallback = text.find(expected)
        if fallback == -1:
            raise ValueError(
                "Stored match_id no longer matches file content. Run search_files again."
            )
        return [(fallback, fallback + len(expected))]

    if old_string is not None:
        if not old_string:
            raise ValueError("old_string must not be empty.")
        occurrences: list[tuple[int, int]] = []
        search_from = 0
        while True:
            index = text.find(old_string, search_from)
            if index == -1:
                break
            occurrences.append((index, index + len(old_string)))
            search_from = index + len(old_string)
        if not occurrences:
            raise ValueError("old_string was not found in the file.")
        if not replace_all and len(occurrences) > 1:
            raise ValueError(
                "old_string matches multiple locations. "
                "Use search_files for match_id or set replace_all=true."
            )
        if replace_all:
            return occurrences
        return [occurrences[0]]

    if start_line is None:
        raise ValueError(
            "Provide match_id, old_string, or start_line/start_column/end_line/end_column."
        )

    lines = _split_lines(text)
    line_offsets = _line_byte_offsets(text)
    start_index = start_line - 1
    end_index = (end_line or start_line) - 1
    if start_index < 0 or end_index >= len(lines):
        raise ValueError("Line range is out of bounds.")

    start_offset = line_offsets[start_index]
    end_offset = (
        line_offsets[end_index + 1]
        if end_index + 1 < len(line_offsets)
        else len(text)
    )
    start_col = max(1, start_column or 1)
    end_col = end_column if end_column is not None else len(lines[end_index]) + 1
    span_start = min(start_offset + start_col - 1, len(text))
    span_end = min(start_offset + end_col - 1, end_offset, len(text))
    if span_end < span_start:
        span_end = span_start
    return [(span_start, span_end)]


def apply_file_edit(
    path: Path,
    *,
    new_text: str,
    anchor_store: FileAnchorStore | None = None,
    match_id: str | None = None,
    old_string: str | None = None,
    start_line: int | None = None,
    start_column: int | None = None,
    end_line: int | None = None,
    end_column: int | None = None,
    replace_all: bool = False,
) -> tuple[str, int]:
    """
    Apply a position-aware edit to a workspace file.

    :param path: Resolved file path
    :param new_text: Replacement or inserted text
    :param anchor_store: Anchor store used by search_files
    :param match_id: Stored match identifier
    :param old_string: Literal text to replace
    :param start_line: 1-based start line
    :param start_column: 1-based start column
    :param end_line: 1-based end line
    :param end_column: 1-based end column
    :param replace_all: Replace every old_string occurrence
    :return: Tuple of success message and number of replacements
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    anchor = anchor_store.get(match_id) if anchor_store and match_id else None
    if match_id and anchor is None:
        raise ValueError(f"Unknown match_id: {match_id}")

    spans = _resolve_edit_span(
        text,
        anchor=anchor,
        old_string=old_string,
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
        replace_all=replace_all,
    )

    updated = text
    delta = 0
    replacements = 0
    for start, end in sorted(spans, key=lambda item: item[0]):
        adjusted_start = start + delta
        adjusted_end = end + delta
        updated = updated[:adjusted_start] + new_text + updated[adjusted_end:]
        delta += len(new_text) - (end - start)
        replacements += 1

    path.write_text(updated, encoding="utf-8")
    if anchor_store and match_id:
        anchor_store.remove(match_id)
    return f"Updated {path} ({replacements} replacement(s)).", replacements
