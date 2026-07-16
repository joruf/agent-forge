import { describe, expect, it } from "vitest";
import {
  BUILTIN_ROLE_IDS,
  isBuiltinRole,
  partitionRoles,
  validateRoleForm,
} from "./roleForm";

describe("isBuiltinRole", () => {
  it("detects built-in roles by flag or id", () => {
    expect(isBuiltinRole({ id: "developer", is_builtin: true })).toBe(true);
    expect(isBuiltinRole({ id: "developer", is_builtin: false })).toBe(true);
    expect(isBuiltinRole({ id: "custom_analyst", is_builtin: false })).toBe(false);
  });
});

describe("validateRoleForm", () => {
  it("requires slug id on create", () => {
    const errors = validateRoleForm(
      { id: "Bad ID", name: "Name", description: "Desc", system_prompt: "Prompt" },
      { isCreate: true },
    );
    expect(errors.some((error) => error.field === "id")).toBe(true);
  });

  it("rejects built-in id conflicts on create", () => {
    const errors = validateRoleForm(
      { id: "developer", name: "Name", description: "Desc", system_prompt: "Prompt" },
      { isCreate: true },
    );
    expect(errors.some((error) => error.messageKey === "settings.roles.errors.idBuiltinConflict")).toBe(true);
  });

  it("rejects duplicate custom ids on create", () => {
    const errors = validateRoleForm(
      { id: "custom_role", name: "Name", description: "Desc", system_prompt: "Prompt" },
      { isCreate: true, existingIds: ["custom_role"] },
    );
    expect(errors.some((error) => error.messageKey === "settings.roles.errors.idExists")).toBe(true);
  });

  it("does not validate id on edit", () => {
    const errors = validateRoleForm(
      { id: "", name: "Name", description: "Desc", system_prompt: "Prompt" },
      { isCreate: false },
    );
    expect(errors.some((error) => error.field === "id")).toBe(false);
  });

  it("requires all editable fields", () => {
    const errors = validateRoleForm(
      { id: "custom_role", name: "", description: "", system_prompt: "" },
      { isCreate: false },
    );
    expect(errors.map((error) => error.field)).toEqual(["name", "description", "system_prompt"]);
  });
});

describe("partitionRoles", () => {
  it("splits built-in and custom roles", () => {
    const roles = [
      { id: "developer", is_builtin: true },
      { id: "custom_role", is_builtin: false },
    ];
    const { builtin, custom } = partitionRoles(roles);
    expect(builtin).toHaveLength(1);
    expect(custom).toHaveLength(1);
    expect(builtin[0].id).toBe("developer");
    expect(custom[0].id).toBe("custom_role");
  });
});

describe("BUILTIN_ROLE_IDS", () => {
  it("includes all nine SDLC built-in roles", () => {
    expect(BUILTIN_ROLE_IDS.size).toBe(9);
    expect(BUILTIN_ROLE_IDS.has("project_manager")).toBe(true);
    expect(BUILTIN_ROLE_IDS.has("devops")).toBe(true);
  });
});
