"""Optional random fact plugin."""

from __future__ import annotations

from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult, PluginTiming
from agentforge.context.http_utils import fetch_json

API_URL = "https://uselessfacts.jsph.pl/api/v2/facts/random"


class RandomFactContextPlugin(ContextPlugin):
    """Fetch a random fact on explicit request."""

    id = "random_fact"
    timing = PluginTiming.JIT
    trigger_keywords = ("random fact", "zufallsfakt", "fun fact", "trivia")

    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Fetch one random fact.

        :param request: Context request payload
        :return: Random fact context result
        """
        try:
            payload = await fetch_json(API_URL, params={"language": "de" if "zufall" in request.user_content.lower() else "en"})
            fact = str(payload.get("text") or "").strip()
            if not fact:
                return ContextResult(plugin_id=self.id, ok=False, text="", error="Empty fact response.")
            return ContextResult(
                plugin_id=self.id,
                ok=True,
                text=f"Random fact: {fact}",
                data={"fact": fact},
            )
        except Exception as exc:
            return ContextResult(
                plugin_id=self.id,
                ok=False,
                text="",
                error=str(exc),
            )
