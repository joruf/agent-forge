import { describe, expect, it } from "vitest";
import { parseTaskBoardEvent, shouldShowTaskBoard } from "./taskBoard";

describe("parseTaskBoardEvent", () => {
  it("parses valid task board websocket payloads", () => {
    const snapshot = parseTaskBoardEvent({
      type: "task_board_updated",
      task_type: "workflow",
      complete: false,
      reason: "",
      targets: ["GitHub/Test12/index.html"],
      steps: [
        {
          step_id: 1,
          action: "create_directory",
          assignee: "developer",
          detail: "Create GitHub/Test12",
          path: "GitHub/Test12",
          status: "done",
        },
        {
          step_id: 2,
          action: "write_file",
          assignee: "developer",
          detail: "Write GitHub/Test12/index.html",
          path: "GitHub/Test12/index.html",
          status: "active",
        },
      ],
    });

    expect(snapshot).not.toBeNull();
    expect(snapshot?.steps).toHaveLength(2);
    expect(snapshot?.steps[0].status).toBe("done");
    expect(snapshot?.steps[1].action).toBe("write_file");
  });

  it("returns null for unrelated events", () => {
    expect(parseTaskBoardEvent({ type: "complete" })).toBeNull();
    expect(parseTaskBoardEvent(null)).toBeNull();
  });
});

describe("shouldShowTaskBoard", () => {
  it("hides general tasks and empty snapshots", () => {
    expect(shouldShowTaskBoard(null)).toBe(false);
    expect(
      shouldShowTaskBoard({
        task_type: "general",
        complete: true,
        reason: "",
        targets: [],
        steps: [{ step_id: 1, action: "analyze", assignee: "pm", detail: "", path: null, status: "done" }],
      }),
    ).toBe(false);
  });

  it("shows workflow snapshots with steps", () => {
    expect(
      shouldShowTaskBoard({
        task_type: "workflow",
        complete: false,
        reason: "",
        targets: [],
        steps: [{ step_id: 1, action: "write_file", assignee: "developer", detail: "x", path: "a.txt", status: "active" }],
      }),
    ).toBe(true);
  });
});
