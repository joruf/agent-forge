import { SDLC_ROLE_ORDER } from "../constants/roles";

export const BUILTIN_ROLE_IDS = new Set<string>(SDLC_ROLE_ORDER);

export const ROLE_ID_PATTERN = /^[a-z][a-z0-9_]*$/;

export interface RoleFormValues {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
}

export interface RoleFormValidationError {
  field: keyof RoleFormValues;
  messageKey: string;
}

export function isBuiltinRole(role: { id: string; is_builtin?: boolean }): boolean {
  if (role.is_builtin) {
    return true;
  }
  return BUILTIN_ROLE_IDS.has(role.id);
}

export function validateRoleForm(
  values: RoleFormValues,
  options: { isCreate: boolean; existingIds?: string[] },
): RoleFormValidationError[] {
  const errors: RoleFormValidationError[] = [];
  const id = values.id.trim();
  const name = values.name.trim();
  const description = values.description.trim();
  const systemPrompt = values.system_prompt.trim();

  if (options.isCreate) {
    if (!id) {
      errors.push({ field: "id", messageKey: "settings.roles.errors.idRequired" });
    } else if (!ROLE_ID_PATTERN.test(id)) {
      errors.push({ field: "id", messageKey: "settings.roles.errors.idFormat" });
    } else if (BUILTIN_ROLE_IDS.has(id)) {
      errors.push({ field: "id", messageKey: "settings.roles.errors.idBuiltinConflict" });
    } else if (options.existingIds?.includes(id)) {
      errors.push({ field: "id", messageKey: "settings.roles.errors.idExists" });
    }
  }

  if (!name) {
    errors.push({ field: "name", messageKey: "settings.roles.errors.nameRequired" });
  }

  if (!description) {
    errors.push({ field: "description", messageKey: "settings.roles.errors.descriptionRequired" });
  }

  if (!systemPrompt) {
    errors.push({ field: "system_prompt", messageKey: "settings.roles.errors.systemPromptRequired" });
  }

  return errors;
}

export function parseApiError(error: unknown): string {
  if (!(error instanceof Error)) {
    return String(error);
  }
  const raw = error.message.trim();
  if (!raw) {
    return raw;
  }
  try {
    const parsed = JSON.parse(raw) as { detail?: string | Array<{ msg?: string }> };
    if (typeof parsed.detail === "string") {
      return parsed.detail;
    }
    if (Array.isArray(parsed.detail)) {
      return parsed.detail.map((entry) => entry.msg ?? "").filter(Boolean).join("; ");
    }
  } catch {
    return raw;
  }
  return raw;
}

export function partitionRoles<T extends { id: string; is_builtin?: boolean }>(
  roles: T[],
): { builtin: T[]; custom: T[] } {
  const builtin: T[] = [];
  const custom: T[] = [];
  for (const role of roles) {
    if (isBuiltinRole(role)) {
      builtin.push(role);
    } else {
      custom.push(role);
    }
  }
  return { builtin, custom };
}
