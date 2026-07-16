"""Deterministic user prompt normalization before intent parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WORD_PATTERN = re.compile(r"\b[\wäöüÄÖÜß.-]+\b", re.UNICODE)

# Intent-critical vocabulary (DE/EN). Keep in sync with workspace_intent patterns.
CANONICAL_KEYWORDS: tuple[str, ...] = (
    "anlegen",
    "anlege",
    "anzeigen",
    "ausgeben",
    "bearbeite",
    "bearbeiten",
    "create",
    "datei",
    "directory",
    "edit",
    "erstelle",
    "erstellen",
    "erstellt",
    "erzeuge",
    "erzeugen",
    "file",
    "folder",
    "generiere",
    "generieren",
    "implement",
    "implementieren",
    "lese",
    "lesen",
    "lies",
    "list",
    "listen",
    "liste",
    "mkdir",
    "modifizieren",
    "ordner",
    "read",
    "replace",
    "schreibe",
    "schreiben",
    "speichere",
    "speichern",
    "tausche",
    "tauschen",
    "update",
    "verzeichnis",
    "write",
    "zeige",
    "ändern",
    "ändere",
    "ersetzen",
    "ersetze",
)

EXTENSION_FIXES: dict[str, str] = {
    ".htlm": ".html",
    ".htmll": ".html",
    ".htl": ".html",
    ".txtt": ".txt",
    ".pdff": ".pdf",
    ".docxx": ".docx",
    ".doc": ".docx",
    ".jss": ".js",
    ".csss": ".css",
}

KEYWORD_LOOKUP: set[str] = {keyword.lower() for keyword in CANONICAL_KEYWORDS}

# Unambiguous common typos (including letter swaps) checked before fuzzy matching.
EXPLICIT_KEYWORD_ALIASES: dict[str, str] = {
    "erstlle": "erstelle",
    "erstellle": "erstelle",
    "erstlelen": "erstellen",
    "lees": "lese",
    "lies": "lese",
    "bearbeitte": "bearbeite",
    "ordenr": "ordner",
    "ordnr": "ordner",
    "verzeichniss": "verzeichnis",
    "dateii": "datei",
    "indx": "index",
}


@dataclass
class PromptCorrection:
    """One spelling or token fix applied during prompt normalization."""

    original: str
    corrected: str
    reason: str = "keyword"


@dataclass
class PromptNormalizationResult:
    """Result of pre-processing a user prompt before orchestration."""

    original: str
    normalized: str
    corrections: list[PromptCorrection] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """
        Return True when at least one correction was applied.

        :return: Whether the normalized text differs from the original
        """
        return self.normalized != self.original


def _levenshtein(left: str, right: str) -> int:
    """
    Compute Levenshtein distance between two strings.

    :param left: First string
    :param right: Second string
    :return: Edit distance
    """
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def _has_adjacent_swap(left: str, right: str) -> bool:
    """
    Return True when two strings differ by exactly one adjacent letter swap.

    :param left: First string
    :param right: Second string
    :return: Whether the strings are transposition neighbors
    """
    if len(left) != len(right):
        return False
    diff_indexes = [index for index, (a, b) in enumerate(zip(left, right)) if a != b]
    if len(diff_indexes) != 2:
        return False
    first, second = diff_indexes
    return (
        second == first + 1
        and left[first] == right[second]
        and left[second] == right[first]
    )


def _keyword_distance(word: str, keyword: str) -> int:
    """
    Compute fuzzy distance between a token and a canonical keyword.

    :param word: User token
    :param keyword: Canonical keyword
    :return: Distance score
    """
    left = word.lower()
    right = keyword.lower()
    if left == right:
        return 0
    if _has_adjacent_swap(left, right):
        return 1
    return _levenshtein(left, right)


def _max_keyword_distance(word: str) -> int:
    """
    Return the allowed fuzzy distance for a token length.

    :param word: User token
    :return: Maximum allowed distance
    """
    length = len(word)
    if length <= 3:
        return 0
    if length <= 5:
        return 1
    return 2


def _preserve_case(original: str, corrected: str) -> str:
    """
    Apply the casing pattern of the original token to the corrected token.

    :param original: Original user token
    :param corrected: Lowercase corrected token
    :return: Corrected token with preserved casing
    """
    if original.isupper():
        return corrected.upper()
    if original[:1].isupper():
        return corrected[:1].upper() + corrected[1:]
    return corrected


def _should_skip_token(token: str) -> bool:
    """
    Return True when a token must not be spell-corrected.

    :param token: User token
    :return: Whether fuzzy correction should be skipped
    """
    if "/" in token or "\\" in token:
        return True
    if token.startswith(".") and token.count(".") > 1:
        return True
    if any(char.isdigit() for char in token) and "." in token:
        return True
    if token.lower() in KEYWORD_LOOKUP:
        return True
    if token.lower() in {
        "erstellten",
        "erstellte",
        "erstellter",
        "erstelltes",
        "h1-tag",
    }:
        return True
    return False


def _best_keyword_match(word: str) -> tuple[str, int] | None:
    """
    Find the closest canonical keyword for a user token.

    :param word: User token
    :return: Tuple of keyword and distance, or None
    """
    if _should_skip_token(word):
        return None

    alias = EXPLICIT_KEYWORD_ALIASES.get(word.lower())
    if alias and alias != word.lower():
        return alias, 0

    best_keyword: str | None = None
    best_distance: int | None = None
    for keyword in CANONICAL_KEYWORDS:
        distance = _keyword_distance(word, keyword)
        if distance == 0:
            return None
        if distance > _max_keyword_distance(word):
            continue
        if best_distance is None or distance < best_distance:
            best_keyword = keyword
            best_distance = distance
            continue
        if distance == best_distance and best_keyword != keyword:
            return None
    if best_keyword is None or best_distance is None:
        return None
    return best_keyword, best_distance


def _fix_extension_typos(text: str) -> tuple[str, list[PromptCorrection]]:
    """
    Fix common file extension typos inside tokens.

    :param text: Prompt text
    :return: Tuple of corrected text and applied fixes
    """
    corrections: list[PromptCorrection] = []
    updated = text
    for wrong, right in EXTENSION_FIXES.items():
        pattern = re.compile(re.escape(wrong) + r"\b", re.IGNORECASE)

        def replacer(match: re.Match[str]) -> str:
            original = match.group(0)
            corrected = original[: -len(wrong)] + right
            corrections.append(
                PromptCorrection(
                    original=original,
                    corrected=corrected,
                    reason="extension",
                )
            )
            return corrected

        updated = pattern.sub(replacer, updated)
    return updated, corrections


def normalize_user_prompt(user_content: str) -> PromptNormalizationResult:
    """
    Normalize intent-critical typos before workspace intent parsing.

    Only canonical keywords and obvious extension typos are corrected.
    Free-form content, paths, and entity names are left unchanged.

    :param user_content: Raw user message
    :return: Normalization result with optional corrections
    """
    original = user_content or ""
    if not original.strip():
        return PromptNormalizationResult(original=original, normalized=original)

    corrections: list[PromptCorrection] = []
    extension_fixed, extension_corrections = _fix_extension_typos(original)
    corrections.extend(extension_corrections)

    replacements: list[tuple[int, int, str, PromptCorrection]] = []
    for match in WORD_PATTERN.finditer(extension_fixed):
        token = match.group(0)
        match_result = _best_keyword_match(token)
        if match_result is None:
            continue
        keyword, _distance = match_result
        corrected = _preserve_case(token, keyword)
        if corrected == token:
            continue
        replacements.append(
            (
                match.start(),
                match.end(),
                corrected,
                PromptCorrection(original=token, corrected=corrected),
            )
        )

    if not replacements:
        normalized = extension_fixed
        return PromptNormalizationResult(
            original=original,
            normalized=normalized,
            corrections=corrections,
        )

    normalized_parts: list[str] = []
    cursor = 0
    for start, end, corrected, correction in replacements:
        normalized_parts.append(extension_fixed[cursor:start])
        normalized_parts.append(corrected)
        corrections.append(correction)
        cursor = end
    normalized_parts.append(extension_fixed[cursor:])
    normalized = "".join(normalized_parts)

    return PromptNormalizationResult(
        original=original,
        normalized=normalized,
        corrections=corrections,
    )


def prompt_normalization_metadata(result: PromptNormalizationResult) -> dict[str, object]:
    """
    Build chat message metadata for applied prompt corrections.

    :param result: Normalization result
    :return: Metadata dict or empty dict when unchanged
    """
    if not result.changed:
        return {}
    return {
        "prompt_corrections": [
            {
                "original": correction.original,
                "corrected": correction.corrected,
                "reason": correction.reason,
            }
            for correction in result.corrections
        ],
        "interpreted_request": result.normalized,
    }


def format_prompt_normalization_block(result: PromptNormalizationResult) -> str:
    """
    Format prompt corrections for orchestration transcripts.

    :param result: Normalization result
    :return: Human-readable block or empty string
    """
    if not result.changed:
        return ""
    lines = ["Prompt normalization (pre-processing):"]
    for correction in result.corrections:
        lines.append(f'- "{correction.original}" -> "{correction.corrected}" ({correction.reason})')
    return "\n".join(lines)
