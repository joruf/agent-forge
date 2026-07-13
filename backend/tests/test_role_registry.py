"""Tests for agent role registry."""

from agentforge.agents.role_registry import RoleRegistry


def test_builtin_roles_present() -> None:
    """Built-in roles are registered by default."""
    registry = RoleRegistry()
    roles = registry.list_roles()
    ids = {role.id for role in roles}
    assert "developer" in ids
    assert "software_tester" in ids
    assert "security" in ids
    assert "devops" in ids
    assert "project_manager" in ids
    assert len(roles) >= 9


def test_localized_role_names_german(german_locale) -> None:
    """Built-in role names are translated for German UI."""
    registry = RoleRegistry()
    roles = registry.list_roles_localized("de")
    developer = next(role for role in roles if role.id == "developer")
    assert developer.name == "Entwickler"


def test_localized_role_names_english(english_locale) -> None:
    """Built-in role names stay English in EN locale."""
    registry = RoleRegistry()
    roles = registry.list_roles_localized("en")
    developer = next(role for role in roles if role.id == "developer")
    assert developer.name == "Developer"


def test_get_roles_preserves_order() -> None:
    """get_roles returns requested roles in order."""
    registry = RoleRegistry()
    roles = registry.get_roles(["reviewer", "developer"])
    assert [role.id for role in roles] == ["reviewer", "developer"]
