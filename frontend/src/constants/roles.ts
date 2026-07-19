export const SINGLE_AUTO_ROLE = "auto";

export const DEFAULT_SINGLE_ROLE = SINGLE_AUTO_ROLE;

export const SINGLE_FALLBACK_ROLE = "developer";

export const DEFAULT_MULTI_ROLES = [
  "project_manager",
  "developer",
  "reviewer",
  "software_tester",
];

export const SDLC_ROLE_ORDER = [
  "developer",
  "software_tester",
  "reviewer",
  "security",
  "architect",
  "devops",
  "researcher",
  "documentation",
  "project_manager",
];

export function sortSdlcRoles<T extends { id: string }>(roles: T[]): T[] {
  const order = new Map(SDLC_ROLE_ORDER.map((id, index) => [id, index]));
  return [...roles].sort((a, b) => {
    const left = order.get(a.id) ?? 999;
    const right = order.get(b.id) ?? 999;
    return left - right || a.id.localeCompare(b.id);
  });
}

export function normalizeSingleRoleIds(roleIds: string[]): string[] {
  return [roleIds[0] ?? DEFAULT_SINGLE_ROLE];
}

export function isAutoSingleRole(roleIds: string[]): boolean {
  return normalizeSingleRoleIds(roleIds)[0] === SINGLE_AUTO_ROLE;
}
