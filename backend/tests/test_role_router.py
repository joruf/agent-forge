"""Tests for SDLC role auto-routing."""

from agentforge.agents.role_router import (
    AUTO_ROLE,
    detect_sdlc_role,
    resolve_single_role,
)


def test_detect_security_role() -> None:
    """Security-related prompts route to the security role."""
    assert detect_sdlc_role("Check this code for OWASP vulnerabilities") == "security"
    assert detect_sdlc_role("Prüfe die Authentifizierung auf Schwachstellen") == "security"


def test_detect_tester_role() -> None:
    """Testing prompts route to the software tester role."""
    assert detect_sdlc_role("Write pytest tests for the login module") == "software_tester"
    assert detect_sdlc_role("Erstelle Testfälle für die Registrierung") == "software_tester"


def test_detect_devops_role() -> None:
    """DevOps prompts route to the devops role."""
    assert detect_sdlc_role("Set up a GitHub Actions CI/CD pipeline") == "devops"


def test_detect_developer_fallback() -> None:
    """Generic coding prompts route to developer."""
    assert detect_sdlc_role("Fix the bug in auth.py") == "developer"
    assert detect_sdlc_role("") == "developer"


def test_resolve_single_role_auto() -> None:
    """Auto mode detects the role from content."""
    role_id, used_auto = resolve_single_role([AUTO_ROLE], "Review this pull request")
    assert used_auto is True
    assert role_id == "reviewer"


def test_resolve_single_role_manual() -> None:
    """Manual role selection bypasses auto routing."""
    role_id, used_auto = resolve_single_role(["architect"], "Fix this bug")
    assert used_auto is False
    assert role_id == "architect"
