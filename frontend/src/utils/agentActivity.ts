import type { AgentActivityItem, AgentActivityStatus } from "../types";

export interface AgentActivityContext {
  t: (key: string, params?: Record<string, string | number>) => string;
  resolveRoleName: (roleId: string) => string;
}

let activitySequence = 0;

/**
 * Create a unique activity item identifier.
 *
 * @param prefix Stable prefix for the activity source
 * @return Unique activity id
 */
function createActivityId(prefix: string): string {
  activitySequence += 1;
  return `${prefix}-${Date.now()}-${activitySequence}`;
}

/**
 * Parse JSON tool arguments from a WebSocket payload.
 *
 * @param raw Raw arguments string
 * @return Parsed argument object
 */
export function parseToolArguments(raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

/**
 * Build a human-readable label for one tool invocation.
 *
 * @param tool Tool name
 * @param argumentsRaw Raw JSON arguments
 * @param t Translation function
 * @return Activity description
 */
export function describeToolCall(
  tool: string,
  argumentsRaw: string,
  t: AgentActivityContext["t"],
): string {
  const args = parseToolArguments(argumentsRaw);
  const path = typeof args.path === "string" ? args.path : "";
  const command = typeof args.command === "string" ? args.command : "";
  const query = typeof args.query === "string" ? args.query : "";

  switch (tool) {
    case "write_file":
      return path
        ? t("agentActivity.tool.writeFile", { path })
        : t("agentActivity.tool.writeFileGeneric");
    case "read_file":
      return path
        ? t("agentActivity.tool.readFile", { path })
        : t("agentActivity.tool.readFileGeneric");
    case "edit_file":
      return path
        ? t("agentActivity.tool.editFile", { path })
        : t("agentActivity.tool.editFileGeneric");
    case "list_directory":
      return path
        ? t("agentActivity.tool.listDirectory", { path })
        : t("agentActivity.tool.listDirectoryGeneric");
    case "run_command":
      return command
        ? t("agentActivity.tool.runCommand", { command })
        : t("agentActivity.tool.runCommandGeneric");
    case "search_files":
      return query
        ? t("agentActivity.tool.searchFiles", { query })
        : t("agentActivity.tool.searchFilesGeneric");
    default:
      return t("agentActivity.tool.generic", { tool });
  }
}

/**
 * Mark the latest running activity for one agent and optional tool as finished.
 *
 * @param items Current activity list
 * @param agentId Agent role id
 * @param tool Optional tool name filter
 * @param status Final status
 * @return Updated activity list
 */
function finishLatestRunningActivity(
  items: AgentActivityItem[],
  agentId: string,
  tool: string | undefined,
  status: AgentActivityStatus,
): AgentActivityItem[] {
  let matchIndex = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.status !== "running" || item.agentId !== agentId) {
      continue;
    }
    if (tool && item.tool !== tool) {
      continue;
    }
    matchIndex = index;
    break;
  }
  if (matchIndex === -1) {
    return items;
  }
  return items.map((item, index) =>
    index === matchIndex ? { ...item, status } : item,
  );
}

/**
 * Apply one WebSocket event to the live agent-activity feed.
 *
 * @param items Current activity items
 * @param event Raw WebSocket payload
 * @param context Translation and role-name helpers
 * @return Updated activity list
 */
export function applyAgentActivityEvent(
  items: AgentActivityItem[],
  event: Record<string, unknown>,
  context: AgentActivityContext,
): AgentActivityItem[] {
  const { t, resolveRoleName } = context;
  const type = String(event.type ?? "");

  if (type === "agent_start" && event.agent_id && event.agent_name) {
    return [
      ...items,
      {
        id: createActivityId("agent-start"),
        agentId: String(event.agent_id),
        agentName: String(event.agent_name),
        description: t("agentActivity.started"),
        status: "running",
      },
    ];
  }

  if (type === "agent_end" && event.agent_id) {
    const agentId = String(event.agent_id);
    return items.map((item) =>
      item.agentId === agentId && item.status === "running"
        ? { ...item, status: "done" }
        : item,
    );
  }

  if (type === "tool_call" && event.agent_id && event.tool) {
    const agentId = String(event.agent_id);
    const tool = String(event.tool);
    const agentName = event.agent_name
      ? String(event.agent_name)
      : resolveRoleName(agentId);
    return [
      ...items,
      {
        id: createActivityId(`tool-${tool}`),
        agentId,
        agentName,
        description: describeToolCall(tool, String(event.arguments ?? "{}"), t),
        status: "running",
        tool,
      },
    ];
  }

  if (type === "tool_result" && event.agent_id && event.tool) {
    return finishLatestRunningActivity(
      items,
      String(event.agent_id),
      String(event.tool),
      event.success === false ? "failed" : "done",
    );
  }

  if (type === "task_board_updated" && Array.isArray(event.steps)) {
    const steps = event.steps as Array<Record<string, unknown>>;
    const activeStep = steps.find((step) => String(step.status ?? "") === "active");
    if (!activeStep) {
      return items;
    }
    const assignee = String(activeStep.assignee ?? "developer");
    const action = String(activeStep.action ?? "analyze");
    const path = typeof activeStep.path === "string" ? activeStep.path : "";
    const detail = typeof activeStep.detail === "string" ? activeStep.detail : "";
    const actionLabel = t(`taskBoard.action.${action}`);
    const description = path
      ? `${actionLabel} — ${path}`
      : detail || actionLabel;
    const nextItem: AgentActivityItem = {
      id: `task-board-${String(activeStep.step_id ?? createActivityId("task"))}`,
      agentId: assignee,
      agentName: resolveRoleName(assignee),
      description,
      status: "running",
    };
    const existingIndex = items.findIndex(
      (item) => item.id.startsWith("task-board-") && item.status === "running",
    );
    if (existingIndex >= 0) {
      return items.map((item, index) => (index === existingIndex ? nextItem : item));
    }
    return [...items, nextItem];
  }

  if (type === "context_plugin_start" && event.plugin_id) {
    const pluginName = String(event.plugin_name ?? event.plugin_id);
    return [
      ...items,
      {
        id: createActivityId(`plugin-${String(event.plugin_id)}`),
        agentId: "context",
        agentName: t("context.pluginLabel", { name: pluginName }),
        description: t("agentActivity.contextPlugin"),
        status: "running",
      },
    ];
  }

  if (type === "context_plugin_complete" && event.plugin_id) {
    const pluginId = String(event.plugin_id);
    let matchIndex = -1;
    for (let index = items.length - 1; index >= 0; index -= 1) {
      if (items[index].id.includes(`plugin-${pluginId}`) && items[index].status === "running") {
        matchIndex = index;
        break;
      }
    }
    if (matchIndex === -1) {
      return items;
    }
    return items.map((item, index) =>
      index === matchIndex
        ? { ...item, status: event.ok === false ? "failed" : "done" }
        : item,
    );
  }

  if (type === "complete" || type === "grill_execute_complete" || type === "stopped") {
    return items.map((item) =>
      item.status === "running" ? { ...item, status: "done" } : item,
    );
  }

  return items;
}

/**
 * Return whether the activity panel should render for the current run state.
 *
 * @param items Activity items
 * @param loading Whether orchestration is active
 * @return True when the panel should be visible
 */
export function shouldShowAgentActivity(
  items: AgentActivityItem[],
  loading: boolean,
): boolean {
  if (items.length === 0) {
    return false;
  }
  return loading || items.some((item) => item.status === "running");
}
