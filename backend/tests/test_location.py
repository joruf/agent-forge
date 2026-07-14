"""Tests for location resolution used by weather plugins."""

from __future__ import annotations

import pytest

from agentforge.config import settings
from agentforge.context.base import ContextRequest
from agentforge.context.location import (
    extract_city_from_text,
    geolocate_from_ip,
    resolve_location,
    should_prefer_ip_location,
    wants_local_location,
)
from agentforge.context.plugins.weather_plugin import WeatherContextPlugin


def test_extract_city_from_weather_message() -> None:
    """Extract explicit city names from weather prompts."""
    assert extract_city_from_text("Wie ist das Wetter in Berlin?") == "Berlin"
    assert extract_city_from_text("Weather forecast for Munich today") == "Munich"


def test_wants_local_location_keywords() -> None:
    """Detect local weather intent."""
    assert wants_local_location("Wie ist das Wetter hier?") is True
    assert wants_local_location("Weather near me") is True


def test_should_prefer_ip_for_generic_weather() -> None:
    """Generic weather prompts should prefer IP geolocation over defaults."""
    assert should_prefer_ip_location("Wie ist das Wetter heute?") is True
    assert should_prefer_ip_location("Wie ist das Wetter in Hamburg?") is False


@pytest.mark.asyncio
async def test_geolocate_from_ip_private_client_uses_local_lookup(monkeypatch) -> None:
    """Loopback client IPs trigger a local public-IP lookup."""

    async def fake_fetch(url: str, *, params=None):
        assert url == "https://ipwho.is"
        return {
            "success": True,
            "city": "Leipzig",
            "region": "Saxony",
            "country": "Germany",
            "country_code": "DE",
            "latitude": 51.34,
            "longitude": 12.37,
            "timezone": {"id": "Europe/Berlin"},
        }

    monkeypatch.setattr("agentforge.context.location.fetch_json", fake_fetch)
    location = await geolocate_from_ip("127.0.0.1")
    assert location is not None
    assert location.source == "ip_geolocation"
    assert location.city == "Leipzig"
    assert location.timezone == "Europe/Berlin"


@pytest.mark.asyncio
async def test_resolve_location_prefers_ip_over_config_city(monkeypatch, english_locale) -> None:
    """Generic weather requests use IP geolocation before configured city defaults."""
    monkeypatch.setattr(settings, "context_city", "Berlin")
    monkeypatch.setattr(settings, "context_latitude", None)
    monkeypatch.setattr(settings, "context_longitude", None)
    monkeypatch.setattr(settings, "context_ip_geolocation_enabled", True)

    async def fake_geolocate(client_ip: str = ""):
        return type(
            "ResolvedLocation",
            (),
            {
                "latitude": 51.34,
                "longitude": 12.37,
                "label": "Leipzig, DE",
                "source": "ip_geolocation",
                "timezone": "Europe/Berlin",
                "country_code": "DE",
                "city": "Leipzig",
            },
        )()

    monkeypatch.setattr("agentforge.context.location.geolocate_from_ip", fake_geolocate)

    location = await resolve_location(ContextRequest(user_content="Wie ist das Wetter heute?"))
    assert location is not None
    assert location.source == "ip_geolocation"
    assert location.city == "Leipzig"


@pytest.mark.asyncio
async def test_weather_plugin_uses_ip_location_in_text(monkeypatch, english_locale) -> None:
    """Weather plugin reports IP-based location in its context text."""

    async def fake_resolve_location(request: ContextRequest):
        return type(
            "ResolvedLocation",
            (),
            {
                "latitude": 51.34,
                "longitude": 12.37,
                "label": "Leipzig, DE",
                "source": "ip_geolocation",
                "timezone": "Europe/Berlin",
                "country_code": "DE",
                "city": "Leipzig",
            },
        )()

    async def fake_fetch(url: str, *, params=None):
        return {
            "current_weather": {
                "temperature": 18,
                "weathercode": 2,
                "windspeed": 12,
            },
            "daily": {
                "temperature_2m_max": [18, 20],
                "temperature_2m_min": [10, 11],
            },
        }

    monkeypatch.setattr("agentforge.context.plugins.weather_plugin.resolve_location", fake_resolve_location)
    monkeypatch.setattr("agentforge.context.plugins.weather_plugin.fetch_json", fake_fetch)

    result = await WeatherContextPlugin().resolve(
        ContextRequest(user_content="Wie ist das Wetter hier?", client_ip="127.0.0.1")
    )
    assert result.ok is True
    assert "Leipzig, DE" in result.text
    assert "IP geolocation" in result.text
    assert result.data.get("location_source") == "ip_geolocation"
