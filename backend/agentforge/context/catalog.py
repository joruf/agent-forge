"""Catalog of free context plugins and their upstream APIs."""

from __future__ import annotations

from agentforge.context.base import PluginCatalogEntry, PluginTiming

PLUGIN_CATALOG: tuple[PluginCatalogEntry, ...] = (
    PluginCatalogEntry(
        id="datetime",
        name="Date & Time",
        description="Current local date, time, weekday, ISO week, and timezone.",
        timing=PluginTiming.JIT,
        api_name="System clock",
        api_url="local",
        api_key_required=False,
        license_note="No external service.",
        trigger_keywords=(
            "date",
            "time",
            "today",
            "heute",
            "tomorrow",
            "morgen",
            "deadline",
            "schedule",
            "calendar",
            "week",
            "month",
            "year",
            "datum",
            "uhrzeit",
            "termin",
            "frist",
        ),
    ),
    PluginCatalogEntry(
        id="weather",
        name="Weather",
        description=(
            "Current weather and short forecast via Open-Meteo. Uses a named city, "
            "configured defaults, or IP geolocation for local requests."
        ),
        timing=PluginTiming.JIT,
        api_name="Open-Meteo + ipwho.is",
        api_url="https://api.open-meteo.com/v1/forecast",
        api_key_required=False,
        license_note=(
            "Open-Meteo free tier (CC BY 4.0). IP geolocation via ipwho.is without API key."
        ),
        trigger_keywords=(
            "weather",
            "wetter",
            "forecast",
            "vorhersage",
            "temperature",
            "temperatur",
            "rain",
            "regen",
            "snow",
            "schnee",
            "wind",
            "hier",
            "here",
            "bei mir",
            "near me",
            "my location",
        ),
        docs_url="https://open-meteo.com/en/docs",
    ),
    PluginCatalogEntry(
        id="holidays",
        name="Public Holidays",
        description="Public holidays for the configured country via Nager.Date.",
        timing=PluginTiming.JIT,
        api_name="Nager.Date",
        api_url="https://date.nager.at/api/v4/PublicHolidays/{year}/{country}",
        api_key_required=False,
        license_note="Open-source project, CORS enabled, no API key.",
        trigger_keywords=(
            "holiday",
            "holidays",
            "feiertag",
            "feiertage",
            "public holiday",
            "urlaub",
            "frei",
        ),
        docs_url="https://date.nager.at/Api",
    ),
    PluginCatalogEntry(
        id="exchange_rates",
        name="Exchange Rates",
        description="Latest ECB exchange rates via Frankfurter.",
        timing=PluginTiming.JIT,
        api_name="Frankfurter",
        api_url="https://api.frankfurter.dev/v1/latest",
        api_key_required=False,
        license_note="Open-source ECB data, no API key.",
        trigger_keywords=(
            "exchange rate",
            "wechselkurs",
            "currency",
            "währung",
            "usd",
            "eur",
            "dollar",
            "euro",
            "forex",
        ),
        docs_url="https://www.frankfurter.app/docs/",
    ),
    PluginCatalogEntry(
        id="country_facts",
        name="Country Facts",
        description="Basic country facts via REST Countries when a country is mentioned.",
        timing=PluginTiming.JIT,
        api_name="REST Countries",
        api_url="https://restcountries.com/v3.1/name/{name}",
        api_key_required=False,
        license_note="Free, no API key.",
        trigger_keywords=(
            "country",
            "land",
            "capital",
            "hauptstadt",
            "population",
            "bevölkerung",
            "timezone",
            "zeitzone",
        ),
        docs_url="https://restcountries.com/",
    ),
    PluginCatalogEntry(
        id="sun_times",
        name="Sunrise & Sunset",
        description="Sunrise, sunset, and daylight length via Open-Meteo astronomy.",
        timing=PluginTiming.JIT,
        api_name="Open-Meteo",
        api_url="https://api.open-meteo.com/v1/forecast",
        api_key_required=False,
        license_note="Same Open-Meteo terms as weather.",
        trigger_keywords=(
            "sunrise",
            "sunset",
            "sonnenaufgang",
            "sonnenuntergang",
            "daylight",
            "tageslicht",
            "golden hour",
        ),
        docs_url="https://open-meteo.com/en/docs",
    ),
    PluginCatalogEntry(
        id="random_fact",
        name="Random Fact",
        description="Lightweight random fact via uselessfacts.jsph.pl (JIT only).",
        timing=PluginTiming.JIT,
        api_name="Useless Facts",
        api_url="https://uselessfacts.jsph.pl/api/v2/facts/random",
        api_key_required=False,
        license_note="Free JSON API, no key.",
        trigger_keywords=("random fact", "zufallsfakt", "fun fact", "trivia"),
        docs_url="https://uselessfacts.jsph.pl/",
    ),
)


def plugin_display_name(plugin_id: str) -> str:
    """
    Return a human-readable plugin name for UI events.

    :param plugin_id: Plugin identifier
    :return: Display name
    """
    for entry in PLUGIN_CATALOG:
        if entry.id == plugin_id:
            return entry.name
    return plugin_id


def catalog_as_dict() -> list[dict[str, object]]:
    """
    Return catalog entries as JSON-serializable dicts.

    :return: List of plugin metadata dicts
    """
    return [
        {
            "id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "timing": entry.timing.value,
            "api_name": entry.api_name,
            "api_url": entry.api_url,
            "api_key_required": entry.api_key_required,
            "license_note": entry.license_note,
            "trigger_keywords": list(entry.trigger_keywords),
            "docs_url": entry.docs_url,
        }
        for entry in PLUGIN_CATALOG
    ]
