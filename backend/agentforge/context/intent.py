"""Detect which context plugins are required for a user message."""

from __future__ import annotations

import re

from agentforge.context.catalog import PLUGIN_CATALOG

PLUGIN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "datetime": (
        re.compile(r"\b(heute|today|morgen|tomorrow|übermorgen|yesterday|gestern)\b", re.I),
        re.compile(r"\b(datum|date|uhrzeit|time|timezone|zeitzone|kalender|calendar)\b", re.I),
        re.compile(r"\b(wochentag|weekday|woche|week|monat|month|jahr|year)\b", re.I),
        re.compile(r"\b(deadline|frist|termin|schedule|planung|planning|dauer|duration)\b", re.I),
        re.compile(r"\b(nächste(?:n|r|s)?|next|letzte(?:n|r|s)?|last|this)\s+(woche|week|monat|month|jahr|year)\b", re.I),
        re.compile(r"\b(in|within)\s+\d+\s+(tag|tage|days|woche|wochen|weeks|monat|monate|months)\b", re.I),
        re.compile(r"\b(\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?|\d{4}-\d{2}-\d{2})\b"),
    ),
    "weather": (
        re.compile(r"\b(wetter|weather|forecast|vorhersage|temperatur|temperature)\b", re.I),
        re.compile(r"\b(regen|rain|schnee|snow|wind|bewölkt|cloudy)\b", re.I),
        re.compile(r"\b(wie warm|how hot|how cold|wird es regnen)\b", re.I),
        re.compile(r"\b(hier|here|bei mir|near me|my location|wo ich bin|in der nähe)\b", re.I),
    ),
    "holidays": (
        re.compile(r"\b(feiertag|feiertage|holiday|holidays|public holiday)\b", re.I),
        re.compile(r"\b(arbeit(?:s)?frei|freier tag|bank holiday)\b", re.I),
        re.compile(r"\b(nächster feiertag|next holiday)\b", re.I),
    ),
    "exchange_rates": (
        re.compile(r"\b(wechselkurs|exchange rate|forex|währung|currency)\b", re.I),
        re.compile(r"\b(usd|eur|dollar|euro|gbp|chf|pfund|franken)\b", re.I),
        re.compile(r"\b(umrechnen|convert(?:ing)?)\b.*\b(usd|eur|dollar|euro)\b", re.I),
    ),
    "sun_times": (
        re.compile(r"\b(sonnenaufgang|sonnenuntergang|sunrise|sunset)\b", re.I),
        re.compile(r"\b(tageslicht|daylight|golden hour|blue hour)\b", re.I),
    ),
    "country_facts": (
        re.compile(r"\b(hauptstadt|capital)\b", re.I),
        re.compile(r"\b(bevölkerung|population)\b", re.I),
        re.compile(r"\b(country|land)\s+[a-zäöüß]{3,}", re.I),
        re.compile(
            r"\b(deutschland|germany|österreich|austria|schweiz|switzerland|frankreich|france|usa|england)\b",
            re.I,
        ),
    ),
    "random_fact": (
        re.compile(r"\b(random fact|zufallsfakt|fun fact|trivia)\b", re.I),
    ),
}


def detect_required_plugins(
    user_content: str,
    *,
    process_context: str = "",
) -> list[str]:
    """
    Decide which context plugins should load for the given text.

    :param user_content: User message text
    :param process_context: Optional extra text from orchestration context
    :return: Ordered list of plugin identifiers to resolve
    """
    text = "\n".join(part.strip() for part in (user_content, process_context) if part and part.strip())
    if not text:
        return []

    required: list[str] = []
    catalog_by_id = {entry.id: entry for entry in PLUGIN_CATALOG}

    for plugin_id, patterns in PLUGIN_PATTERNS.items():
        if any(pattern.search(text) for pattern in patterns):
            required.append(plugin_id)
            continue
        entry = catalog_by_id.get(plugin_id)
        if entry and any(_keyword_matches(text, keyword) for keyword in entry.trigger_keywords):
            required.append(plugin_id)

    return _unique(required)


def _keyword_matches(text: str, keyword: str) -> bool:
    """
    Match trigger keywords without substring false positives.

    Multi-word phrases use a simple case-insensitive substring check. Single words
    require word boundaries so terms like ``date`` do not match ``Datei``.

    :param text: Source text
    :param keyword: Trigger keyword from catalog
    :return: Whether the keyword matches
    """
    normalized = keyword.strip().lower()
    if not normalized:
        return False
    if " " in normalized:
        return normalized in text.lower()
    pattern = re.compile(rf"\b{re.escape(normalized)}\b", re.I)
    return pattern.search(text) is not None


def explain_required_plugins(
    user_content: str,
    *,
    process_context: str = "",
) -> list[dict[str, str]]:
    """
    Return human-readable reasons why plugins were selected.

    :param user_content: User message text
    :param process_context: Optional extra text from orchestration context
    :return: List of plugin id and reason pairs
    """
    required = detect_required_plugins(user_content, process_context=process_context)
    reasons: list[dict[str, str]] = []
    user_matches = set(detect_required_plugins(user_content))
    for plugin_id in required:
        if plugin_id in user_matches:
            reasons.append({"plugin_id": plugin_id, "reason": "matched_user_intent"})
        else:
            reasons.append({"plugin_id": plugin_id, "reason": "matched_process_context"})
    return reasons


def _unique(values: list[str]) -> list[str]:
    """
    Preserve order while removing duplicates.

    :param values: Input values
    :return: Unique values
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
