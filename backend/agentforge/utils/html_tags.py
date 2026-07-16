"""Shared HTML tag parsing for natural-language workspace prompts."""

from __future__ import annotations

import re

HTML_TAG_NAME = r"[a-z][a-z0-9]*(?:-[a-z][a-z0-9]+)*"

TAG_SUFFIX = r"(?:-\s*tags?\b|\s+tags?\b)"

VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

NATURAL_TAG_REFERENCE = re.compile(
    rf"\b({HTML_TAG_NAME}){TAG_SUFFIX}",
    re.IGNORECASE,
)

NATURAL_TAG_ALS = re.compile(
    rf"\bals\s+({HTML_TAG_NAME}){TAG_SUFFIX}",
    re.IGNORECASE,
)

CONTENT_FROM_TAG = re.compile(
    rf"(?:vom|from|des)\s+({HTML_TAG_NAME}){TAG_SUFFIX}?",
    re.IGNORECASE,
)

DERIVED_NAME_FROM_TAG = re.compile(
    rf"(?:namen|name)\s+(?:des\s+)?(?:inhalts?\s+)?(?:des\s+)?"
    rf"({HTML_TAG_NAME}){TAG_SUFFIX}",
    re.IGNORECASE,
)

DERIVED_FILE_WITH_TAG = re.compile(
    rf"(?:"
    rf"(?:neue\s+)?datei.*?(?:namen|name).*?(?:{HTML_TAG_NAME}|überschrift).*?(?:\.txt|dateiendung\s*\.txt)"
    rf"|"
    rf"(?:namen|name)\s+(?:des\s+)?(?:inhalts?\s+)?(?:des\s+)?"
    rf"(?:{HTML_TAG_NAME}|überschrift|heading){TAG_SUFFIX}.*?(?:\.txt|dateiendung)"
    rf"|"
    rf"(?:named|name(?:d)?\s+after).*?(?:{HTML_TAG_NAME}|heading).*?(?:\.txt|txt\s+file)"
    rf"|"
    rf"file.*?named.*?{HTML_TAG_NAME}.*?(?:\.txt|txt)"
    rf")",
    re.IGNORECASE,
)

TAG_INSERT_CLAUSE = re.compile(
    rf"(?:unter|under|below|nach|inside|in)\s+"
    rf"(?:dem|den|the)\s+({HTML_TAG_NAME}){TAG_SUFFIX}.*?"
    rf"(?:einen|ein|a|an)\s+({HTML_TAG_NAME}){TAG_SUFFIX}.*?"
    rf"(?:mit\s+der\s+beschriftung|beschriftung|labeled?|with\s+(?:the\s+)?text)\s+"
    rf'["\«„]([^"\»""]+)["\»""]',
    re.IGNORECASE | re.DOTALL,
)

TAG_LITERAL_IN_REQUEST = re.compile(
    rf'(?:text|inhalt)\s+["\«„]([^"\»""]+)["\»""]\s*(?:als\s+)?'
    rf"(?:{HTML_TAG_NAME}){TAG_SUFFIX}?",
    re.IGNORECASE,
)

HTML_TAG_IN_CLAUSE = re.compile(
    rf"\b({HTML_TAG_NAME}){TAG_SUFFIX}",
    re.IGNORECASE,
)


def normalize_tag_name(name: str | None) -> str | None:
    """
    Normalize a user-mentioned HTML tag name.

    :param name: Raw tag token from natural language
    :return: Lowercase tag name or None when invalid
    """
    if not name:
        return None
    cleaned = name.strip().lower().rstrip("-")
    if not cleaned or not re.fullmatch(HTML_TAG_NAME, cleaned, re.IGNORECASE):
        return None
    return cleaned


def is_valid_html_tag_name(name: str) -> bool:
    """
    Return True when the token is a valid HTML tag name.

    :param name: Candidate tag name
    :return: Whether the name matches HTML tag naming rules
    """
    return normalize_tag_name(name) is not None


def parse_tag_reference(text: str) -> str | None:
    """
    Parse the first HTML tag reference from natural language text.

    Matches forms such as ``h1-tag``, ``p Tag``, ``div-Tag``, ``img tag``.

    :param text: User clause or message fragment
    :return: Normalized tag name or None
    """
    for pattern in (NATURAL_TAG_ALS, NATURAL_TAG_REFERENCE, DERIVED_NAME_FROM_TAG, CONTENT_FROM_TAG):
        match = pattern.search(text or "")
        if match:
            tag = normalize_tag_name(match.group(1))
            if tag:
                return tag
    return None


def parse_all_tag_references(text: str) -> list[str]:
    """
    Parse all distinct HTML tag references from natural language text.

    :param text: User clause or message fragment
    :return: Ordered unique normalized tag names
    """
    seen: set[str] = set()
    tags: list[str] = []
    for match in HTML_TAG_IN_CLAUSE.finditer(text or ""):
        tag = normalize_tag_name(match.group(1))
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def clause_mentions_html_tag(text: str) -> bool:
    """
    Return True when a clause references any HTML tag.

    :param text: User clause or message fragment
    :return: Whether an HTML tag reference was found
    """
    return parse_tag_reference(text) is not None or bool(HTML_TAG_IN_CLAUSE.search(text or ""))


def extract_tag_text_from_html(html: str, tag_name: str) -> str | None:
    """
    Extract visible text from the first matching HTML element.

    :param html: HTML document body
    :param tag_name: Element tag name such as h1, p, or div
    :return: Plain text content or None
    """
    tag = normalize_tag_name(tag_name)
    if not tag:
        return None
    body = html or ""
    if tag in VOID_HTML_TAGS:
        alt_match = re.search(
            rf"<{tag}\b[^>]*\balt=[\"']([^\"']+)[\"']",
            body,
            re.IGNORECASE,
        )
        if alt_match:
            text = alt_match.group(1).strip()
            return text or None
        title_match = re.search(
            rf"<{tag}\b[^>]*\btitle=[\"']([^\"']+)[\"']",
            body,
            re.IGNORECASE,
        )
        if title_match:
            text = title_match.group(1).strip()
            return text or None
        return None
    pattern = re.compile(
        rf"<{re.escape(tag)}\b[^>]*>(.*?)</{re.escape(tag)}>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(body)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", "", match.group(1))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def extract_tag_insert_from_clause(clause_text: str) -> tuple[str, str, str] | None:
    """
    Extract an HTML element insertion request from one clause.

    :param clause_text: Prompt clause text
    :return: Tuple of (after_tag, insert_tag, text) or None
    """
    match = TAG_INSERT_CLAUSE.search(clause_text or "")
    if not match:
        return None
    after_tag = normalize_tag_name(match.group(1))
    insert_tag = normalize_tag_name(match.group(2))
    text = match.group(3).strip()
    if not after_tag or not insert_tag or not text:
        return None
    return after_tag, insert_tag, text


def insert_tag_after_tag(html: str, after_tag: str, insert_tag: str, text: str) -> str:
    """
    Insert a new HTML element immediately after an existing element.

    :param html: HTML document body
    :param after_tag: Existing element tag name to insert after
    :param insert_tag: New element tag name to insert
    :param text: Visible element text (ignored for void tags)
    :return: Updated HTML document
    """
    anchor = normalize_tag_name(after_tag)
    new_tag = normalize_tag_name(insert_tag)
    if not anchor or not new_tag:
        return html
    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if anchor in VOID_HTML_TAGS:
        pattern = re.compile(rf"<{re.escape(anchor)}\b[^>]*/?>", re.IGNORECASE)
    else:
        pattern = re.compile(
            rf"<{re.escape(anchor)}\b[^>]*>.*?</{re.escape(anchor)}>",
            re.IGNORECASE | re.DOTALL,
        )
    match = pattern.search(html or "")
    if not match:
        return html
    if new_tag in VOID_HTML_TAGS:
        insertion = f'\n  <{new_tag} alt="{safe_text}" />'
    else:
        insertion = f"\n  <{new_tag}>{safe_text}</{new_tag}>"
    end = match.end()
    return html[:end] + insertion + html[end:]
