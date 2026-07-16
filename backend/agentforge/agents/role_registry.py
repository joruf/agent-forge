"""Built-in and custom agent role definitions."""

import json
from pathlib import Path

import yaml

from agentforge.config import settings
from agentforge.i18n import current_locale, t
from agentforge.models.schemas import AgentRole

BUILTIN_ROLES: list[AgentRole] = [
    AgentRole(
        id="developer",
        name="Developer",
        description="Writes, edits, and refactors code in the workspace.",
        system_prompt=(
            "You are an expert software developer. You write clean, maintainable code, "
            "follow project conventions, and use available tools to read, create, and "
            "edit files. You explain technical decisions briefly when helpful."
        ),
    ),
    AgentRole(
        id="reviewer",
        name="Reviewer",
        description="Reviews code quality, bugs, and best practices.",
        system_prompt=(
            "You are a senior code reviewer. You analyze code for bugs, security issues, "
            "performance problems, and style violations. Provide actionable feedback with "
            "specific suggestions."
        ),
    ),
    AgentRole(
        id="architect",
        name="Architect",
        description="Plans system structure, modules, and interfaces.",
        system_prompt=(
            "You are a software architect. You design scalable structures, define module "
            "boundaries, choose appropriate patterns, and document architectural decisions."
        ),
    ),
    AgentRole(
        id="researcher",
        name="Researcher",
        description="Researches topics and summarizes findings.",
        system_prompt=(
            "You are a technical researcher. You gather information, compare approaches, "
            "and produce clear summaries with sources when available."
        ),
    ),
    AgentRole(
        id="documentation",
        name="Documentation",
        description="Creates and maintains documentation and technical writing.",
        system_prompt=(
            "You are a technical writer. You create clear documentation, README files, "
            "API docs, and user guides. You structure content for readability."
        ),
    ),
    AgentRole(
        id="project_manager",
        name="Project Manager",
        description="Coordinates agents, involves the user when needed, delivers results.",
        system_prompt=(
            "You are a project manager coordinating a team of AI agents. You break down "
            "tasks, delegate to specialists, synthesize their outputs, ask the user clarifying "
            "questions when blocked, and deliver a clear final result."
        ),
    ),
    AgentRole(
        id="software_tester",
        name="Software Tester",
        description="Designs test cases, runs tests, and reports quality issues.",
        system_prompt=(
            "You are an expert software tester and QA engineer. You analyze requirements and "
            "code, design test cases, identify edge cases and regressions, run tests via shell "
            "tools when appropriate, and report clear, actionable findings with reproduction steps."
        ),
    ),
    AgentRole(
        id="security",
        name="Security Engineer",
        description="Reviews code and architecture for security vulnerabilities.",
        system_prompt=(
            "You are a security engineer focused on secure software development. You identify "
            "vulnerabilities (injection, auth, secrets, dependencies), review code and configs, "
            "reference OWASP best practices, and recommend concrete mitigations without unnecessary alarmism."
        ),
    ),
    AgentRole(
        id="devops",
        name="DevOps Engineer",
        description="Handles CI/CD, deployment scripts, and infrastructure automation.",
        system_prompt=(
            "You are a DevOps engineer. You design and maintain build pipelines, deployment "
            "scripts, Docker/CI configs, and operational tooling. You use shell and file tools "
            "safely, prefer reproducible automation, and explain operational trade-offs clearly."
        ),
    ),
]


class RoleRegistry:
    """Registry for built-in and user-defined roles."""

    def __init__(self, roles_dir: Path | None = None) -> None:
        """Load built-in roles and optional custom role files."""
        self._roles: dict[str, AgentRole] = {r.id: r for r in BUILTIN_ROLES}
        self.roles_dir = roles_dir or settings.roles_dir
        self._load_custom_roles()

    def _load_custom_roles(self) -> None:
        """Load YAML/JSON role files from assets directory."""
        if not self.roles_dir.exists():
            return
        for path in self.roles_dir.glob("*"):
            if path.suffix not in (".yaml", ".yml", ".json"):
                continue
            try:
                raw = path.read_text(encoding="utf-8")
                data = yaml.safe_load(raw) if path.suffix != ".json" else json.loads(raw)
                if isinstance(data, list):
                    items = data
                else:
                    items = [data]
                for item in items:
                    role = AgentRole(**item, is_builtin=False)
                    self._roles[role.id] = role
            except Exception:
                continue

    def list_roles(self) -> list[AgentRole]:
        """Return all registered roles."""
        return list(self._roles.values())

    def list_roles_localized(self, locale: str | None = None) -> list[AgentRole]:
        """Return roles with localized names for built-in roles."""
        lang = locale or current_locale()
        localized: list[AgentRole] = []
        for role in self.list_roles():
            if role.is_builtin:
                localized.append(
                    role.model_copy(
                        update={
                            "name": t(f"roles.{role.id}.name", locale=lang),
                            "description": t(f"roles.{role.id}.description", locale=lang),
                        }
                    )
                )
            else:
                localized.append(role)
        return localized

    def get_role(self, role_id: str) -> AgentRole | None:
        """Get role by ID."""
        return self._roles.get(role_id)

    def get_roles(self, role_ids: list[str]) -> list[AgentRole]:
        """Get multiple roles preserving order."""
        roles = []
        for role_id in role_ids:
            role = self.get_role(role_id)
            if role:
                roles.append(role)
        return roles

    def add_role(self, role: AgentRole) -> AgentRole:
        """Register a custom role and persist to disk."""
        if role.id in self._roles:
            existing = self._roles[role.id]
            if existing.is_builtin:
                raise ValueError(f"Role id '{role.id}' conflicts with a built-in role")
            raise ValueError(f"Role id '{role.id}' already exists")
        role.is_builtin = False
        self._roles[role.id] = role
        self._persist_role(role)
        return role

    def update_role(self, role_id: str, role: AgentRole) -> AgentRole:
        """Update an existing custom role and persist to disk."""
        existing = self._roles.get(role_id)
        if not existing:
            raise KeyError(role_id)
        if existing.is_builtin:
            raise ValueError("Built-in roles cannot be modified")
        role.id = role_id
        role.is_builtin = False
        self._roles[role_id] = role
        self._persist_role(role)
        return role

    def delete_role(self, role_id: str) -> None:
        """Remove a custom role from memory and delete its file."""
        existing = self._roles.get(role_id)
        if not existing:
            raise KeyError(role_id)
        if existing.is_builtin:
            raise ValueError("Built-in roles cannot be deleted")
        del self._roles[role_id]
        path = self.roles_dir / f"{role_id}.yaml"
        if path.exists():
            path.unlink()

    def _persist_role(self, role: AgentRole) -> None:
        """Write a custom role to the roles directory."""
        self.roles_dir.mkdir(parents=True, exist_ok=True)
        path = self.roles_dir / f"{role.id}.yaml"
        path.write_text(
            yaml.dump(role.model_dump(), allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )


role_registry = RoleRegistry()
