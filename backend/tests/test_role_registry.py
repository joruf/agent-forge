"""Tests for agent role registry."""

import pytest

from agentforge.agents.role_registry import RoleRegistry
from agentforge.models.schemas import AgentRole


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


def test_add_update_delete_custom_role(tmp_path) -> None:
    """Custom roles can be created, updated, and deleted."""
    registry = RoleRegistry(roles_dir=tmp_path / "roles")
    created = registry.add_role(
        AgentRole(
            id="custom_analyst",
            name="Analyst",
            description="Analyzes data",
            system_prompt="You analyze data.",
            is_builtin=False,
        )
    )
    assert created.id == "custom_analyst"
    assert (tmp_path / "roles" / "custom_analyst.yaml").exists()

    updated = registry.update_role(
        "custom_analyst",
        AgentRole(
            id="custom_analyst",
            name="Senior Analyst",
            description="Analyzes data deeply",
            system_prompt="You analyze data in depth.",
            is_builtin=False,
        ),
    )
    assert updated.name == "Senior Analyst"

    registry.delete_role("custom_analyst")
    assert registry.get_role("custom_analyst") is None
    assert not (tmp_path / "roles" / "custom_analyst.yaml").exists()


def test_cannot_modify_builtin_role(tmp_path) -> None:
    """Built-in roles reject update and delete."""
    registry = RoleRegistry(roles_dir=tmp_path / "roles")
    with pytest.raises(ValueError, match="Built-in"):
        registry.update_role(
            "developer",
            AgentRole(
                id="developer",
                name="Dev",
                description="x",
                system_prompt="y",
                is_builtin=False,
            ),
        )
    with pytest.raises(ValueError, match="Built-in"):
        registry.delete_role("developer")


def test_add_role_rejects_duplicate_id(tmp_path) -> None:
    """Duplicate custom role ids are rejected."""
    registry = RoleRegistry(roles_dir=tmp_path / "roles")
    role = AgentRole(
        id="custom_role",
        name="Custom",
        description="Desc",
        system_prompt="Prompt",
        is_builtin=False,
    )
    registry.add_role(role)
    with pytest.raises(ValueError, match="already exists"):
        registry.add_role(role)
    with pytest.raises(ValueError, match="built-in"):
        registry.add_role(
            AgentRole(
                id="developer",
                name="Dev",
                description="Desc",
                system_prompt="Prompt",
                is_builtin=False,
            )
        )
