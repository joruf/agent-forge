import type { ContextPluginRun } from "../types";
import { useI18n } from "../hooks/useI18n";
import { ExpandableText } from "./ExpandableText";
import { formatMessageTimestamp } from "../utils/formatMessageTimestamp";

interface ContextPluginLogProps {
  runs: ContextPluginRun[];
}

export function ContextPluginLog({ runs }: ContextPluginLogProps) {
  const { t, intlLocale } = useI18n();

  if (runs.length === 0) {
    return null;
  }

  return (
    <>
      {runs.map((run) => (
        <div
          key={`${run.plugin_id}-${run.timestamp}`}
          className={`message message-agent${
            run.status === "running" ? " message-loading" : ""
          }`}
        >
          <div className="message-header">
            <span>{t("context.pluginLabel", { name: run.plugin_name })}</span>
            <span className="message-meta">
              <span className="message-time">
                {formatMessageTimestamp(run.timestamp, intlLocale)}
              </span>
            </span>
          </div>
          {run.reason === "matched_user_intent" ? (
            <p className="message-plugin-reason">{t("context.reasonIntent")}</p>
          ) : run.reason === "matched_process_context" ? (
            <p className="message-plugin-reason">{t("context.reasonProcess")}</p>
          ) : null}
          {run.status === "running" && (
            <div className="message-loading-text">{t("context.loadingPlugin")}</div>
          )}
          {run.text && run.status === "ok" && (
            <ExpandableText text={run.text} previewLength={500} />
          )}
          {run.error && run.status === "error" && (
            <p className="message-user-error-text">{run.error}</p>
          )}
        </div>
      ))}
    </>
  );
}
