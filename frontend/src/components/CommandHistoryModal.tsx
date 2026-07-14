import type { ShellCommandEntry } from "../types";
import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { formatMessageTimestamp } from "../utils/formatMessageTimestamp";

interface CommandHistoryModalProps {
  open: boolean;
  entries: ShellCommandEntry[];
  onClose: () => void;
}

export function CommandHistoryModal({ open, entries, onClose }: CommandHistoryModalProps) {
  const { t, intlLocale } = useI18n();
  useEscapeClose(open, onClose);

  if (!open) {
    return null;
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal command-history-modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header command-history-modal-header">
          <h2>{t("shellCommands.title")}</h2>
          <button
            type="button"
            className="command-history-close-btn"
            onClick={onClose}
            aria-label={t("shellCommands.close")}
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          {entries.length === 0 ? (
            <p className="command-history-empty">{t("shellCommands.empty")}</p>
          ) : (
            <ol className="command-history-list">
              {entries.map((entry, index) => (
                <li key={entry.id} className={`command-history-item command-history-item--${entry.status}`}>
                  <div className="command-history-item-head">
                    <span className="command-history-index">{index + 1}.</span>
                    <code className="command-history-command">{entry.command}</code>
                  </div>
                  <div className="command-history-item-meta">
                    <span className={`command-history-status command-history-status--${entry.status}`}>
                      {t(`shellCommands.status.${entry.status}`)}
                    </span>
                    {entry.cwd && (
                      <span className="command-history-cwd">
                        {t("shellCommands.cwd", { cwd: entry.cwd })}
                      </span>
                    )}
                    {entry.agent_name && (
                      <span className="command-history-agent">{entry.agent_name}</span>
                    )}
                    {entry.exit_code !== null && entry.exit_code !== undefined && (
                      <span className="command-history-exit">
                        {t("shellCommands.exitCode", { code: entry.exit_code })}
                      </span>
                    )}
                    <span className="command-history-time">
                      {formatMessageTimestamp(entry.timestamp, intlLocale)}
                    </span>
                  </div>
                  {entry.output && entry.status !== "pending" && (
                    <pre className="command-history-output">{entry.output}</pre>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
        <div className="modal-actions">
          <button type="button" onClick={onClose}>
            {t("shellCommands.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
