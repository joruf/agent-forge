"""Detect workspace file, directory, and command intents from user messages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agentforge.config import settings


@dataclass
class WorkspaceIntent:
    """Parsed workspace action intent from user input."""

    wants_file_creation: bool = False
    wants_directory_creation: bool = False
    wants_command_execution: bool = False
    target_paths: list[str] = field(default_factory=list)
    target_dirs: list[str] = field(default_factory=list)
    raw_paths: list[str] = field(default_factory=list)

    @property
    def requires_tools(self) -> bool:
        """
        Return True when workspace tools should be enabled.

        :return: Whether tools are required for this request
        """
        return (
            self.wants_file_creation
            or self.wants_directory_creation
            or self.wants_command_execution
        )

    def build_prompt_addon(self) -> str:
        """
        Build system-prompt instructions for detected workspace actions.

        :return: Additional prompt text or empty string
        """
        if not self.requires_tools:
            return ""

        lines = [
            "\n\nWorkspace action required:",
            "The user wants changes on disk — not just code shown in chat.",
            "You MUST use tools to fulfill this request:",
            "- write_file: create or update files (creates parent directories automatically)",
            "- list_directory: inspect existing project structure",
            "- run_command: run shell commands when needed (mkdir, git, npm, etc.)",
            "Never reply with only JSON, code blocks, or pasted file contents.",
            "Always call write_file for each file you create.",
        ]

        if self.target_dirs:
            dirs = ", ".join(self.target_dirs)
            lines.append(f"Target directory (relative to workspace root): {dirs}")

        if self.target_paths:
            paths = ", ".join(self.target_paths)
            lines.append(f"Target path(s) (relative to workspace root): {paths}")

        if self.raw_paths:
            raw = ", ".join(self.raw_paths)
            lines.append(f"User-mentioned path(s): {raw}")

        return "\n".join(lines)


CREATE_KEYWORDS = re.compile(
    r"\b("
    r"create|creates|creating|write|writes|writing|save|saves|saving|store|stores|"
    r"speichern|speichere|speichert|abspeichern|gespeichert|abzuspeichern|"
    r"erstellen|erstellt|erzeuge|erzeugen|anlegen|anlege|"
    r"generiere|generieren|schreibe|schreiben|"
    r"file|files|datei|dateien|verzeichnis|ordner|directory|folder|"
    r"programm|program|script|skript|entwurf|template|"
    r"header|footer|menü|menu|content|"
    r"mkdir|touch|kopieren|copy to|implement|implementier"
    r")\b",
    re.IGNORECASE,
)

COMMAND_KEYWORDS = re.compile(
    r"\b("
    r"run|execute|terminal|shell|command|befehl|ausführen|ausfuehren|führe aus|fuehre aus"
    r")\b",
    re.IGNORECASE,
)

ABS_PATH = re.compile(r"(?<![\w./-])(/[\w./-]+)")
PATH_AFTER_KEYWORD = re.compile(
    r"(?:"
    r"(?:speicher(?:n|e|t)?|save|store|write|create|erstell(?:en|t)?|unter|under|in|to|nach|path|pfad)"
    r"\s*(?:den code|the code|es|it|them|die datei(?:en)?|files?)?\s*)?"
    r":?\s*"
    r"(/[\w./-]+)",
    re.IGNORECASE,
)

CODE_EXTENSIONS = re.compile(
    r"\.\w{1,10}\b",
)

NAMED_FOLDER = re.compile(
    r"(?:"
    r"(?:ordner|verzeichnis|folder|directory)"
    r"(?:\s+(?:mit\s+(?:dem\s+)?)?namen)?"
    r"|"
    r"(?:folder|directory|ordner|verzeichnis)\s+(?:named|called|genannt)"
    r")"
    r"[\s.:,]*"
    r"([\w.-]+)",
    re.IGNORECASE,
)


def extract_named_folder(user_content: str) -> str | None:
    """
    Extract a folder name from natural-language create-directory requests.

    :param user_content: User message text
    :return: Folder basename or None
    """
    match = NAMED_FOLDER.search(user_content or "")
    if not match:
        return None
    name = match.group(1).strip().strip(".")
    if not name or name.lower() in {"mit", "dem", "namen", "name", "der", "die", "das"}:
        return None
    return name


def _to_workspace_relative(path_str: str) -> tuple[str | None, str | None]:
    """
    Convert a user path to workspace-relative form.

    :param path_str: Absolute or relative path from user text
    :return: Tuple of (relative_path, directory_relative) or (None, None)
    """
    raw = path_str.strip().strip("'\"")
    if not raw:
        return None, None

    root = settings.workspace_root.resolve()
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(root)):
                return None, None
            relative = str(resolved.relative_to(root))
        else:
            target = (root / raw.lstrip("/")).resolve()
            if not str(target).startswith(str(root)):
                return None, None
            relative = str(target.relative_to(root))

        if relative.endswith("/"):
            relative = relative.rstrip("/")
        directory = relative if not CODE_EXTENSIONS.search(Path(relative).name) else str(
            Path(relative).parent
        )
        if directory == ".":
            directory = relative
        return relative, directory
    except (OSError, ValueError):
        return None, None


def _extract_paths(user_content: str) -> list[str]:
    """
    Extract filesystem paths mentioned in user text.

    :param user_content: User message
    :return: Deduplicated path strings
    """
    found: list[str] = []
    seen: set[str] = set()

    for match in PATH_AFTER_KEYWORD.finditer(user_content):
        path = match.group(1).rstrip(".,;:")
        if path not in seen:
            seen.add(path)
            found.append(path)

    for match in ABS_PATH.finditer(user_content):
        path = match.group(1).rstrip(".,;:")
        if path not in seen:
            seen.add(path)
            found.append(path)

    return found


def detect_workspace_intent(user_content: str) -> WorkspaceIntent:
    """
    Detect whether the user wants files or directories created in the workspace.

    :param user_content: User message text
    :return: Parsed workspace intent
    """
    text = user_content or ""
    wants_create = CREATE_KEYWORDS.search(text) is not None
    wants_command = COMMAND_KEYWORDS.search(text) is not None
    raw_paths = _extract_paths(text)

    target_paths: list[str] = []
    target_dirs: list[str] = []
    for raw in raw_paths:
        relative, directory = _to_workspace_relative(raw)
        if relative and relative not in target_paths:
            target_paths.append(relative)
        if directory and directory not in target_dirs:
            target_dirs.append(directory)

    save_phrase = re.search(
        r"(speicher|save|store|write|ab\s*speicher|unter|under|in ordner|in folder|to folder)",
        text,
        re.IGNORECASE,
    )
    wants_file_creation = wants_create or bool(save_phrase and raw_paths)
    wants_directory_creation = wants_file_creation and bool(target_dirs)

    named_folder = extract_named_folder(text)
    if named_folder and target_dirs:
        enriched_dirs: list[str] = []
        enriched_paths: list[str] = []
        for directory in target_dirs:
            combined = f"{directory}/{named_folder}"
            if combined not in enriched_dirs:
                enriched_dirs.append(combined)
        for relative in target_paths:
            path = Path(relative)
            if path.suffix:
                parent = str(path.parent)
                if parent in {"", "."}:
                    combined = f"{target_dirs[0]}/{named_folder}/{path.name}"
                else:
                    combined = f"{parent}/{named_folder}/{path.name}"
                if combined not in enriched_paths:
                    enriched_paths.append(combined)
            else:
                combined = f"{relative}/{named_folder}"
                if combined not in enriched_dirs:
                    enriched_dirs.append(combined)
        if enriched_dirs:
            target_dirs = enriched_dirs
        if enriched_paths:
            target_paths = enriched_paths

    return WorkspaceIntent(
        wants_file_creation=wants_file_creation,
        wants_directory_creation=wants_directory_creation,
        wants_command_execution=wants_command,
        target_paths=target_paths,
        target_dirs=target_dirs,
        raw_paths=raw_paths,
    )
