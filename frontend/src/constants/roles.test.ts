import { describe, expect, it } from "vitest";
import {
  DEFAULT_MULTI_ROLES,
  SDLC_ROLE_ORDER,
  sortSdlcRoles,
} from "./roles";

describe("sortSdlcRoles", () => {
  it("orders roles according to SDLC_ROLE_ORDER", () => {
    const input = [
      { id: "project_manager", name: "PM" },
      { id: "developer", name: "Dev" },
      { id: "security", name: "Sec" },
    ];
    expect(sortSdlcRoles(input).map((role) => role.id)).toEqual([
      "developer",
      "security",
      "project_manager",
    ]);
  });

  it("keeps unknown roles after known ones", () => {
    const input = [
      { id: "custom_role", name: "Custom" },
      { id: "developer", name: "Dev" },
    ];
    expect(sortSdlcRoles(input).map((role) => role.id)).toEqual([
      "developer",
      "custom_role",
    ]);
  });
});

describe("role constants", () => {
  it("defines nine SDLC roles in display order", () => {
    expect(SDLC_ROLE_ORDER).toHaveLength(9);
    expect(SDLC_ROLE_ORDER).toContain("software_tester");
    expect(SDLC_ROLE_ORDER).toContain("security");
    expect(SDLC_ROLE_ORDER).toContain("devops");
  });

  it("defaults multi-agent selection to PM, developer, reviewer, and tester", () => {
    expect(DEFAULT_MULTI_ROLES).toEqual([
      "project_manager",
      "developer",
      "reviewer",
      "software_tester",
    ]);
  });
});
