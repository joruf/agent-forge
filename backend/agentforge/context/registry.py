"""Registry and builder for ambient context plugins."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from agentforge.config import settings
from agentforge.context.base import ContextPlugin, ContextRequest, ContextResult
from agentforge.context.catalog import plugin_display_name
from agentforge.context.intent import detect_required_plugins, explain_required_plugins
from agentforge.context.plugins.country_facts_plugin import CountryFactsContextPlugin
from agentforge.context.plugins.datetime_plugin import DateTimeContextPlugin
from agentforge.context.plugins.exchange_rates_plugin import ExchangeRatesContextPlugin
from agentforge.context.plugins.holidays_plugin import HolidaysContextPlugin
from agentforge.context.plugins.random_fact_plugin import RandomFactContextPlugin
from agentforge.context.plugins.sun_times_plugin import SunTimesContextPlugin
from agentforge.context.plugins.weather_plugin import WeatherContextPlugin

ContextEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ContextRegistry:
    """Loads, caches, and resolves context plugins."""

    def __init__(self) -> None:
        """Initialize plugin registry."""
        self._plugins: dict[str, ContextPlugin] = {}
        self._startup_cache: dict[str, Any] | None = None
        self._startup_cache_at: float = 0.0

    def register(self, plugin: ContextPlugin) -> None:
        """
        Register a context plugin.

        :param plugin: Plugin instance
        """
        self._plugins[plugin.id] = plugin

    def list_plugins(self) -> list[str]:
        """
        Return registered plugin identifiers.

        :return: Plugin ids
        """
        return list(self._plugins.keys())

    def get_plugin(self, plugin_id: str) -> ContextPlugin | None:
        """
        Return a plugin by id.

        :param plugin_id: Plugin identifier
        :return: Plugin instance or None
        """
        return self._plugins.get(plugin_id)

    def _enabled_plugin_ids(self) -> set[str]:
        """
        Return enabled plugin identifiers from settings.

        :return: Enabled plugin ids
        """
        if not settings.context_plugins_enabled:
            return set()
        return set(settings.context_plugins_enabled_list)

    async def resolve_plugin(self, plugin_id: str, request: ContextRequest | None = None) -> ContextResult:
        """
        Resolve one plugin on demand.

        :param plugin_id: Plugin identifier
        :param request: Optional request payload
        :return: Plugin result
        """
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return ContextResult(plugin_id=plugin_id, ok=False, text="", error="Unknown plugin.")
        payload = request or ContextRequest(force=True)
        return await plugin.resolve(payload)

    async def build_startup(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """
        Build startup context snapshot with short-lived cache.

        No plugins are loaded at startup anymore; this endpoint returns metadata only.

        :param force_refresh: Ignore cached startup snapshot
        :return: Startup context payload
        """
        ttl = max(30.0, float(settings.context_startup_cache_seconds))
        now = time.monotonic()
        if (
            not force_refresh
            and self._startup_cache is not None
            and now - self._startup_cache_at < ttl
        ):
            cached = dict(self._startup_cache)
            cached["cached"] = True
            return cached

        payload = self._serialize([], mode="startup", required_plugins=[])
        self._startup_cache = payload
        self._startup_cache_at = now
        return payload

    async def build_for_message(
        self,
        user_content: str,
        chat_id: str = "",
        on_event: ContextEventCallback | None = None,
        process_context: str = "",
        client_ip: str = "",
        workspace_task_active: bool = False,
    ) -> str:
        """
        Build ambient context for a chat message.

        Datetime and other plugins load only when intent detection decides they
        are required for the current user message or orchestration context.

        :param user_content: Current user message
        :param chat_id: Chat identifier
        :param on_event: Optional callback for plugin lifecycle events
        :param process_context: Optional orchestration text used for detection
        :param client_ip: Optional client IP for location-aware plugins
        :param workspace_task_active: When True, skip plugins for workspace file tasks
        :return: Combined context text for system prompt injection
        """
        payload = await self.build_for_message_report(
            user_content,
            chat_id,
            on_event=on_event,
            process_context=process_context,
            client_ip=client_ip,
            workspace_task_active=workspace_task_active,
        )
        text = payload.get("text") or ""
        if not text:
            return ""
        return "Ambient context:\n" + text

    async def build_for_message_report(
        self,
        user_content: str,
        chat_id: str = "",
        on_event: ContextEventCallback | None = None,
        process_context: str = "",
        client_ip: str = "",
        workspace_task_active: bool = False,
    ) -> dict[str, Any]:
        """
        Build ambient context and emit plugin lifecycle events.

        :param user_content: Current user message
        :param chat_id: Chat identifier
        :param on_event: Optional callback for plugin lifecycle events
        :param process_context: Optional orchestration text used for detection
        :param client_ip: Optional client IP for location-aware plugins
        :param workspace_task_active: When True, skip plugins for workspace file tasks
        :return: Structured context payload
        """
        if not settings.context_plugins_enabled or workspace_task_active:
            return {
                "mode": "message",
                "text": "",
                "required_plugins": [],
                "results": [],
                "selection": [],
                "cached": False,
            }

        request = ContextRequest(
            user_content=user_content,
            chat_id=chat_id,
            client_ip=client_ip,
        )
        required = self._filter_enabled(
            detect_required_plugins(
                user_content,
                workspace_task_active=workspace_task_active,
            )
        )
        selection = explain_required_plugins(
            user_content,
            workspace_task_active=workspace_task_active,
        )

        if not required:
            return {
                "mode": "message",
                "text": "",
                "required_plugins": [],
                "results": [],
                "selection": [],
                "cached": False,
            }

        if on_event is not None:
            await on_event(
                {
                    "type": "context_plugins_started",
                    "required_plugins": required,
                    "selection": selection,
                }
            )

        results = await self._resolve_plugin_ids(request, required, on_event=on_event, selection=selection)
        payload = self._serialize(results, mode="message", required_plugins=required)
        payload["selection"] = selection

        if on_event is not None:
            await on_event({"type": "context_plugins_done", **payload})

        return payload

    async def build_for_content(
        self,
        user_content: str,
        process_context: str = "",
        client_ip: str = "",
    ) -> dict[str, Any]:
        """
        Build a structured context payload for API consumers.

        :param user_content: User message or prompt text
        :param process_context: Optional orchestration text used for detection
        :param client_ip: Optional client IP for location-aware plugins
        :return: Serialized plugin results with selection metadata
        """
        return await self.build_for_message_report(
            user_content,
            process_context=process_context,
            client_ip=client_ip,
        )

    def _filter_enabled(self, plugin_ids: list[str]) -> list[str]:
        """
        Keep only plugins that are enabled in settings.

        :param plugin_ids: Candidate plugin ids
        :return: Enabled plugin ids preserving order
        """
        enabled = self._enabled_plugin_ids()
        return [plugin_id for plugin_id in plugin_ids if plugin_id in enabled and plugin_id in self._plugins]

    async def _resolve_plugin_ids(
        self,
        request: ContextRequest,
        plugin_ids: list[str],
        on_event: ContextEventCallback | None = None,
        selection: list[dict[str, str]] | None = None,
    ) -> list[ContextResult]:
        """
        Resolve an explicit list of plugins.

        :param request: Context request payload
        :param plugin_ids: Plugin identifiers to resolve
        :param on_event: Optional callback for plugin lifecycle events
        :param selection: Optional plugin selection reasons
        :return: Plugin results
        """
        reason_map = {
            item["plugin_id"]: item["reason"]
            for item in (selection or [])
            if item.get("plugin_id")
        }
        results: list[ContextResult] = []
        for plugin_id in plugin_ids:
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                continue
            plugin_name = plugin_display_name(plugin_id)
            if on_event is not None:
                await on_event(
                    {
                        "type": "context_plugin_start",
                        "plugin_id": plugin_id,
                        "plugin_name": plugin_name,
                        "reason": reason_map.get(plugin_id),
                    }
                )
            result = await plugin.resolve(request)
            results.append(result)
            if on_event is not None:
                await on_event(
                    {
                        "type": "context_plugin_complete",
                        "plugin_id": plugin_id,
                        "plugin_name": plugin_name,
                        "ok": result.ok,
                        "text": result.text,
                        "error": result.error,
                    }
                )
        return results

    @staticmethod
    def _join_results(results: list[ContextResult]) -> str:
        """
        Join successful plugin texts.

        :param results: Plugin results
        :return: Combined multiline text
        """
        lines = [result.text for result in results if result.ok and result.text]
        return "\n".join(lines)

    def _serialize(
        self,
        results: list[ContextResult],
        *,
        mode: str,
        required_plugins: list[str],
    ) -> dict[str, Any]:
        """
        Serialize plugin results for API responses.

        :param results: Plugin results
        :param mode: Collection mode label
        :param required_plugins: Plugin ids selected for this run
        :return: JSON-serializable payload
        """
        return {
            "mode": mode,
            "text": self._join_results(results),
            "required_plugins": required_plugins,
            "results": [
                {
                    "plugin_id": result.plugin_id,
                    "plugin_name": plugin_display_name(result.plugin_id),
                    "ok": result.ok,
                    "text": result.text,
                    "error": result.error,
                    "data": result.data,
                }
                for result in results
            ],
            "cached": False,
        }


def build_default_registry() -> ContextRegistry:
    """
    Create registry with built-in context plugins.

    :return: Configured context registry
    """
    registry = ContextRegistry()
    for plugin in (
        DateTimeContextPlugin(),
        WeatherContextPlugin(),
        HolidaysContextPlugin(),
        ExchangeRatesContextPlugin(),
        SunTimesContextPlugin(),
        CountryFactsContextPlugin(),
        RandomFactContextPlugin(),
    ):
        registry.register(plugin)
    return registry


context_registry = build_default_registry()
