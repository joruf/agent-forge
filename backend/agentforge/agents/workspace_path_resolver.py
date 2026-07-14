"""Detect and fix obvious workspace path assembly errors."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from agentforge.agents.workspace_intent import WorkspaceIntent, extract_named_folder
from agentforge.tools.registry import normalize_workspace_relative_path


def _file_exists_in_workspace(relative_path: str) -> bool:
    """
    Check whether a workspace-relative file exists without import cycles.

    :param relative_path: Workspace-relative file path
    :return: True when the file exists
    """
    from agentforge.config import settings

    try:
        normalized = normalize_workspace_relative_path(relative_path)
        target = (settings.workspace_root.resolve() / normalized).resolve()
        return target.is_file()
    except (OSError, PermissionError):
        return False

_path_context: ContextVar[PathResolutionContext | None] = ContextVar(
    "workspace_path_context",
    default=None,
)


@dataclass
class PathResolutionContext:
    """Active orchestration context used to remap wrong workspace paths."""

    user_content: str
    intent: WorkspaceIntent
    canonical_paths: list[str] = field(default_factory=list)


def build_path_resolution_context(
    user_content: str,
    intent: WorkspaceIntent,
) -> PathResolutionContext:
    """
    Build canonical path hints for the current workspace request.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :return: Path resolution context for tool calls
    """
    canonical: list[str] = []
    seen: set[str] = set()

    def add_path(path: str | None) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        canonical.append(path)

    from agentforge.agents.workspace_executor import (
        plan_deliverable_files,
        resolve_read_file_paths,
    )

    for path in plan_deliverable_files(user_content, intent):
        add_path(path)
    for path in resolve_read_file_paths(user_content, intent):
        add_path(path)
    for path in intent.target_paths:
        add_path(path)
    for path in intent.target_dirs:
        add_path(path)

    return PathResolutionContext(
        user_content=user_content,
        intent=intent,
        canonical_paths=canonical,
    )


def activate_path_resolution_context(context: PathResolutionContext):
    """
    Activate path resolution context for the current async task.

    :param context: Path resolution context
    :return: ContextVar reset token
    """
    return _path_context.set(context)


def deactivate_path_resolution_context(token) -> None:
    """
    Restore the previous path resolution context.

    :param token: Token returned by activate_path_resolution_context
    """
    _path_context.reset(token)


def collapsed_target_directory(target_dir: str, named_folder: str) -> str | None:
    """
    Return the parent directory with the named folder segment removed.

    Example: GitHub/Test12 + Test12 -> GitHub

    :param target_dir: Workspace-relative target directory
    :param named_folder: Named folder from the user request
    :return: Collapsed directory or None
    """
    parts = [part for part in Path(target_dir).parts if part != named_folder]
    if not parts:
        return None
    return str(Path(*parts))


def is_obvious_missing_named_folder(
    relative: str,
    named_folder: str,
    target_dirs: list[str],
) -> bool:
    """
    Detect when a path omitted the named folder from the user request.

    :param relative: Workspace-relative path
    :param named_folder: Named folder from the user request
    :param target_dirs: Parsed target directories
    :return: True when the path likely misses the named folder
    """
    path = Path(relative)
    if named_folder in path.parts:
        return False

    for target_dir in target_dirs:
        collapsed = collapsed_target_directory(target_dir, named_folder)
        if not collapsed:
            continue
        collapsed_path = Path(collapsed)
        if path == collapsed_path / path.name:
            return True
        if path.name == path.as_posix() and collapsed_path.name:
            return True
    return False


def remap_missing_named_folder(
    relative: str,
    named_folder: str,
    target_dirs: list[str],
) -> str | None:
    """
    Rebuild a path by inserting the missing named folder segment.

    :param relative: Workspace-relative path that may be missing a folder
    :param named_folder: Named folder from the user request
    :param target_dirs: Parsed target directories
    :return: Remapped path or None
    """
    path = Path(relative)
    if named_folder in path.parts:
        return None

    for target_dir in target_dirs:
        target = Path(target_dir)
        if named_folder not in target.parts:
            continue
        collapsed = collapsed_target_directory(target_dir, named_folder)
        if not collapsed:
            continue
        collapsed_path = Path(collapsed)
        if path == collapsed_path / path.name or path.name == path.as_posix():
            return str(target / path.name)
    return None


def _is_ordered_path_subsequence(
    path_parts: tuple[str, ...],
    candidate_parts: tuple[str, ...],
) -> bool:
    """
    Return True when path parts appear in order inside the candidate path.

    :param path_parts: Shorter path segments
    :param candidate_parts: Longer canonical path segments
    :return: Whether path_parts is an ordered subsequence
    """
    index = 0
    for part in candidate_parts:
        if index < len(path_parts) and path_parts[index] == part:
            index += 1
    return index == len(path_parts)


def remap_to_known_canonical(
    relative: str,
    canonical_paths: list[str],
) -> str | None:
    """
    Map a shortened path to a known canonical file path with the same basename.

    :param relative: Workspace-relative path
    :param canonical_paths: Known canonical paths for this request
    :return: Canonical path or None
    """
    path = Path(relative)
    for canonical in canonical_paths:
        candidate = Path(canonical)
        if not candidate.suffix or path.name != candidate.name:
            continue
        if path == candidate:
            return None
        if len(candidate.parts) <= len(path.parts):
            continue
        if _is_ordered_path_subsequence(path.parts, candidate.parts):
            return canonical
    return None


def resolve_workspace_path(path_str: str) -> str:
    """
    Normalize and correct obvious workspace path assembly errors.

    Absolute paths under the workspace root, shortened parent paths, and bare
    filenames are remapped to canonical deliverable paths when the active
    orchestration context makes the intended target unambiguous.

    :param path_str: Absolute or workspace-relative path
    :return: Corrected workspace-relative path
    """
    normalized = normalize_workspace_relative_path(path_str)
    if _file_exists_in_workspace(normalized):
        return normalized

    context = _path_context.get()
    if context is None:
        return normalized

    candidates: list[str] = []
    named_folder = extract_named_folder(context.user_content)
    if named_folder:
        remapped = remap_missing_named_folder(
            normalized,
            named_folder,
            context.intent.target_dirs,
        )
        if remapped:
            candidates.append(remapped)

    remapped = remap_to_known_canonical(normalized, context.canonical_paths)
    if remapped:
        candidates.append(remapped)

    for candidate in candidates:
        if candidate == normalized:
            continue
        if _file_exists_in_workspace(candidate):
            return candidate
        if named_folder and is_obvious_missing_named_folder(
            normalized,
            named_folder,
            context.intent.target_dirs,
        ):
            return candidate
        if remapped == candidate:
            return candidate

    return normalized
