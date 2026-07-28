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
    missing: Array.isArray(data.missing)
      ? data.missing.map((path) => String(path))
      : undefined,
    targets: Array.isArray(data.targets)
      ? data.targets.map((target) => String(target))
      : [],
    steps: steps.sort((left, right) => left.step_id - right.step_id),
  };
}

const TASK_BOARD_REASON_KEYS: Record<string, string> = {
  "Missing verified file content": "taskBoard.reason.missingVerifiedFileContent",
  "No verified file content collected": "taskBoard.reason.noVerifiedFileContentCollected",
  "Missing verified writes at required paths": "taskBoard.reason.missingVerifiedWritesAtRequiredPaths",
  "No verified writes recorded": "taskBoard.reason.noVerifiedWritesRecorded",
  "Missing verified write_file steps": "taskBoard.reason.missingVerifiedWriteFileSteps",
  "Missing verified writes before read-back": "taskBoard.reason.missingVerifiedWritesBeforeReadBack",
  "Missing verified file content after write": "taskBoard.reason.missingVerifiedFileContentAfterWrite",
  "Missing verified file edits": "taskBoard.reason.missingVerifiedFileEdits",
  "No directory listing collected": "taskBoard.reason.noDirectoryListingCollected",
  "No command output collected": "taskBoard.reason.noCommandOutputCollected",
};

/**
 * Resolve a task-board blocker reason to an i18n key when known.
 *
 * @param reason Raw completion reason from the backend
 * @return i18n key or null when no mapping exists
 */
export function taskBoardReasonKey(reason: string): string | null {
  const trimmed = reason.trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed in TASK_BOARD_REASON_KEYS) {
    return TASK_BOARD_REASON_KEYS[trimmed];
  }
  if (trimmed.startsWith("Files written to wrong location:")) {
    return "taskBoard.reason.filesWrittenToWrongLocation";
  }
  if (trimmed.startsWith("Missing derived .txt file from ")) {
    return "taskBoard.reason.missingDerivedTxtFile";
  }
  if (trimmed.startsWith("Missing content-source write for ")) {
    return "taskBoard.reason.missingContentSourceWrite";
  }
  return null;
}

/**
 * Translate a task-board blocker reason for display.
 *
 * @param reason Raw completion reason from the backend
 * @param t i18n translate function
 * @return Localized reason text
 */
export function translateTaskBoardReason(
  reason: string,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  const trimmed = reason.trim();
  if (!trimmed) {
    return "";
  }
  const key = taskBoardReasonKey(trimmed);
  if (key === "taskBoard.reason.filesWrittenToWrongLocation") {
    const detail = trimmed.slice("Files written to wrong location:".length).trim();
    return t(key, { detail });
  }
  if (key === "taskBoard.reason.missingDerivedTxtFile") {
    const detail = trimmed.slice("Missing derived .txt file from ".length).trim();
    return t(key, { detail });
  }
  if (key === "taskBoard.reason.missingContentSourceWrite") {
    const detail = trimmed.slice("Missing content-source write for ".length).trim();
    return t(key, { detail });
  }
  if (key) {
    return t(key);
  }
  return trimmed;
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
