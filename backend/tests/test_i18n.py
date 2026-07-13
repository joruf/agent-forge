"""Tests for i18n translation helper."""

from agentforge.i18n import current_locale, resolve_locale, t


def test_resolve_locale_defaults_to_en() -> None:
    """Unknown locale codes fall back to English."""
    assert resolve_locale(None) == "en"
    assert resolve_locale("fr") == "en"
    assert resolve_locale("de") == "de"


def test_translate_english(english_locale) -> None:
    """English catalog returns expected strings."""
    assert t("setup.backend_ok", locale="en") == "Backend is running"
    assert t("tasks.coding.label", locale="en") == "Coding / Development"


def test_translate_german(german_locale) -> None:
    """German catalog returns expected strings."""
    assert t("setup.backend_ok", locale="de") == "Backend läuft"
    assert "Entwickler" in t("roles.developer.name", locale="de")


def test_translate_with_placeholders(english_locale) -> None:
    """Placeholder tokens are replaced in translated strings."""
    text = t("setup.ollama_ok", locale="en", count=3, url="http://host:11434")
    assert "3 model(s)" in text
    assert "http://host:11434" in text


def test_current_locale_follows_settings(german_locale) -> None:
    """current_locale reads from runtime settings."""
    assert current_locale() == "de"
