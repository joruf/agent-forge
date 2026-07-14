"""Tests for context plugins."""

from __future__ import annotations

import pytest

from agentforge.config import settings
from agentforge.context.base import ContextRequest
from agentforge.context.intent import detect_required_plugins
from agentforge.context.plugins.datetime_plugin import DateTimeContextPlugin
from agentforge.context.plugins.exchange_rates_plugin import ExchangeRatesContextPlugin
from agentforge.context.plugins.weather_plugin import WeatherContextPlugin
from agentforge.context.registry import build_default_registry


@pytest.mark.asyncio
async def test_datetime_plugin_returns_current_date(english_locale) -> None:
    """DateTime plugin returns an ISO date block."""
    plugin = DateTimeContextPlugin()
    result = await plugin.resolve(ContextRequest())
    assert result.ok is True
    assert "Current date/time:" in result.text
    assert result.data.get("date")


@pytest.mark.asyncio
async def test_exchange_rates_plugin(monkeypatch, english_locale) -> None:
    """Exchange rates plugin formats Frankfurter payload."""

    async def fake_fetch(url: str, *, params=None):
        return {"date": "2026-07-14", "base": "EUR", "rates": {"USD": 1.17, "GBP": 0.86}}

    monkeypatch.setattr(
        "agentforge.context.plugins.exchange_rates_plugin.fetch_json",
        fake_fetch,
    )
    result = await ExchangeRatesContextPlugin().resolve(ContextRequest())
    assert result.ok is True
    assert "USD=1.17" in result.text


def test_detect_required_plugins_empty_for_generic_prompt() -> None:
    """Generic prompts do not require any plugins."""
    assert detect_required_plugins("Erstelle mir eine Python-Datei") == []
    assert detect_required_plugins('Write "Hello World" to a file') == []


def test_detect_required_plugins_datetime_on_schedule_words() -> None:
    """Scheduling words require the datetime plugin."""
    required = detect_required_plugins("Plane die Aufgaben für nächste Woche")
    assert required == ["datetime"]


def test_detect_required_plugins_weather_intent() -> None:
    """Weather prompts require the weather plugin."""
    required = detect_required_plugins("Wie ist das Wetter in Berlin?")
    assert required == ["weather"]


def test_detect_required_plugins_ignores_hier_in_read_request() -> None:
    """Standalone 'hier' in file-read prompts must not trigger weather."""
    prompt = (
        "lese den dateiinhalt von /home/joruf/Dokumente/GitHub/Test12/test.txt "
        "und liste mir den inhalt hier auf"
    )
    assert detect_required_plugins(prompt) == []


def test_detect_required_plugins_skips_for_workspace_tasks() -> None:
    """Workspace tasks suppress ambient plugins entirely."""
    assert detect_required_plugins(
        "Wie ist das Wetter heute?",
        workspace_task_active=True,
    ) == []


@pytest.mark.asyncio
async def test_registry_startup_loads_no_plugins(monkeypatch, english_locale) -> None:
    """Startup collection does not preload plugins anymore."""
    registry = build_default_registry()
    monkeypatch.setattr(settings, "context_plugins_enabled", True)
    monkeypatch.setattr(
        settings,
        "context_plugins_enabled_list",
        ["datetime", "weather", "exchange_rates", "random_fact"],
    )

    payload = await registry.build_startup(force_refresh=True)
    assert payload["required_plugins"] == []
    assert payload["results"] == []


@pytest.mark.asyncio
async def test_registry_message_loads_detected_plugins_only(monkeypatch, english_locale) -> None:
    """Chat messages load only intent-matched plugins."""
    from agentforge.context.base import ContextResult

    registry = build_default_registry()
    monkeypatch.setattr(settings, "context_plugins_enabled", True)
    monkeypatch.setattr(settings, "context_plugins_enabled_list", ["datetime", "weather"])

    async def fake_weather(self, request):
        return ContextResult(plugin_id="weather", ok=True, text="Weather ok")

    monkeypatch.setattr(WeatherContextPlugin, "resolve", fake_weather)

    generic = await registry.build_for_message('Write "Hello World" to a file')
    assert generic == ""

    weather = await registry.build_for_message("Wie ist das Wetter heute?")
    assert "Weather ok" in weather


@pytest.mark.asyncio
async def test_build_for_message_emits_plugin_events(monkeypatch, english_locale) -> None:
    """Plugin lifecycle events are emitted during message context building."""
    from agentforge.context.base import ContextResult

    registry = build_default_registry()
    monkeypatch.setattr(settings, "context_plugins_enabled", True)
    monkeypatch.setattr(settings, "context_plugins_enabled_list", ["datetime", "weather"])
    events: list[dict[str, object]] = []

    async def capture(event: dict[str, object]) -> None:
        events.append(event)

    async def fake_weather(self, request):
        return ContextResult(plugin_id="weather", ok=True, text="Weather ok")

    monkeypatch.setattr(
        "agentforge.context.plugins.weather_plugin.WeatherContextPlugin.resolve",
        fake_weather,
    )

    await registry.build_for_message("Wie ist das Wetter?", on_event=capture)
    types = [event["type"] for event in events]
    assert "context_plugins_started" in types
    assert "context_plugin_start" in types
    assert "context_plugin_complete" in types
    assert "context_plugins_done" in types
