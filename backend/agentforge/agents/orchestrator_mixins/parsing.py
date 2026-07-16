"""Orchestrator mixin — parsing helpers and tool-call extraction."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any


class ParsingMixin:
    """Mixin for AgentOrchestrator parsing."""

    JSON_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
    KNOWN_TOOLS = frozenset({
        "write_file",
        "edit_file",
        "read_file",
        "search_files",
        "list_directory",
        "run_command",
        "remember",
        "web_search",
    })
    CODE_OUTPUT = re.compile(
        r"```|<!DOCTYPE|<\?php|<html[\s>]|function\s+\w+\(",
        re.IGNORECASE,
    )
    TOOL_USE_NUDGE = (
        "You responded with code or JSON text instead of using tools. "
        "Use the write_file tool now to create each file on disk. "
        "Do not reply with pasted code."
    )
    EMPTY_RESPONSE_NUDGE = (
        "Your last reply was empty or unusable for the team discussion. "
        "Use write_file to create the requested files, then summarize what you wrote."
    )
    READ_TOOL_USE_NUDGE = (
        "You responded with JSON or a placeholder instead of reading the file. "
        "Use read_file now for each requested path and quote the content verbatim."
    )
    READ_EMPTY_RESPONSE_NUDGE = (
        "Your last reply was empty or unusable. "
        "Use read_file to load the requested file and show its content to the team."
    )

    @classmethod
    def _is_weak_discussion_content(cls, content: str) -> bool:
        """
        Detect assistant replies that are empty placeholders.

        :param content: Assistant message text
        :return: True when the content should not be shown in team discussion
        """
        text = (content or "").strip()
        if not text:
            return True
        if text in ("{}", "[]", "null", "undefined"):
            return True
        if text.startswith("{") and len(text) <= 200:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return False
            if not parsed:
                return True
            if isinstance(parsed, dict):
                if set(parsed.keys()).issubset({"status", "message", "error", "code", "data"}):
                    status = str(parsed.get("status", "")).lower()
                    if status in {"success", "error", "ok", "failed", "failure"}:
                        return True
                if not parsed.get("function") and not parsed.get("name"):
                    if set(parsed.keys()).issubset({"arguments", "parameters", "tool", "content"}):
                        nested = parsed.get("arguments") or parsed.get("parameters") or {}
                        if not nested:
                            return True
        return False

    @classmethod
    def _finalize_agent_content(cls, content: str, tool_summaries: list[str]) -> str:
        """
        Replace weak assistant text with a summary of successful tool actions.

        :param content: Raw assistant message text
        :param tool_summaries: Human-readable tool action summaries
        :return: Content suitable for team discussion
        """
        if not cls._is_weak_discussion_content(content):
            return content
        if tool_summaries:
            return "Completed workspace actions:\n- " + "\n- ".join(tool_summaries)
        return content.strip() or "No output produced."

    @staticmethod
    def _summarize_tool_call(name: str, arguments: str, output: str) -> str | None:
        """
        Build a short summary line for a successful tool call.

        :param name: Tool name
        :param arguments: JSON-encoded tool arguments
        :param output: Tool execution output
        :return: Summary line or None
        """
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            parsed_arguments = {}

        if name == "write_file":
            path = parsed_arguments.get("path")
            if path:
                return f"Created/updated file: {path}"
        if name == "edit_file":
            path = parsed_arguments.get("path")
            match_id = parsed_arguments.get("match_id")
            if path and match_id:
                return f"Edited file: {path} at {match_id}"
            if path:
                return f"Edited file: {path}"
        if name == "search_files":
            query = parsed_arguments.get("query")
            if query:
                return f"Searched files for: {query}"
        if name == "run_command":
            command = parsed_arguments.get("command")
            if command:
                return f"Ran command: {command}"
        if output:
            return f"{name}: {output[:120]}"
        return None

    @classmethod
    def _looks_like_code_only_output(cls, content: str) -> bool:
        """
        Detect assistant replies that contain code but no tool execution.

        :param content: Assistant message text
        :return: True when output looks like file content instead of tool use
        """
        text = (content or "").strip()
        if not text:
            return False
        if cls.CODE_OUTPUT.search(text):
            return True
        if text.startswith("{") and '"content"' in text and '"function"' not in text:
            return True
        return False

    @staticmethod
    def _normalize_tool_call_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        """
        Normalize one embedded tool-call dict.

        :param payload: Parsed JSON object
        :return: Normalized tool call dict or None
        """
        name = payload.get("function") or payload.get("name") or payload.get("tool")
        if not isinstance(name, str) or not name:
            return None
        if name not in ParsingMixin.KNOWN_TOOLS:
            return None

        arguments = payload.get("arguments") or payload.get("parameters") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        return {
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "name": name,
            "arguments": json.dumps(arguments),
        }

    @classmethod
    def _extract_tool_calls_from_payload(cls, payload: Any) -> list[dict[str, Any]]:
        """
        Extract tool calls from parsed JSON payload.

        :param payload: Parsed JSON value
        :return: Normalized tool call dicts
        """
        if isinstance(payload, list):
            calls: list[dict[str, Any]] = []
            for item in payload:
                if isinstance(item, dict):
                    normalized = cls._normalize_tool_call_payload(item)
                    if normalized:
                        calls.append(normalized)
            return calls

        if not isinstance(payload, dict):
            return []

        normalized = cls._normalize_tool_call_payload(payload)
        return [normalized] if normalized else []

    @classmethod
    def _parse_content_tool_calls(cls, content: str) -> list[dict[str, Any]]:
        """
        Parse tool calls embedded in assistant text.

        Some Ollama models return JSON tool instructions in content instead of
        structured tool_calls.

        :param content: Assistant message text
        :return: Normalized tool call dicts
        """
        stripped = (content or "").strip()
        if not stripped:
            return []

        candidates = [match.group(1).strip() for match in cls.JSON_FENCE.finditer(stripped)]
        candidates.append(stripped)

        parsed_calls: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if not candidate.startswith("{") and not candidate.startswith("["):
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            for call in cls._extract_tool_calls_from_payload(payload):
                key = (call["name"], call["arguments"])
                if key in seen:
                    continue
                seen.add(key)
                parsed_calls.append(call)
        return parsed_calls

    @staticmethod
    def _parse_run_command_arguments(arguments: str) -> tuple[str, str | None]:
        """
        Parse run_command tool arguments.

        :param arguments: JSON-encoded tool arguments
        :return: Command string and optional relative cwd
        """
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments.strip(), None
        command = str(parsed.get("command", "")).strip()
        cwd = parsed.get("cwd")
        return command, str(cwd).strip() if cwd else None
