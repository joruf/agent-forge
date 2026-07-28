import { describe, expect, it } from "vitest";
import { createTranslator } from "../i18n";
import {
  parseTaskBoardEvent,
  shouldShowTaskBoard,
  taskBoardReasonKey,
  translateTaskBoardReason,
} from "./taskBoard";

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

  it("parses optional missing paths", () => {
    const snapshot = parseTaskBoardEvent({
      type: "task_board_updated",
      task_type: "write_then_read",
      complete: false,
      reason: "Missing verified writes at required paths",
      missing: ["GitHub/Test12/hello.txt"],
      targets: ["GitHub/Test12/hello.txt"],
      steps: [
        {
          step_id: 1,
          action: "write_file",
          assignee: "developer",
          detail: "Write GitHub/Test12/hello.txt",
          path: "GitHub/Test12/hello.txt",
          status: "active",
        },
      ],
    });

    expect(snapshot?.missing).toEqual(["GitHub/Test12/hello.txt"]);
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

describe("translateTaskBoardReason", () => {
  it("maps known backend reasons to i18n keys", () => {
    expect(
      taskBoardReasonKey("Missing verified writes at required paths"),
    ).toBe("taskBoard.reason.missingVerifiedWritesAtRequiredPaths");
  });

  it("translates known reasons in German", () => {
    const t = createTranslator("de");
    expect(
      translateTaskBoardReason(
        "Missing verified writes at required paths",
        t,
      ),
    ).toContain("Verifizierte Schreibvorgänge");
  });

  it("falls back to the raw reason when unmapped", () => {
    const t = createTranslator("en");
    expect(translateTaskBoardReason("Custom blocker from reviewer", t)).toBe(
      "Custom blocker from reviewer",
    );
  });
});
