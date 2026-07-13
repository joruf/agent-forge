"""Catalog of deliverable file types inferred from user request context."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DeliverableFileType:
    """
    One inferable deliverable file type from natural-language context.

    :param type_id: Stable identifier for tests and logging
    :param extension: File extension including the dot (e.g. ``.js``)
    :param default_filename: Default basename when the user omits a filename
    :param keyword_pattern: Regex matched against the full user message
    :param priority: Lower values are evaluated first (more specific wins)
    """

    type_id: str
    extension: str
    default_filename: str
    keyword_pattern: re.Pattern[str]
    priority: int = 100


def _pattern(*terms: str) -> re.Pattern[str]:
    """
    Build a word-boundary regex from keyword terms.

    :param terms: Keyword fragments joined as alternation
    :return: Compiled case-insensitive pattern
    """
    escaped = "|".join(re.escape(term) for term in terms)
    return re.compile(rf"\b(?:{escaped})\b", re.IGNORECASE)


# Ordered catalog: more specific types must appear before broader ones.
DELIVERABLE_FILE_TYPES: tuple[DeliverableFileType, ...] = (
    DeliverableFileType(
        type_id="tsx",
        extension=".tsx",
        default_filename="App.tsx",
        keyword_pattern=_pattern("tsx", "typescript react", "typescript-react"),
        priority=10,
    ),
    DeliverableFileType(
        type_id="jsx",
        extension=".jsx",
        default_filename="App.jsx",
        keyword_pattern=_pattern("jsx", "react component", "react-component"),
        priority=20,
    ),
    DeliverableFileType(
        type_id="typescript",
        extension=".ts",
        default_filename="index.ts",
        keyword_pattern=_pattern(
            "typescript",
            "type script",
            "ts-datei",
            "ts datei",
            "ts-code",
            "ts code",
        ),
        priority=30,
    ),
    DeliverableFileType(
        type_id="vue",
        extension=".vue",
        default_filename="App.vue",
        keyword_pattern=_pattern("vue", "vue component", "vue-component", "vue-datei"),
        priority=40,
    ),
    DeliverableFileType(
        type_id="javascript",
        extension=".js",
        default_filename="app.js",
        keyword_pattern=_pattern(
            "javascript",
            "java script",
            "js-datei",
            "js datei",
            "js-code",
            "js code",
            "js-skript",
            "js skript",
            "js-script",
            "js script",
        ),
        priority=50,
    ),
    DeliverableFileType(
        type_id="php",
        extension=".php",
        default_filename="index.php",
        keyword_pattern=_pattern(
            "php",
            "php-datei",
            "php datei",
            "php-seite",
            "php seite",
            "php-website",
            "php website",
        ),
        priority=60,
    ),
    DeliverableFileType(
        type_id="html",
        extension=".html",
        default_filename="index.html",
        keyword_pattern=_pattern(
            "html",
            "htm",
            "html-datei",
            "html datei",
            "webseite",
            "website",
            "homepage",
            "webpage",
            "web page",
            "webprojekt",
            "web projekt",
        ),
        priority=70,
    ),
    DeliverableFileType(
        type_id="css",
        extension=".css",
        default_filename="styles.css",
        keyword_pattern=_pattern(
            "css",
            "css-datei",
            "css datei",
            "stylesheet",
            "style sheet",
            "stylesheet-datei",
            "stylesheet datei",
            "styles",
            "stilblatt",
            "stil datei",
        ),
        priority=80,
    ),
    DeliverableFileType(
        type_id="python",
        extension=".py",
        default_filename="main.py",
        keyword_pattern=_pattern(
            "python",
            "py-datei",
            "py datei",
            "python-skript",
            "python skript",
            "python-script",
            "python script",
        ),
        priority=90,
    ),
    DeliverableFileType(
        type_id="json",
        extension=".json",
        default_filename="data.json",
        keyword_pattern=_pattern("json", "json-datei", "json datei"),
        priority=100,
    ),
    DeliverableFileType(
        type_id="markdown",
        extension=".md",
        default_filename="README.md",
        keyword_pattern=_pattern(
            "markdown",
            "md-datei",
            "md datei",
            "readme",
        ),
        priority=110,
    ),
    DeliverableFileType(
        type_id="sql",
        extension=".sql",
        default_filename="schema.sql",
        keyword_pattern=_pattern("sql", "sql-datei", "sql datei"),
        priority=120,
    ),
    DeliverableFileType(
        type_id="shell",
        extension=".sh",
        default_filename="script.sh",
        keyword_pattern=_pattern(
            "shell",
            "bash",
            "sh-datei",
            "sh datei",
            "shell-skript",
            "shell skript",
        ),
        priority=130,
    ),
    DeliverableFileType(
        type_id="yaml",
        extension=".yaml",
        default_filename="config.yaml",
        keyword_pattern=_pattern("yaml", "yml"),
        priority=140,
    ),
    DeliverableFileType(
        type_id="xml",
        extension=".xml",
        default_filename="data.xml",
        keyword_pattern=_pattern("xml", "xml-datei", "xml datei"),
        priority=150,
    ),
)

HTML_STRUCTURE = re.compile(
    r"\b(header|footer|menü|menu|content|nav|navigation)\b",
    re.IGNORECASE,
)

_SORTED_DELIVERABLE_FILE_TYPES: tuple[DeliverableFileType, ...] = tuple(
    sorted(DELIVERABLE_FILE_TYPES, key=lambda item: item.priority)
)

WEB_STACK_TYPE_IDS: frozenset[str] = frozenset({"html", "css", "javascript"})

PLANNED_TYPE_ORDER: dict[str, int] = {
    "html": 10,
    "css": 20,
    "javascript": 30,
    "php": 40,
    "tsx": 50,
    "jsx": 60,
    "typescript": 70,
    "vue": 80,
    "python": 90,
}


def _resolve_type_conflicts(
    matched: list[DeliverableFileType],
    user_content: str,
) -> list[DeliverableFileType]:
    """
    Remove overlapping file-type matches that would over-plan deliverables.

    :param matched: Candidate deliverable types
    :param user_content: Original user message
    :return: Filtered deliverable types
    """
    type_ids = {spec.type_id for spec in matched}
    result = list(matched)
    text = user_content or ""

    if "tsx" in type_ids and "jsx" in type_ids:
        result = [spec for spec in result if spec.type_id != "jsx"]

    if "php" in type_ids and "html" in type_ids and re.search(r"\bphp\b", text, re.IGNORECASE):
        result = [spec for spec in result if spec.type_id != "html"]

    return result


def match_deliverable_file_types(user_content: str) -> list[DeliverableFileType]:
    """
    Match all deliverable file types referenced in a user message.

    :param user_content: User message text
    :return: Matched file type specs in planned creation order
    """
    text = user_content or ""
    matched: list[DeliverableFileType] = []
    seen: set[str] = set()
    for spec in _SORTED_DELIVERABLE_FILE_TYPES:
        if spec.type_id in seen:
            continue
        if spec.keyword_pattern.search(text):
            seen.add(spec.type_id)
            matched.append(spec)

    if not matched and HTML_STRUCTURE.search(text):
        html_spec = next(item for item in DELIVERABLE_FILE_TYPES if item.type_id == "html")
        matched.append(html_spec)

    matched = _resolve_type_conflicts(matched, text)

    if len(matched) > 1:
        matched.sort(
            key=lambda item: (
                PLANNED_TYPE_ORDER.get(item.type_id, 1000),
                item.priority,
            )
        )
    return matched


def match_deliverable_file_type(user_content: str) -> DeliverableFileType | None:
    """
    Match the most specific deliverable file type for a user message.

    :param user_content: User message text
    :return: Matched file type spec or None
    """
    matched = match_deliverable_file_types(user_content)
    return matched[0] if matched else None


def list_deliverable_file_types() -> list[dict[str, str]]:
    """
    Return the deliverable type catalog for documentation and diagnostics.

    :return: Serializable list of type metadata
    """
    return [
        {
            "type_id": spec.type_id,
            "extension": spec.extension,
            "default_filename": spec.default_filename,
            "priority": str(spec.priority),
        }
        for spec in _SORTED_DELIVERABLE_FILE_TYPES
    ]
