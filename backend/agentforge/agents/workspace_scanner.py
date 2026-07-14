"""Scan user-mentioned workspace paths and collect context for agents."""

from __future__ import annotations

from pathlib import Path

from agentforge.agents.workspace_intent import WorkspaceIntent
from agentforge.config import settings
from agentforge.services.command_audit import record_list_directory, record_read_file
from agentforge.tools.registry import _resolve_path

IMPORTANT_FILE_NAMES: frozenset[str] = frozenset({
    "README.md",
    "README",
    "README.txt",
    "package.json",
    "package-lock.json",
    "composer.json",
    "composer.lock",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "setup.py",
    "index.html",
    "index.php",
    "main.py",
    "app.py",
    "main.ts",
    "app.ts",
    "app.js",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    ".gitignore",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "webpack.config.js",
    "angular.json",
    ".env.example",
    "phpunit.xml",
    "pest.php",
})

IMPORTANT_SUFFIXES: frozenset[str] = frozenset({
    ".php",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".vue",
    ".json",
    ".md",
    ".yaml",
    ".yml",
    ".xml",
    ".sql",
    ".env",
})

MAX_SCAN_DIRS = 3
MAX_DIR_ENTRIES = 120
MAX_READ_FILES = 10
MAX_FILE_CHARS = 3500


def _scan_targets(intent: WorkspaceIntent) -> list[str]:
    """
    Build a deduplicated list of workspace-relative directories to scan.

    :param intent: Parsed workspace intent
    :return: Relative directory paths
    """
    targets: list[str] = []
    seen: set[str] = set()

    for directory in intent.target_dirs:
        if directory and directory not in seen:
            seen.add(directory)
            targets.append(directory)

    for relative in intent.target_paths:
        path = Path(relative)
        directory = str(path.parent) if path.suffix else relative
        if directory in {"", "."}:
            directory = relative
        if directory and directory not in seen:
            seen.add(directory)
            targets.append(directory)

    return targets[:MAX_SCAN_DIRS]


def _score_file(path: Path) -> tuple[int, str]:
    """
    Rank files by usefulness for agent context.

    :param path: Candidate file path
    :return: Sortable score tuple
    """
    name = path.name
    if name in IMPORTANT_FILE_NAMES:
        return (0, name.lower())
    if name.startswith(".env") and name != ".env":
        return (1, name.lower())
    suffix = path.suffix.lower()
    if suffix in IMPORTANT_SUFFIXES:
        return (2, name.lower())
    return (9, name.lower())


async def _read_file_excerpt(relative_path: str) -> str | None:
    """
    Read a workspace file with truncation.

    :param relative_path: Workspace-relative file path
    :return: File excerpt or None when unreadable
    """
    try:
        absolute = _resolve_path(relative_path)
        if not absolute.is_file():
            await record_read_file(
                relative_path,
                output=f"File not found: {relative_path}",
                success=False,
            )
            return None
        content = absolute.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n... [truncated]"
        await record_read_file(relative_path, output=content, success=True)
        return content
    except (OSError, PermissionError, UnicodeError) as exc:
        await record_read_file(relative_path, output=str(exc), success=False)
        return None


async def _scan_directory(relative_dir: str) -> str:
    """
    Scan one workspace directory and return a formatted summary.

    :param relative_dir: Workspace-relative directory path
    :return: Human-readable scan summary
    """
    try:
        absolute = _resolve_path(relative_dir)
    except PermissionError:
        summary = f"Directory `{relative_dir}` is outside the workspace root."
        await record_list_directory(relative_dir, output=summary, success=False)
        return summary

    if absolute.is_file():
        relative_dir = str(Path(relative_dir).parent)
        try:
            absolute = _resolve_path(relative_dir)
        except PermissionError:
            summary = f"Path `{relative_dir}` is outside the workspace root."
            await record_list_directory(relative_dir, output=summary, success=False)
            return summary

    if not absolute.exists():
        summary = (
            f"Directory `{relative_dir}` does not exist yet "
            f"(relative to workspace root `{settings.workspace_root}`)."
        )
        await record_list_directory(relative_dir, output=summary, success=False)
        return summary

    if not absolute.is_dir():
        summary = f"Path `{relative_dir}` is not a directory."
        await record_list_directory(relative_dir, output=summary, success=False)
        return summary

    entries = sorted(absolute.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    lines = [f"Directory `{relative_dir}`:"]

    if not entries:
        lines.append("- (empty)")
        summary = "\n".join(lines)
        await record_list_directory(relative_dir, output=summary, success=True)
        return summary

    for entry in entries[:MAX_DIR_ENTRIES]:
        prefix = "[DIR]" if entry.is_dir() else "[FILE]"
        lines.append(f"- {prefix} {entry.name}")
    if len(entries) > MAX_DIR_ENTRIES:
        lines.append(f"- ... {len(entries) - MAX_DIR_ENTRIES} more entries")

    files = [entry for entry in entries if entry.is_file()]
    ranked = sorted(files, key=_score_file)[:MAX_READ_FILES]

    listing_output = "\n".join(lines)
    await record_list_directory(relative_dir, output=listing_output, success=True)

    for entry in ranked:
        if _score_file(entry)[0] > 2 and entry.name not in IMPORTANT_FILE_NAMES:
            continue
        relative_file = str(Path(relative_dir) / entry.name)
        excerpt = await _read_file_excerpt(relative_file)
        if not excerpt:
            continue
        lines.append("")
        lines.append(f"File `{relative_file}`:")
        lines.append(excerpt)

    return "\n".join(lines)


async def build_workspace_path_context(intent: WorkspaceIntent) -> str:
    """
    Build workspace context for paths mentioned in the user request.

    :param intent: Parsed workspace intent
    :return: Context block for system prompts or empty string
    """
    if intent.wants_file_read:
        contents: dict[str, str] = {}
        for relative in intent.target_paths:
            if not Path(relative).suffix:
                continue
            excerpt = await _read_file_excerpt(relative)
            if excerpt is not None:
                contents[relative] = excerpt
            else:
                contents[relative] = f"[ERROR] File not found: {relative}"
        if contents:
            lines = [
                "Verified file content from disk (use this in your response; do not invent text):",
            ]
            for relative_path, payload in contents.items():
                lines.append(f"\nFile `{relative_path}`:")
                if payload.startswith("[ERROR]"):
                    lines.append(payload)
                else:
                    lines.append("```")
                    lines.append(payload)
                    lines.append("```")
            return "\n".join(lines)

    targets = _scan_targets(intent)
    if not targets:
        return ""

    sections = [await _scan_directory(target) for target in targets]
    body = "\n\n".join(section for section in sections if section)
    if not body:
        return ""

    return (
        "Existing workspace context for user-mentioned path(s):\n"
        f"{body}\n\n"
        "Use this information before creating or modifying files."
    )
