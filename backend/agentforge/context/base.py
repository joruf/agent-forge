"""Base types for ambient context plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PluginTiming(str, Enum):
    """When a context plugin should run."""

    STARTUP = "startup"
    JIT = "jit"
    BOTH = "both"


@dataclass(frozen=True)
class PluginCatalogEntry:
    """Documentation metadata for a context plugin."""

    id: str
    name: str
    description: str
    timing: PluginTiming
    api_name: str
    api_url: str
    api_key_required: bool
    license_note: str
    trigger_keywords: tuple[str, ...] = ()
    docs_url: str = ""


@dataclass
class ContextRequest:
    """Input passed to context plugins."""

    user_content: str = ""
    chat_id: str = ""
    client_ip: str = ""
    force: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextResult:
    """Output from a single context plugin."""

    plugin_id: str
    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cached: bool = False


class ContextPlugin(ABC):
    """Provides read-only ambient facts for system prompts or tools."""

    id: str
    timing: PluginTiming
    trigger_keywords: tuple[str, ...] = ()

    @abstractmethod
    async def resolve(self, request: ContextRequest) -> ContextResult:
        """
        Resolve context for the current request.

        :param request: Context request payload
        :return: Structured context result
        """

    def should_run(self, request: ContextRequest, *, startup: bool) -> bool:
        """
        Decide whether the plugin should run for this request.

        :param request: Context request payload
        :param startup: True when building startup context
        :return: True when the plugin should execute
        """
        if request.force:
            return True
        if startup:
            return self.timing in (PluginTiming.STARTUP, PluginTiming.BOTH)
        if self.timing in (PluginTiming.STARTUP,):
            return False
        if self.timing in (PluginTiming.JIT, PluginTiming.BOTH):
            if not self.trigger_keywords:
                return False
            lowered = (request.user_content or "").lower()
            return any(keyword in lowered for keyword in self.trigger_keywords)
        return False
