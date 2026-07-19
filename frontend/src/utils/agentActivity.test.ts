import { describe, expect, it } from "vitest";
import {
  applyAgentActivityEvent,
  describeToolCall,
  shouldShowAgentActivity,
} from "./agentActivity";

const t = (key: string, params?: Record<string, string | number>) => {
  if (params) {
    return `${key}:${JSON.stringify(params)}`;
  }
  return key;
};

const context = {
  t,
  resolveRoleName: (roleId: string) => roleId,
};

describe("applyAgentActivityEvent", () => {
  it("tracks agent start and end lifecycle", () => {
    let items = applyAgentActivityEvent([], {
      type: "agent_start",
      agent_id: "developer",
      agent_name: "Developer",
    }, context);

    expect(items).toHaveLength(1);
    expect(items[0].status).toBe("running");

    items = applyAgentActivityEvent(items, {
      type: "agent_end",
      agent_id: "developer",
    }, context);

    expect(items[0].status).toBe("done");
  });

  it("tracks tool call and result pairs", () => {
    let items = applyAgentActivityEvent([], {
      type: "tool_call",
      agent_id: "developer",
      agent_name: "Developer",
      tool: "write_file",
      arguments: JSON.stringify({ path: "GitHub/emailsender/SimpleEmailSender.php" }),
    }, context);

    expect(items[0].description).toContain("GitHub/emailsender/SimpleEmailSender.php");
    expect(items[0].status).toBe("running");

    items = applyAgentActivityEvent(items, {
      type: "tool_result",
      agent_id: "developer",
      tool: "write_file",
      success: true,
    }, context);

    expect(items[0].status).toBe("done");
  });

  it("marks wrong-location tool results as failed", () => {
    let items = applyAgentActivityEvent([], {
      type: "tool_call",
      agent_id: "developer",
      agent_name: "Developer",
      tool: "write_file",
      arguments: JSON.stringify({ path: "GitHub/emailsender/SimpleEmailSender.php" }),
    }, context);

    items = applyAgentActivityEvent(items, {
      type: "tool_result",
      agent_id: "developer",
      tool: "write_file",
      success: false,
    }, context);

    expect(items[0].status).toBe("failed");
  });
});

describe("describeToolCall", () => {
  it("describes write_file with path", () => {
    const label = describeToolCall(
      "write_file",
      JSON.stringify({ path: "GitHub/emailsender/SimpleEmailSender.php" }),
      t,
    );
    expect(label).toContain("GitHub/emailsender/SimpleEmailSender.php");
  });
});

describe("shouldShowAgentActivity", () => {
  it("shows while loading with items", () => {
    expect(shouldShowAgentActivity([
      {
        id: "1",
        agentId: "developer",
        agentName: "Developer",
        description: "Writing file",
        status: "done",
      },
    ], true)).toBe(true);
  });

  it("hides when idle and all items are done", () => {
    expect(shouldShowAgentActivity([
      {
        id: "1",
        agentId: "developer",
        agentName: "Developer",
        description: "Writing file",
        status: "done",
      },
    ], false)).toBe(false);
  });
});
