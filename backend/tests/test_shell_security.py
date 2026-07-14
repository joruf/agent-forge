"""Tests for shell command security helpers."""

from agentforge.tools.shell_security import classify_shell_command, extract_base_command


def test_extract_base_command() -> None:
    """Base command names are parsed from shell strings."""
    assert extract_base_command("mkdir -p GitHub/Test123") == "mkdir"
    assert extract_base_command("git status") == "git"


def test_classify_whitelisted_command() -> None:
    """Whitelisted commands run without approval."""
    result = classify_shell_command("mkdir GitHub/Test123")
    assert result.allowed is True
    assert result.needs_approval is False
    assert result.base_command == "mkdir"


def test_classify_blacklisted_command(monkeypatch) -> None:
    """Blacklisted commands are rejected."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "command_blacklist", ["rm"])
    result = classify_shell_command("rm -rf /")
    assert result.allowed is False
    assert result.needs_approval is False
    assert "blocked" in result.reason.lower()


def test_classify_unknown_command_needs_approval() -> None:
    """Unknown commands require approval."""
    result = classify_shell_command("custom-tool --help")
    assert result.allowed is True
    assert result.needs_approval is True
    assert result.base_command == "custom-tool"
