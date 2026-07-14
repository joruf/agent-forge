"""Current date and time context."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agentforge.config import settings
from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult, PluginTiming


class DateTimeContextPlugin(ContextPlugin):
    """Inject current local date and time."""

    id = "datetime"
    timing = PluginTiming.JIT
    trigger_keywords = (
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
    )

    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Build a human-readable date/time block.

        :param request: Context request payload
        :return: Context result with current timestamp
        """
        timezone_name = settings.context_timezone.strip() or "local"
        try:
            tz = ZoneInfo(timezone_name) if timezone_name not in ("local", "") else datetime.now().astimezone().tzinfo
            now = datetime.now(tz) if tz is not None else datetime.now().astimezone()
        except Exception:
            now = datetime.now().astimezone()
            timezone_name = str(now.tzinfo or "local")

        weekday_names = {
            "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        }
        locale = "de" if settings.ui_language.lower().startswith("de") else "en"
        weekday = weekday_names[locale][now.weekday()]
        text = (
            f"Current date/time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({weekday}, ISO week {now.isocalendar().week})\n"
            f"Timezone: {timezone_name}\n"
            f"Use this as authoritative 'today' when scheduling, deadlines, or relative dates are involved."
        )
        return ContextResult(
            plugin_id=self.id,
            ok=True,
            text=text,
            data={
                "iso": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": weekday,
                "timezone": timezone_name,
            },
        )
