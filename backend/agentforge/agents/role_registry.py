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
        description=(
            "Reviews code quality, bugs, and best practices — from shared diffs/snippets in "
            "team discussions, or by reading files directly when working alone."
        ),
        system_prompt=(
            "You are a senior code reviewer. In a team discussion you usually review the diffs, "
            "snippets, and summaries other agents have already shared in the conversation rather "
            "than exploring the repository yourself — ask for the relevant code if it's missing. "
            "When working alone, you have full read access and should open the actual files. "
            "Analyze code for bugs, security issues, performance problems, and style violations, "
            "and give actionable, specific feedback."
        ),
    ),
    AgentRole(
        id="architect",
        name="Architect",
        description=(
            "Plans system structure, modules, and interfaces; inspects the codebase but leaves "
            "full feature implementation to the developer role."
        ),
        system_prompt=(
            "You are a software architect. You design scalable structures, define module "
            "boundaries, choose appropriate patterns, and document architectural decisions. "
            "You have the same file and shell access as a developer, but use it mainly to "
            "inspect the existing codebase and write design docs or interface stubs — leave "
            "full feature implementation to the developer role to avoid duplicated work, unless "
            "no developer is involved in the task."
        ),
    ),
    AgentRole(
        id="researcher",
        name="Researcher",
        description=(
            "Researches external topics via web search and memory; has no file access, so "
            "cannot inspect this codebase directly."
        ),
        system_prompt=(
            "You are a technical researcher. You have web search and long-term memory, but no "
            "file or shell tools — you cannot open files in this workspace. Gather information, "
            "compare approaches, cite sources when available, and produce clear summaries. If a "
            "question requires inspecting this project's actual code, say so explicitly and "
            "defer to the developer or architect role instead of guessing."
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
        self._overridden_ids: set[str] = set()
        self.roles_dir = roles_dir or settings.roles_dir
        self._load_custom_roles()

    def _load_custom_roles(self) -> None:
        """Load YAML/JSON role files from assets directory.

        A file whose ``id`` matches a built-in role is treated as a persisted
        edit of that built-in role (keeps ``is_builtin=True``) rather than a
        new custom role.
        """
        if not self.roles_dir.exists():
            return
        builtin_ids = {r.id for r in BUILTIN_ROLES}
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
                    item = dict(item)
                    item.pop("is_builtin", None)
                    role = AgentRole(**item, is_builtin=item.get("id") in builtin_ids)
                    self._roles[role.id] = role
                    self._overridden_ids.add(role.id)
            except Exception:
                continue

    def list_roles(self) -> list[AgentRole]:
        """Return all registered roles."""
        return list(self._roles.values())

    def list_roles_localized(self, locale: str | None = None) -> list[AgentRole]:
        """Return roles with localized names for untouched built-in roles.

        Built-in roles the user has edited (persisted to ``roles_dir``) keep
        their saved name/description as-is instead of falling back to the
        translation catalog.
        """
        lang = locale or current_locale()
        localized: list[AgentRole] = []
        for role in self.list_roles():
            if role.is_builtin and role.id not in self._overridden_ids:
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
        """Update an existing role (built-in or custom) and persist to disk.

        Built-in roles keep ``is_builtin=True`` so they remain protected from
        deletion, but their name/description/system prompt can be edited and
        the edit is persisted as an override in ``roles_dir``.
        """
        existing = self._roles.get(role_id)
        if not existing:
            raise KeyError(role_id)
        role.id = role_id
        role.is_builtin = existing.is_builtin
        self._roles[role_id] = role
        self._overridden_ids.add(role_id)
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
