"""Country facts via REST Countries."""

from __future__ import annotations

import re

from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult, PluginTiming
from agentforge.context.http_utils import fetch_json

API_URL = "https://restcountries.com/v3.1/name/{query}"
COUNTRY_HINTS = {
    "germany": "germany",
    "deutschland": "germany",
    "austria": "austria",
    "österreich": "austria",
    "switzerland": "switzerland",
    "schweiz": "switzerland",
    "france": "france",
    "frankreich": "france",
    "usa": "united states",
    "united states": "united states",
    "uk": "united kingdom",
    "england": "united kingdom",
}


def _extract_country_query(content: str) -> str | None:
    """
    Extract a likely country name from user content.

    :param content: User message
    :return: REST Countries query or None
    """
    lowered = content.lower()
    for hint, query in COUNTRY_HINTS.items():
        if re.search(rf"\b{re.escape(hint)}\b", lowered):
            return query
    match = re.search(r"\b(?:country|land)\s+([a-zäöüß\- ]{3,40})", lowered)
    if match:
        return match.group(1).strip()
    return None


class CountryFactsContextPlugin(ContextPlugin):
    """Basic country facts when a country is mentioned."""

    id = "country_facts"
    timing = PluginTiming.JIT
    trigger_keywords = (
        "country",
        "land",
        "capital",
        "hauptstadt",
        "population",
        "bevölkerung",
        "timezone",
        "zeitzone",
    )

    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Fetch country facts for a detected country mention.

        :param request: Context request payload
        :return: Country facts context result
        """
        query = _extract_country_query(request.user_content)
        if not query:
            return ContextResult(
                plugin_id=self.id,
                ok=False,
                text="",
                error="No country mention detected.",
            )
        try:
            payload = await fetch_json(API_URL.format(query=query), params={"fields": "name,capital,population,timezones,region,subregion,languages,currencies"})
            if not payload:
                return ContextResult(
                    plugin_id=self.id,
                    ok=False,
                    text="",
                    error=f"No country data for '{query}'.",
                )
            item = payload[0]
            name = item.get("name", {}).get("common") or query.title()
            capital = ", ".join(item.get("capital") or [])
            population = item.get("population")
            region = item.get("region")
            subregion = item.get("subregion")
            timezones = ", ".join(item.get("timezones") or [])[:120]
            text = (
                f"Country facts ({name}): capital {capital or 'n/a'}, population {population:,}, "
                f"region {region}/{subregion}, timezones {timezones or 'n/a'}."
            )
            return ContextResult(
                plugin_id=self.id,
                ok=True,
                text=text,
                data={"query": query, "country": item},
            )
        except Exception as exc:
            return ContextResult(
                plugin_id=self.id,
                ok=False,
                text="",
                error=str(exc),
            )
