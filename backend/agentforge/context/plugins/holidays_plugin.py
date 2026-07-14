"""Public holiday context via Nager.Date."""

from __future__ import annotations

from datetime import date, datetime

from agentforge.config import settings
from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult, PluginTiming
from agentforge.context.http_utils import fetch_json

API_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"


class HolidaysContextPlugin(ContextPlugin):
    """Upcoming public holidays for configured country."""

    id = "holidays"
    timing = PluginTiming.JIT
    trigger_keywords = (
        "holiday",
        "holidays",
        "feiertag",
        "feiertage",
        "public holiday",
        "urlaub",
        "frei",
    )

    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Fetch holidays for the current year and summarize nearby dates.

        :param request: Context request payload
        :return: Holiday context result
        """
        country = settings.context_country_code.strip().upper() or "DE"
        year = date.today().year
        try:
            payload = await fetch_json(API_URL.format(year=year, country=country))
            today = date.today()
            upcoming = []
            for item in payload:
                holiday_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
                if holiday_date >= today:
                    upcoming.append(f"{item['date']}: {item.get('localName') or item.get('name')}")
                if len(upcoming) >= 4:
                    break
            if not upcoming:
                text = f"No upcoming public holidays found for {country} in {year}."
            else:
                text = f"Upcoming public holidays ({country}): " + "; ".join(upcoming)
            return ContextResult(
                plugin_id=self.id,
                ok=True,
                text=text,
                data={"country": country, "year": year, "upcoming": upcoming},
            )
        except Exception as exc:
            return ContextResult(
                plugin_id=self.id,
                ok=False,
                text="",
                error=str(exc),
            )
