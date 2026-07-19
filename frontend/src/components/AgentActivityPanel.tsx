import type { AgentActivityItem } from "../types";
import { useI18n } from "../hooks/useI18n";
import { AgentRunningClock } from "./AgentRunningClock";

interface AgentActivityPanelProps {
  items: AgentActivityItem[];
  embedded?: boolean;
  showEmpty?: boolean;
  emptyLabel?: string;
}

function statusLabelKey(status: AgentActivityItem["status"]): string {
  return `agentActivity.status.${status}`;
}

export function AgentActivityPanel({
  items,
  embedded = false,
  showEmpty = false,
  emptyLabel = "",
}: AgentActivityPanelProps) {
  const { t } = useI18n();

  if (items.length === 0 && !showEmpty) {
    return null;
  }

  return (
    <section
      className={`agent-activity-panel${embedded ? " agent-activity-panel--embedded" : ""}`}
      aria-label={t("agentActivity.title")}
    >
      <div className="agent-activity-header">
        <span className="agent-activity-title">{t("agentActivity.title")}</span>
      </div>
      {items.length === 0 ? (
        <p className="workflow-sidebar-empty">{emptyLabel}</p>
      ) : (
      <ul className="agent-activity-list">
        {items.map((item) => (
          <li
            key={item.id}
            className={`agent-activity-item agent-activity-item--${item.status}`}
            data-status={item.status}
          >
            <span className="agent-activity-bullet" aria-hidden="true">
              {item.status === "running" ? "•" : item.status === "done" ? "✓" : "✗"}
            </span>
            <div className="agent-activity-body">
              <div className="agent-activity-line">
                <strong className="agent-activity-agent">{item.agentName}</strong>
                <span className="agent-activity-separator">—</span>
                <span className="agent-activity-description">{item.description}</span>
              </div>
              <div className="agent-activity-meta">
                <span className={`agent-activity-status agent-activity-status--${item.status}`}>
                  {item.status === "running" && (
                    <AgentRunningClock title={t("agentActivity.status.running")} />
                  )}
                  {t(statusLabelKey(item.status))}
                </span>
              </div>
            </div>
          </li>
        ))}
      </ul>
      )}
    </section>
  );
}
