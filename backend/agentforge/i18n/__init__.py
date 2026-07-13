"""Lightweight translation helper for backend user-facing strings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentforge.config import settings

SUPPORTED_LOCALES = ("en", "de")
DEFAULT_LOCALE = "en"

_catalogs: dict[str, dict[str, Any]] = {}


def _load_catalog(locale: str) -> dict[str, Any]:
    """Load locale JSON from disk once."""
    if locale not in _catalogs:
        path = Path(__file__).parent / f"{locale}.json"
        _catalogs[locale] = json.loads(path.read_text(encoding="utf-8"))
    return _catalogs[locale]


def resolve_locale(value: str | None) -> str:
    """Normalize locale code."""
    if value in SUPPORTED_LOCALES:
        return value
    return DEFAULT_LOCALE


def current_locale() -> str:
    """Return active UI locale from settings."""
    return resolve_locale(getattr(settings, "ui_language", DEFAULT_LOCALE))


def t(key: str, locale: str | None = None, **params: str | int) -> str:
    """
    Translate a dotted key from locale files.

    :param key: Dotted translation key
    :param locale: Optional locale override
    :param params: Placeholder values for {{name}} tokens
    :return: Translated string or key if missing
    """
    lang = resolve_locale(locale or current_locale())
    node: Any = _load_catalog(lang)
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            node = key
            break

    text = node if isinstance(node, str) else key
    for name, value in params.items():
        text = text.replace(f"{{{{{name}}}}}", str(value))
    return text
