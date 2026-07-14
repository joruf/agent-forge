"""Weather context via Open-Meteo."""

from __future__ import annotations

from typing import Any

from agentforge.config import settings
from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult, PluginTiming
from agentforge.context.http_utils import fetch_json
from agentforge.context.location import location_source_label, resolve_location

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "light snow",
    80: "rain showers",
    95: "thunderstorm",
}


def _weather_label(code: int | None) -> str:
    """
    Map Open-Meteo weather code to text.

    :param code: WMO weather code
    :return: Human-readable label
    """
    if code is None:
        return "unknown"
    return WEATHER_CODES.get(code, f"code {code}")


def _resolve_timezone(location_timezone: str | None) -> str:
    """
    Choose the timezone parameter for Open-Meteo.

    :param location_timezone: Timezone from geolocation when available
    :return: Timezone string for API query
    """
    if location_timezone:
        return location_timezone
    configured = settings.context_timezone.strip()
    return configured or "auto"


class WeatherContextPlugin(ContextPlugin):
    """Current weather and short forecast."""

    id = "weather"
    timing = PluginTiming.JIT
    trigger_keywords = (
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
    )

    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Fetch weather for configured, named, or IP-derived location.

        :param request: Context request payload
        :return: Weather context result
        """
        try:
            location = await resolve_location(request)
            if location is None:
                return ContextResult(
                    plugin_id=self.id,
                    ok=False,
                    text="",
                    error=(
                        "Weather plugin could not determine a location. "
                        "Set AGENTFORGE_CONTEXT_CITY, coordinates, or enable IP geolocation."
                    ),
                )

            payload = await fetch_json(
                FORECAST_URL,
                params={
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "current_weather": "true",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                    "timezone": _resolve_timezone(location.timezone),
                    "forecast_days": 2,
                },
            )
            current: dict[str, Any] = payload.get("current_weather") or {}
            daily = payload.get("daily") or {}
            code = current.get("weathercode")
            temp = current.get("temperature")
            wind = current.get("windspeed")
            tomorrow_max = (daily.get("temperature_2m_max") or [None, None])[1]
            tomorrow_min = (daily.get("temperature_2m_min") or [None, None])[1]
            source_label = location_source_label(location.source)
            text = (
                f"Weather ({location.label}; {source_label}): "
                f"now {temp}°C, {_weather_label(code)}, wind {wind} km/h.\n"
                f"Tomorrow: {tomorrow_min}°C to {tomorrow_max}°C."
            )
            return ContextResult(
                plugin_id=self.id,
                ok=True,
                text=text,
                data={
                    "location": location.label,
                    "location_source": location.source,
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "timezone": location.timezone,
                    "country_code": location.country_code,
                    "city": location.city,
                    "current": current,
                    "daily": daily,
                },
            )
        except Exception as exc:
            return ContextResult(
                plugin_id=self.id,
                ok=False,
                text="",
                error=str(exc),
            )
