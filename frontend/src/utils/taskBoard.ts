import type { TaskBoardSnapshot, TaskBoardStep, TaskBoardStepStatus } from "../types";

/**
 * Parse a WebSocket task-board payload into a typed snapshot.
 *
 * @param payload Raw WebSocket event data
 * @return Parsed snapshot or null when invalid
 */
export function parseTaskBoardEvent(payload: unknown): TaskBoardSnapshot | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const data = payload as Record<string, unknown>;
  if (data.type !== "task_board_updated") {
    return null;
  }
  if (!Array.isArray(data.steps)) {
    return null;
  }

  const steps: TaskBoardStep[] = data.steps.flatMap((entry) => {
    if (!entry || typeof entry !== "object") {
      return [];
    }
    const step = entry as Record<string, unknown>;
    const stepId = Number(step.step_id);
    const action = String(step.action ?? "").trim();
    const assignee = String(step.assignee ?? "").trim();
    const detail = String(step.detail ?? "").trim();
    const status = String(step.status ?? "pending").trim() as TaskBoardStepStatus;
    if (!stepId || !action) {
      return [];
    }
    const normalizedStatus: TaskBoardStepStatus =
      status === "done" || status === "active" ? status : "pending";
    return [{
      step_id: stepId,
      action,
      assignee,
      detail,
      path: typeof step.path === "string" ? step.path : null,
      status: normalizedStatus,
    }];
  });

  if (steps.length === 0) {
    return null;
  }

  return {
    task_type: String(data.task_type ?? "general"),
    complete: Boolean(data.complete),
    reason: typeof data.reason === "string" ? data.reason : "",
    targets: Array.isArray(data.targets)
      ? data.targets.map((target) => String(target))
      : [],
    steps: steps.sort((left, right) => left.step_id - right.step_id),
  };
}

/**
 * Return whether a task-board snapshot should be shown in the chat UI.
 *
 * @param snapshot Parsed task-board snapshot
 * @return True when the panel should render
 */
export function shouldShowTaskBoard(snapshot: TaskBoardSnapshot | null): boolean {
  return snapshot !== null && snapshot.steps.length > 0 && snapshot.task_type !== "general";
}

/**
 * Return whether the task board should render for the current chat mode and grill phase.
 *
 * @param snapshot Parsed task-board snapshot
 * @param chatMode Active chat mode
 * @param grillPhase Current grill workflow phase, if any
 * @return True when the task board panel should render
 */
export function shouldShowTaskBoardInChat(
  snapshot: TaskBoardSnapshot | null,
  chatMode: string | undefined,
  grillPhase: { phase: string } | null,
  grillEnabled = false,
): boolean {
  if (!shouldShowTaskBoard(snapshot)) {
    return false;
  }
  const grillMode = chatMode === "grill" || grillEnabled;
  if (!grillMode) {
    return true;
  }
  if (!grillPhase) {
    return false;
  }
  return grillPhase.phase === "execute" || grillPhase.phase === "test" || grillPhase.phase === "done";
}
