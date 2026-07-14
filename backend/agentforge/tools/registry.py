"""Tool definitions for agent actions."""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agentforge.config import settings
from agentforge.i18n import t
from agentforge.models.schemas import ToolCallResult
from agentforge.tools.file_search import (
    FileAnchorStore,
    apply_file_edit,
    format_numbered_lines,
    format_search_results,
    search_workspace_files,
)


class BaseTool(ABC):
    """Abstract base for agent tools."""

    name: str
    description: str

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Return OpenAI function tool schema."""

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Execute the tool with parsed arguments."""


def normalize_workspace_relative_path(path_str: str) -> str:
    """
    Normalize an absolute or relative path to a workspace-relative string.

    :param path_str: Absolute or workspace-relative path
    :return: Path relative to the workspace root
    :raises PermissionError: When the path escapes the workspace root
    """
    raw = path_str.strip().strip("'\"")
    if not raw:
        raise PermissionError("Empty path")

    root = settings.workspace_root.resolve()
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not str(resolved).startswith(str(root)):
            raise PermissionError("Path escapes workspace root")
        return str(resolved.relative_to(root))

    target = (root / raw.lstrip("/")).resolve()
    if not str(target).startswith(str(root)):
        raise PermissionError("Path escapes workspace root")
    return str(target.relative_to(root))


def _resolve_path(relative_path: str) -> Path:
    """Resolve path within workspace root."""
    from agentforge.agents.workspace_path_resolver import resolve_workspace_path

    root = settings.workspace_root.resolve()
    normalized = resolve_workspace_path(relative_path)
    return (root / normalized).resolve()


def _parents_to_create(relative_path: str) -> list[str]:
    """
    Return workspace-relative directories that do not exist yet for a file path.

    :param relative_path: Target workspace-relative file path
    :return: Ordered directory paths to create
    """
    root = settings.workspace_root.resolve()
    target = _resolve_path(relative_path)
    missing: list[str] = []
    current = target.parent
    while str(current).startswith(str(root)) and current != root:
        if not current.exists():
            missing.append(str(current.relative_to(root)))
        current = current.parent
    missing.reverse()
    return missing


class ReadFileTool(BaseTool):
    """Read file contents from workspace."""

    name = "read_file"
    description = (
        "Read a text file from the workspace. "
        "Use start_line/end_line for partial reads with line numbers."
    )

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path"},
                        "start_line": {
                            "type": "integer",
                            "description": "First line to read (1-based, optional)",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Last line to read (1-based, inclusive, optional)",
                        },
                        "numbered": {
                            "type": "boolean",
                            "description": "Prefix lines with line numbers",
                            "default": True,
                        },
                    },
                    "required": ["path"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Read file and return contents."""
        from agentforge.services.command_audit import record_read_file

        relative_path = str(arguments["path"])
        try:
            path = _resolve_path(relative_path)
            if not path.exists():
                output = f"File not found: {path}"
                await record_read_file(relative_path, output=output, success=False)
                return ToolCallResult(tool=self.name, success=False, output=output)
            content = path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            start_line = arguments.get("start_line")
            end_line = arguments.get("end_line")
            numbered = arguments.get("numbered", True)

            if start_line is not None or end_line is not None:
                start_index = max(1, int(start_line or 1)) - 1
                end_index = int(end_line or len(lines)) - 1
                if start_index < 0 or end_index >= len(lines):
                    output = "Line range is out of bounds."
                    await record_read_file(relative_path, output=output, success=False)
                    return ToolCallResult(
                        tool=self.name,
                        success=False,
                        output=output,
                    )
                selected = "\n".join(lines[start_index:end_index + 1])
                if numbered:
                    content = format_numbered_lines(selected, start_line=start_index + 1)
                else:
                    content = selected
            elif numbered:
                content = format_numbered_lines(content, start_line=1)

            if len(content) > settings.max_output_chars:
                content = content[: settings.max_output_chars] + "\n... [truncated]"
            await record_read_file(relative_path, output=content, success=True)
            return ToolCallResult(tool=self.name, success=True, output=content)
        except Exception as exc:
            await record_read_file(relative_path, output=str(exc), success=False)
            return ToolCallResult(tool=self.name, success=False, output=str(exc))


class WriteFileTool(BaseTool):
    """Write or create a file in workspace."""

    name = "write_file"
    description = (
        "Create or overwrite a text file in the workspace. "
        "Use this whenever the user asks to save, create, or write files. "
        "Parent directories are created automatically."
    )

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path inside the workspace",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full file contents to write",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Write file contents."""
        from agentforge.services.command_audit import record_write_file

        relative_path = str(arguments["path"])
        try:
            created_dirs = _parents_to_create(relative_path)
            path = _resolve_path(relative_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments["content"], encoding="utf-8")
            output = f"Written: {path}"
            result = ToolCallResult(tool=self.name, success=True, output=output)
            await record_write_file(
                relative_path,
                output=output,
                success=True,
                created_dirs=created_dirs,
            )
            return result
        except Exception as exc:
            output = str(exc)
            await record_write_file(
                relative_path,
                output=output,
                success=False,
                created_dirs=_parents_to_create(relative_path),
            )
            return ToolCallResult(tool=self.name, success=False, output=output)


class SearchFilesTool(BaseTool):
    """Search workspace files and remember exact match positions."""

    name = "search_files"
    description = (
        "Search files in the workspace for a text or regex pattern. "
        "Returns match_id values with line, column, and byte positions "
        "for later edits via edit_file."
    )

    def __init__(self, anchor_store: FileAnchorStore | None = None) -> None:
        """Initialize with a per-chat anchor store."""
        self.anchor_store = anchor_store or FileAnchorStore()

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text or regex pattern to search for",
                        },
                        "path": {
                            "type": "string",
                            "description": "Workspace-relative file or directory",
                            "default": ".",
                        },
                        "glob": {
                            "type": "string",
                            "description": "Optional glob filter, for example *.py",
                        },
                        "case_insensitive": {
                            "type": "boolean",
                            "description": "Ignore letter case",
                            "default": False,
                        },
                        "regex": {
                            "type": "boolean",
                            "description": "Treat query as regular expression",
                            "default": False,
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of matches to return",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Search files and register match anchors."""
        try:
            query = str(arguments.get("query", "")).strip()
            if not query:
                return ToolCallResult(tool=self.name, success=False, output="query is required.")

            matches = search_workspace_files(
                query,
                relative_path=str(arguments.get("path") or "."),
                glob_pattern=arguments.get("glob"),
                case_insensitive=bool(arguments.get("case_insensitive", False)),
                use_regex=bool(arguments.get("regex", False)),
                max_results=arguments.get("max_results"),
            )
            output = format_search_results(matches, self.anchor_store)
            if len(output) > settings.max_output_chars:
                output = output[: settings.max_output_chars] + "\n... [truncated]"
            return ToolCallResult(tool=self.name, success=True, output=output)
        except Exception as exc:
            return ToolCallResult(tool=self.name, success=False, output=str(exc))


class EditFileTool(BaseTool):
    """Edit workspace files at remembered or explicit positions."""

    name = "edit_file"
    description = (
        "Edit a file at an exact position. Use match_id from search_files, "
        "or old_string/new_text, or start_line/start_column/end_line/end_column."
    )

    def __init__(self, anchor_store: FileAnchorStore | None = None) -> None:
        """Initialize with a per-chat anchor store."""
        self.anchor_store = anchor_store or FileAnchorStore()

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path (required unless match_id is used)",
                        },
                        "match_id": {
                            "type": "string",
                            "description": "Match identifier returned by search_files",
                        },
                        "old_string": {
                            "type": "string",
                            "description": "Exact text to replace (must be unique unless replace_all)",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "Replacement text",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "1-based start line for manual range edit",
                        },
                        "start_column": {
                            "type": "integer",
                            "description": "1-based start column for manual range edit",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "1-based end line for manual range edit",
                        },
                        "end_column": {
                            "type": "integer",
                            "description": "1-based end column for manual range edit",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace every old_string occurrence",
                            "default": False,
                        },
                    },
                    "required": ["new_text"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Apply a position-aware file edit."""
        try:
            match_id = arguments.get("match_id")
            relative_path = arguments.get("path")
            anchor = self.anchor_store.get(match_id) if match_id else None
            if anchor and not relative_path:
                relative_path = anchor["path"]
            if not relative_path:
                return ToolCallResult(
                    tool=self.name,
                    success=False,
                    output="path is required when match_id is not provided.",
                )

            path = _resolve_path(str(relative_path))
            message, _count = apply_file_edit(
                path,
                new_text=str(arguments.get("new_text", "")),
                anchor_store=self.anchor_store,
                match_id=match_id,
                old_string=arguments.get("old_string"),
                start_line=arguments.get("start_line"),
                start_column=arguments.get("start_column"),
                end_line=arguments.get("end_line"),
                end_column=arguments.get("end_column"),
                replace_all=bool(arguments.get("replace_all", False)),
            )
            return ToolCallResult(tool=self.name, success=True, output=message)
        except Exception as exc:
            return ToolCallResult(tool=self.name, success=False, output=str(exc))


class ListDirectoryTool(BaseTool):
    """List directory contents."""

    name = "list_directory"
    description = "List files and folders in a workspace directory"

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative directory path", "default": "."},
                    },
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """List directory entries."""
        from agentforge.services.command_audit import record_list_directory

        relative_path = str(arguments.get("path", ".")).strip() or "."
        try:
            path = _resolve_path(relative_path)
            if not path.is_dir():
                output = "Not a directory"
                await record_list_directory(relative_path, output=output, success=False)
                return ToolCallResult(tool=self.name, success=False, output=output)
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            lines = [f"{'[DIR]' if e.is_dir() else '[FILE]'} {e.name}" for e in entries[:200]]
            output = "\n".join(lines) or "(empty)"
            await record_list_directory(relative_path, output=output, success=True)
            return ToolCallResult(tool=self.name, success=True, output=output)
        except Exception as exc:
            await record_list_directory(relative_path, output=str(exc), success=False)
            return ToolCallResult(tool=self.name, success=False, output=str(exc))


class ShellTool(BaseTool):
    """
    Shell command tool stub.

    Runtime execution is handled centrally by command_audit.execute_shell_command.
    """

    name = "run_command"
    description = "Run an allowed shell command in the workspace"

    def __init__(self, approval_callback=None) -> None:
        """Initialize with optional approval handler."""
        self.approval_callback = approval_callback

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string", "description": "Relative working directory"},
                    },
                    "required": ["command"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Shell execution must go through command_audit.execute_shell_command."""
        return ToolCallResult(
            tool=self.name,
            success=False,
            output="Shell execution must go through the central command audit service.",
        )


class RememberTool(BaseTool):
    """Store information in persistent memory."""

    name = "remember"
    description = "Store a key-value fact in this chat's memory"

    def __init__(self, memory_callback=None) -> None:
        """Initialize with memory store callback."""
        self.memory_callback = memory_callback

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                        "scope": {"type": "string", "enum": ["chat"], "default": "chat"},
                    },
                    "required": ["key", "value"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Save memory entry."""
        if not self.memory_callback:
            return ToolCallResult(tool=self.name, success=False, output="Memory not available")
        await self.memory_callback(
            "chat",
            arguments["key"],
            arguments["value"],
        )
        return ToolCallResult(tool=self.name, success=True, output="Memory saved")


class WebSearchTool(BaseTool):
    """Search the public web without API keys."""

    name = "web_search"
    description = (
        "Search the internet for current information using DuckDuckGo and Wikipedia. "
        "Use for documentation, news, facts, and research."
    )

    def schema(self) -> dict[str, Any]:
        """Return tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results (1-10)",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolCallResult:
        """Run a web search and return formatted results."""
        if not settings.web_search_enabled:
            return ToolCallResult(
                tool=self.name,
                success=False,
                output="Web search is disabled in settings.",
            )

        query = str(arguments.get("query", "")).strip()
        if not query:
            return ToolCallResult(tool=self.name, success=False, output="Search query is required.")

        max_results = int(arguments.get("max_results") or settings.web_search_max_results)
        max_results = max(1, min(max_results, 10))

        try:
            from agentforge.tools.web_search import format_search_results, search_web

            results = await search_web(query, max_results=max_results)
            output = format_search_results(query, results)
            if len(output) > settings.max_output_chars:
                output = output[: settings.max_output_chars] + "\n... [truncated]"
            return ToolCallResult(tool=self.name, success=True, output=output)
        except Exception as exc:
            return ToolCallResult(tool=self.name, success=False, output=f"Web search failed: {exc}")


class ToolRegistry:
    """Registry and executor for all agent tools."""

    def __init__(self) -> None:
        """Initialize empty tool registry."""
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        """Return all tool schemas for LLM."""
        return [t.schema() for t in self._tools.values()]

    async def execute(self, name: str, arguments: str | dict) -> ToolCallResult:
        """Execute tool by name."""
        if name not in self._tools:
            return ToolCallResult(tool=name, success=False, output=f"Unknown tool: {name}")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return await self._tools[name].execute(arguments)
