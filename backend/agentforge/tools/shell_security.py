"""Shell command classification and execution helpers."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path

from agentforge.config import settings
from agentforge.i18n import t


@dataclass(frozen=True)
class CommandClassification:
    """Result of validating a shell command against security rules."""

    allowed: bool
    needs_approval: bool
    base_command: str
    reason: str


def extract_base_command(command: str) -> str | None:
    """
    Extract the executable base name from a shell command string.

    :param command: Full shell command
    :return: Base command name or None when parsing fails
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts:
        return None
    return Path(parts[0]).name


def classify_shell_command(command: str) -> CommandClassification:
    """
    Classify a shell command against whitelist and blacklist rules.

    :param command: Full shell command string
    :return: Classification describing whether execution is allowed
    """
    base = extract_base_command(command)
    if base is None:
        return CommandClassification(
            allowed=False,
            needs_approval=False,
            base_command="",
            reason="Invalid command syntax",
        )
    if not base:
        return CommandClassification(
            allowed=False,
            needs_approval=False,
            base_command="",
            reason="Empty command",
        )
    if base in settings.command_blacklist:
        return CommandClassification(
            allowed=False,
            needs_approval=False,
            base_command=base,
            reason=f"Command '{base}' is blocked",
        )
    if base in settings.command_whitelist:
        return CommandClassification(
            allowed=True,
            needs_approval=False,
            base_command=base,
            reason="Whitelisted",
        )
    return CommandClassification(
        allowed=True,
        needs_approval=True,
        base_command=base,
        reason=t("tools.approval_required", command=base),
    )


async def run_shell_command(
    command: str,
    cwd: Path,
) -> tuple[bool, int | None, str]:
    """
    Execute a shell command inside the workspace.

    :param command: Shell command string
    :param cwd: Working directory for the subprocess
    :return: Tuple of success flag, exit code, and formatted output
    """
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
    if len(output) > settings.max_output_chars:
        output = output[: settings.max_output_chars] + "\n... [truncated]"
    exit_code = proc.returncode
    status = "OK" if exit_code == 0 else f"Exit {exit_code}"
    formatted = f"[{status}]\n{output}".strip()
    return exit_code == 0, exit_code, formatted
