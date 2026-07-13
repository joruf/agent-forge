import { useEffect, useRef, useState } from "react";
import type { AgentMessage } from "../types";
import { useI18n } from "../hooks/useI18n";
import { AgentRunningClock } from "./AgentRunningClock";
import { ExpandableText } from "./ExpandableText";
import { formatMessageTimestamp } from "../utils/formatMessageTimestamp";

export interface ActiveAgentInfo {
  roleId: string;
  roleName: string;
  model?: string | null;
}

interface AgentHistoryProps {
  discussions: AgentMessage[];
  liveEvents: AgentMessage[];
  activeAgents: Map<string, ActiveAgentInfo>;
}

export function AgentHistory({
  discussions,
  liveEvents,
  activeAgents,
}: AgentHistoryProps) {
  const { t, intlLocale } = useI18n();
  const [copiedItemKey, setCopiedItemKey] = useState<string | null>(null);
  const copyFeedbackTimeoutRef = useRef<number | null>(null);
  const items = [...discussions, ...liveEvents];
  const activeAgentList = Array.from(activeAgents.values());

  useEffect(() => {
    return () => {
      if (copyFeedbackTimeoutRef.current !== null) {
        window.clearTimeout(copyFeedbackTimeoutRef.current);
      }
    };
  }, []);

  const copyItemText = async (itemKey: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }

    setCopiedItemKey(itemKey);
    if (copyFeedbackTimeoutRef.current !== null) {
      window.clearTimeout(copyFeedbackTimeoutRef.current);
    }
    copyFeedbackTimeoutRef.current = window.setTimeout(() => {
      setCopiedItemKey(null);
      copyFeedbackTimeoutRef.current = null;
    }, 900);
  };

  if (items.length === 0 && activeAgentList.length === 0) {
    return (
      <div className="agent-history empty">
        <p>{t("agentHistory.empty")}</p>
      </div>
    );
  }

  return (
    <div className="agent-history">
      <h3>{t("agentHistory.title")}</h3>

      <div className="agent-history-list">
        {items.map((item, index) => {
          const itemKey = `${item.timestamp}-${index}`;

          return (
            <div key={itemKey} className="agent-history-item">
              <div className="agent-history-meta">
                <div className="agent-history-meta-main">
                  <span className="agent-name">{item.from_agent}</span>
                  {item.to_agent && (
                    <span className="agent-arrow">→ {item.to_agent}</span>
                  )}
                </div>
                <span className="agent-history-actions">
                  <span className="agent-time">
                    {formatMessageTimestamp(item.timestamp, intlLocale)}
                  </span>
                  <button
                    type="button"
                    className={`message-copy-btn${
                      copiedItemKey === itemKey ? " message-copy-btn--copied" : ""
                    }`}
                    title={t("chat.copyMessage")}
                    aria-label={t("chat.copyMessage")}
                    onClick={() => void copyItemText(itemKey, item.content)}
                  >
                    <svg
                      className="message-copy-icon"
                      viewBox="0 0 24 24"
                      aria-hidden="true"
                      focusable="false"
                    >
                      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                    </svg>
                  </button>
                </span>
              </div>
              <ExpandableText text={item.content} previewLength={300} />
            </div>
          );
        })}

        {activeAgentList.map((active) => (
          <div
            key={`active-${active.roleId}`}
            className="agent-history-item agent-history-item--running message-assistant"
          >
            <div className="agent-history-meta">
              <div className="agent-history-meta-main">
                <span className="agent-name">{active.roleName}</span>
                <span className="agent-arrow">→ {t("agentHistory.team")}</span>
              </div>
              <span className="agent-history-actions">
                <span className="agent-history-icon">
                  <AgentRunningClock title={t("agentHistory.running")} />
                </span>
              </span>
            </div>
            <p className="agent-history-live-text">{t("agentHistory.working")}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
