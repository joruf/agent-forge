"""Sunrise and sunset context via Open-Meteo."""

from __future__ import annotations

from agentforge.config import settings
from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult, PluginTiming
from agentforge.context.http_utils import fetch_json
from agentforge.context.location import resolve_location

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class SunTimesContextPlugin(ContextPlugin):
    """Sunrise, sunset, and daylight length."""

    id = "sun_times"
    timing = PluginTiming.JIT
    trigger_keywords = (
        "sunrise",
        "sunset",
        "sonnenaufgang",
        "sonnenuntergang",
        "daylight",
        "tageslicht",
        "golden hour",
    )

    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Fetch daily sun times for configured location.

        :param request: Context request payload
        :return: Sun times context result
        """
        try:
            location = await resolve_location(request)
            if location is None:
                return ContextResult(
                    plugin_id=self.id,
                    ok=False,
                    text="",
                    error="Sun times plugin could not determine a location.",
                )
            lat, lon, label = location.latitude, location.longitude, location.label
            payload = await fetch_json(
                FORECAST_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "sunrise,sunset,daylight_duration",
                    "timezone": location.timezone or settings.context_timezone.strip() or "auto",
                    "forecast_days": 1,
                },
            )
            daily = payload.get("daily") or {}
            sunrise = (daily.get("sunrise") or [None])[0]
            sunset = (daily.get("sunset") or [None])[0]
            duration = (daily.get("daylight_duration") or [None])[0]
            hours = round(duration / 3600, 1) if isinstance(duration, (int, float)) else None
            text = f"Sun times ({label}): sunrise {sunrise}, sunset {sunset}"
            if hours is not None:
                text += f", daylight ~{hours} h"
            text += "."
            return ContextResult(
                plugin_id=self.id,
                ok=True,
                text=text,
                data={"location": label, "sunrise": sunrise, "sunset": sunset, "daylight_hours": hours},
            )
        except Exception as exc:
            return ContextResult(
                plugin_id=self.id,
                ok=False,
                text="",
                error=str(exc),
            )
