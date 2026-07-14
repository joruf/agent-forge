import type { ApprovalRequest, Message, ShellCommandEntry } from "../types";

export function isShellCommandMessage(message: Message): boolean {
  if (message.metadata?.kind === "shell_command") {
    return true;
  }
  return message.role === "tool" && message.content.startsWith("Command executed:");
}

export function messageToShellCommand(message: Message): ShellCommandEntry | null {
  if (message.metadata?.kind === "shell_command") {
    const status = String(message.metadata.status ?? (message.metadata.success ? "success" : "failed"));
    return {
      id: message.id,
      command: String(message.metadata.command ?? ""),
      cwd: message.metadata.cwd ? String(message.metadata.cwd) : undefined,
      status: status as ShellCommandEntry["status"],
      success: Boolean(message.metadata.success),
      exit_code:
        typeof message.metadata.exit_code === "number" ? message.metadata.exit_code : null,
      agent_id: message.agent_id,
      agent_name: message.agent_name,
      approval_id: message.metadata.approval_id
        ? String(message.metadata.approval_id)
        : undefined,
      output: message.content,
      timestamp: message.created_at,
    };
  }

  if (message.role === "tool" && message.content.startsWith("Command executed:")) {
    const [firstLine, ...rest] = message.content.split("\n");
    const command = firstLine.replace("Command executed:", "").trim();
    if (!command) {
      return null;
    }
    return {
      id: message.id,
      command,
      status: "success",
      success: true,
      exit_code: null,
      agent_id: message.agent_id,
      agent_name: message.agent_name,
      approval_id: message.metadata.approval_id
        ? String(message.metadata.approval_id)
        : undefined,
      output: rest.join("\n").trim(),
      timestamp: message.created_at,
    };
  }

  return null;
}

export function approvalToShellCommand(approval: ApprovalRequest): ShellCommandEntry {
  return {
    id: `pending-${approval.id}`,
    command: String(approval.payload.command ?? approval.description),
    cwd: approval.payload.cwd ? String(approval.payload.cwd) : undefined,
    status: "pending",
    success: false,
    exit_code: null,
    approval_id: approval.id,
    timestamp: approval.created_at,
  };
}

export function collectShellCommands(
  messages: Message[],
  approvals: ApprovalRequest[],
  pendingLive: ShellCommandEntry[] = [],
): ShellCommandEntry[] {
  const recorded = messages
    .map(messageToShellCommand)
    .filter((entry): entry is ShellCommandEntry => entry !== null);

  const recordedApprovalIds = new Set(
    recorded
      .map((entry) => entry.approval_id)
      .filter((approvalId): approvalId is string => Boolean(approvalId)),
  );

  const pendingFromApprovals = approvals
    .filter((approval) => approval.action_type === "command")
    .filter((approval) => !recordedApprovalIds.has(approval.id))
    .map(approvalToShellCommand);

  const pendingFromLive = pendingLive.filter(
    (entry) => !entry.approval_id || !recordedApprovalIds.has(entry.approval_id),
  );

  const merged = new Map<string, ShellCommandEntry>();
  for (const entry of [...recorded, ...pendingFromApprovals, ...pendingFromLive]) {
    const key = entry.approval_id ?? entry.id;
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, entry);
      continue;
    }
    const replaceExisting =
      (existing.status === "pending" && entry.status !== "pending")
      || new Date(entry.timestamp).getTime() >= new Date(existing.timestamp).getTime();
    if (replaceExisting) {
      merged.set(key, entry);
    }
  }

  return Array.from(merged.values()).sort(
    (left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
  );
}

const EXECUTED_SHELL_COMMAND_STATUSES = new Set<ShellCommandEntry["status"]>([
  "success",
  "failed",
]);

export function countExecutedShellCommands(entries: ShellCommandEntry[]): number {
  return entries.filter((entry) => EXECUTED_SHELL_COMMAND_STATUSES.has(entry.status)).length;
}
