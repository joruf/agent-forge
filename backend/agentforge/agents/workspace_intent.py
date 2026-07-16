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
    wants_file_read: bool = False
    wants_list_directory: bool = False
    wants_directory_creation: bool = False
    wants_command_execution: bool = False
    wants_file_edit: bool = False
    wants_derived_file: bool = False
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
            or self.wants_file_read
            or self.wants_list_directory
            or self.wants_directory_creation
            or self.wants_command_execution
            or self.wants_file_edit
            or self.wants_derived_file
        )

    def build_prompt_addon(self) -> str:
        """
        Build system-prompt instructions for detected workspace actions.

        :return: Additional prompt text or empty string
        """
        if self.wants_file_creation and self.wants_file_read:
            lines = [
                "\n\nWorkspace workflow required (complete every step in order):",
                "1. Create missing directories (write_file creates parent folders automatically).",
                "2. write_file for each requested file with the exact user-specified content.",
                "3. read_file for each created file and quote the on-disk content verbatim.",
            ]
            if self.wants_file_edit:
                lines.append(
                    "4. Edit the file on disk (write_file with replaced text) when the user "
                    "asked to replace or change content."
                )
            if self.wants_derived_file:
                lines.append(
                    "5. Create any follow-up file whose name is derived from file content "
                    "(for example a .txt file named after the H1 text in HTML)."
                )
            lines.append("Never skip steps. Never invent file contents.")
            if self.target_paths:
                paths = ", ".join(self.target_paths)
                lines.append(f"Target file path(s) (relative to workspace root): {paths}")
            if self.target_dirs:
                dirs = ", ".join(self.target_dirs)
                lines.append(f"Target directory (relative to workspace root): {dirs}")
            if self.raw_paths:
                raw = ", ".join(self.raw_paths)
                lines.append(f"User-mentioned path(s): {raw}")
            lines.append(
                "Use workspace-relative paths only (for example GitHub/Test12/index.html). "
                "Never pass absolute filesystem paths to read_file or write_file."
            )
            return "\n".join(lines)

        if self.wants_file_read:
            lines = [
                "\n\nWorkspace read action required:",
                "The user wants existing file content shown in chat — not written to disk.",
                "You MUST use read_file for each requested path and quote the content verbatim.",
                "Never reply with only JSON, status placeholders, or invented file contents.",
                "Do not call write_file unless the user explicitly asked to create or modify files.",
            ]
            if self.target_paths:
                paths = ", ".join(self.target_paths)
                lines.append(f"Target path(s) (relative to workspace root): {paths}")
            if self.raw_paths:
                raw = ", ".join(self.raw_paths)
                lines.append(f"User-mentioned path(s): {raw}")
            return "\n".join(lines)

        if self.wants_list_directory:
            lines = [
                "\n\nDirectory listing required:",
                "Use list_directory to inspect the requested folder and summarize the entries.",
            ]
            if self.target_dirs:
                dirs = ", ".join(self.target_dirs)
                lines.append(f"Target directory (relative to workspace root): {dirs}")
            return "\n".join(lines)

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
    r"verzeichnis|ordner|directory|folder|"
    r"programm|program|script|skript|entwurf|template|"
    r"header|footer|menü|menu|"
    r"mkdir|touch|kopieren|copy to|implement|implementier"
    r")\b",
    re.IGNORECASE,
)

READ_KEYWORDS = re.compile(
    r"\b("
    r"read|reads|reading|lese|lies|lesen|anzeigen|anzeige|zeige|zeig|show|display|"
    r"ausgeben|ausgabe|auflisten|liste|list|inhalt|contents|dateiinhalt|"
    r"file content|get content|print|output|darstellen|mitteilen|nenne mir|gib mir|"
    r"what is in|was steht|was ist in|open file|öffne|oeffne"
    r")\b",
    re.IGNORECASE,
)

LIST_DIRECTORY_KEYWORDS = re.compile(
    r"\b("
    r"list directory|list dir|list folder|verzeichnis auflisten|ordner auflisten|"
    r"verzeichnis anzeigen|ordner anzeigen|directory listing|folder listing|"
    r"was liegt in|what is in the folder|dateien im ordner|files in folder"
    r")\b",
    re.IGNORECASE,
)

WRITE_VERBS = re.compile(
    r"\b("
    r"create|write|save|speicher|speichern|erstell|generier|schreib|implement|"
    r"mkdir|touch|anleg|abspeicher"
    r")",
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
    r"(?:speicher(?:n|e|t)?|save|store|write|create|erstell(?:en|t)?|unter|under|in|to|nach|path|pfad|"
    r"lese|lies|read|zeige|show|open|öffne|oeffne)"
    r"\s*(?:den code|the code|es|it|them|die datei(?:en)?|files?|inhalt|content)?\s*)?"
    r":?\s*"
    r"(/[\w./-]+)",
    re.IGNORECASE,
)

CODE_EXTENSIONS = re.compile(
    r"\.\w{1,10}\b",
)

REQUESTED_FILE_NAME = re.compile(
    r"\b([\w.-]+\.(?:php|html|htm|css|js|ts|tsx|jsx|py|md|json|txt|vue|sql|xml|yaml|yml|sh|pdf|docx))\b",
    re.IGNORECASE,
)

EDIT_KEYWORDS = re.compile(
    r"\b("
    r"bearbeit\w*|edit(?:ing|s|ed)?|änder\w*|ander\w*|update\w*|modif\w*|"
    r"tausch\w*|replace\w*|ersetz\w*"
    r")\b",
    re.IGNORECASE,
)

REPLACE_QUOTED = re.compile(
    r'tausch(?:e)?\s+"([^"]+)"\s+aus\s+gegen\s+"([^"]+)"',
    re.IGNORECASE,
)

REPLACE_AGAINST = re.compile(
    r'(?:tausch(?:e)?|replace)\s+["\']?(.+?)["\']?\s+(?:aus\s+)?gegen\s+["\']?(.+?)["\']?',
    re.IGNORECASE,
)

DERIVED_TXT_FROM_H1 = re.compile(
    r"(?:"
    r"(?:neue\s+)?datei.*?(?:namen|name).*?(?:h1|überschrift).*?(?:\.txt|dateiendung\s*\.txt)"
    r"|"
    r"(?:namen|name)\s+(?:des\s+)?(?:inhalts?\s+)?(?:des\s+)?(?:h1(?:-tag)?s?(?:\s+inhalts?)?|überschrift)"
    r".*?(?:\.txt|dateiendung\s*\.txt|dateiendung)"
    r"|"
    r"(?:named|name(?:d)?\s+after).*?(?:h1|heading).*?(?:\.txt|txt\s+file)"
    r"|"
    r"file.*?named.*?h1.*?(?:\.txt|txt)"
    r")",
    re.IGNORECASE | re.DOTALL,
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


def detect_derived_filename_intent(user_content: str) -> bool:
    """
    Return True when the user wants a file named from HTML H1 content.

    :param user_content: User message text
    :return: Whether a derived filename step is required
    """
    text = user_content or ""
    if not DERIVED_TXT_FROM_H1.search(text):
        return False
    return bool(re.search(r"\b(?:h1|überschrift|heading)\b", text, re.IGNORECASE))


def extract_named_folder(user_content: str) -> str | None:
    """
    Extract a folder name from natural-language create-directory requests.

    :param user_content: User message text
    :return: Folder basename or None
    """
    for match in NAMED_FOLDER.finditer(user_content or ""):
        name = match.group(1).strip().strip(".")
        if (
            not name
            or name.startswith("_")
            or name.lower() in {"mit", "dem", "namen", "name", "der", "die", "das", "workspace"}
        ):
            continue
        return name
    return None


def detect_file_edit_intent(user_content: str) -> bool:
    """
    Return True when the user asked to modify an existing file.

    :param user_content: User message text
    :return: Whether an edit/replace step is required
    """
    text = user_content or ""
    if not EDIT_KEYWORDS.search(text):
        return False
    return bool(REPLACE_QUOTED.search(text) or REPLACE_AGAINST.search(text))


def extract_text_replacement(user_content: str) -> tuple[str, str] | None:
    """
    Extract old/new text pair from replace instructions.

    :param user_content: User message text
    :return: Tuple of (old_text, new_text) or None
    """
    text = user_content or ""
    match = REPLACE_QUOTED.search(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = REPLACE_AGAINST.search(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None


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
    Detect whether the user wants files read, created, or listed in the workspace.

    :param user_content: User message text
    :return: Parsed workspace intent
    """
    text = user_content or ""
    wants_list = LIST_DIRECTORY_KEYWORDS.search(text) is not None
    wants_create = CREATE_KEYWORDS.search(text) is not None and not wants_list
    wants_read = READ_KEYWORDS.search(text) is not None
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
    has_write = bool(
        wants_create
        or (save_phrase and raw_paths)
        or WRITE_VERBS.search(text)
    )
    has_read = wants_read and not wants_list

    if has_write and has_read:
        wants_file_creation = True
        wants_file_read = True
    elif has_read:
        wants_file_creation = False
        wants_file_read = True
    elif has_write:
        wants_file_creation = True
        wants_file_read = False
    else:
        wants_file_creation = False
        wants_file_read = False

    wants_directory_creation = wants_file_creation and bool(target_dirs)
    wants_list_directory = wants_list and not wants_file_read and not wants_file_creation
    wants_file_edit = detect_file_edit_intent(text)
    wants_derived_file = detect_derived_filename_intent(text)

    named_folder = extract_named_folder(text)
    if named_folder and target_dirs and (
        wants_file_creation or wants_file_read or wants_file_edit
    ):
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

    file_paths = [path for path in target_paths if CODE_EXTENSIONS.search(Path(path).name)]
    dir_paths = [path for path in target_paths if path not in file_paths]
    for directory in dir_paths:
        if directory not in target_dirs:
            target_dirs.append(directory)

    if wants_file_creation and not file_paths:
        seen_names: set[str] = set()
        named = extract_named_folder(text)
        named_file = re.search(
            r"(?:datei|file)\s+mit\s+(?:dem\s+)?namen[\s.:,]+([\w.-]+\.\w+)",
            text,
            re.IGNORECASE,
        )
        filenames = [named_file.group(1)] if named_file else [
            match.group(1) for match in REQUESTED_FILE_NAME.finditer(text)
        ]
        base: str | None = None
        if target_dirs:
            if named:
                matching = [
                    directory
                    for directory in target_dirs
                    if directory == named or directory.endswith(f"/{named}")
                ]
                base = matching[0] if matching else max(target_dirs, key=len)
            else:
                base = max(target_dirs, key=len)
        for filename in filenames:
            key = filename.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            if base:
                combined = f"{base}/{filename}"
                if combined not in file_paths:
                    file_paths.append(combined)

    target_paths = file_paths

    return WorkspaceIntent(
        wants_file_creation=wants_file_creation,
        wants_file_read=wants_file_read,
        wants_list_directory=wants_list_directory,
        wants_directory_creation=wants_directory_creation,
        wants_command_execution=wants_command,
        wants_file_edit=wants_file_edit,
        wants_derived_file=wants_derived_file,
        target_paths=target_paths,
        target_dirs=target_dirs,
        raw_paths=raw_paths,
    )
