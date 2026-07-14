"""Shared HTTP helpers for context plugins."""

from __future__ import annotations

from typing import Any

import httpx

from agentforge.config import settings

USER_AGENT = "AgentForge/0.1 (+context plugins)"


async def fetch_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    """
    Perform a short JSON GET request for context plugins.

    :param url: Request URL
    :param params: Optional query parameters
    :return: Parsed JSON payload
    """
    timeout = max(3.0, float(settings.context_plugin_timeout))
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()
