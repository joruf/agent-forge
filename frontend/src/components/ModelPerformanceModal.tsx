import type { ModelPerformanceProgress, ModelPerformanceReport, ModelPerformanceSample } from "../types";
import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { useResizableModalSize } from "../hooks/useResizableModalSize";
import { formatMessageTimestamp } from "../utils/formatMessageTimestamp";

const SPARKLINE_WIDTH = 60;
const SPARKLINE_HEIGHT = 20;

/**
 * Render a tiny inline trend line for recent throughput samples.
 *
 * @param samples Recent tokens/sec samples, oldest first
 * @returns A small SVG sparkline, or null when there's not enough history
 */
function PerformanceSparkline({ samples }: { samples: ModelPerformanceSample[] }) {
  if (samples.length < 2) {
    return null;
  }
  const values = samples.map((sample) => sample.tokens_per_second);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = SPARKLINE_WIDTH / (values.length - 1);
  const toY = (value: number) => SPARKLINE_HEIGHT - ((value - min) / range) * SPARKLINE_HEIGHT;
  const points = values.map((value, index) => `${(index * step).toFixed(1)},${toY(value).toFixed(1)}`).join(" ");
  const lastX = (values.length - 1) * step;
  const lastY = toY(values[values.length - 1]);
  const tooltip = values.map((value) => value.toFixed(1)).join(" → ");

  return (
    <svg
      className="model-performance-sparkline"
      viewBox={`0 0 ${SPARKLINE_WIDTH} ${SPARKLINE_HEIGHT}`}
      width={SPARKLINE_WIDTH}
      height={SPARKLINE_HEIGHT}
      role="img"
      aria-label={tooltip}
    >
      <title>{tooltip}</title>
      <polyline
        points={points}
        fill="none"
        stroke="var(--text-muted)"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={lastX} cy={lastY} r="2" fill="var(--accent)" />
    </svg>
  );
}

interface ModelPerformanceModalProps {
  open: boolean;
  report: ModelPerformanceReport | null;
  busy: boolean;
  progress: ModelPerformanceProgress | null;
  error: string;
  onClose: () => void;
  onRefresh: () => void;
}

function formatTokensPerSecond(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return value.toFixed(1);
}

function formatSourceLabel(source: string, t: (key: string) => string): string {
  if (source === "benchmark") {
    return t("modelPerformance.source.benchmark");
  }
  if (source === "runtime") {
    return t("modelPerformance.source.runtime");
  }
  return source;
}

const MODEL_PERFORMANCE_MODAL_SIZE = {
  storageKey: "agentforge-model-performance-modal-size",
  defaultWidth: 1120,
  defaultHeight: 720,
  minWidth: 760,
  minHeight: 420,
  maxWidth: 1600,
} as const;

export function ModelPerformanceModal({
  open,
  report,
  busy,
  progress,
  error,
  onClose,
  onRefresh,
}: ModelPerformanceModalProps) {
  const { t, intlLocale } = useI18n();
  const { modalRef, modalSizeStyle } = useResizableModalSize(open, MODEL_PERFORMANCE_MODAL_SIZE);
  useEscapeClose(open, onClose);

  if (!open) {
    return null;
  }

  const models = report?.models ?? [];
  const progressTotal = progress?.total ?? 0;
  const progressCompleted = progress?.completed ?? 0;
  const progressPercent = progressTotal > 0
    ? Math.min(100, Math.round((progressCompleted / progressTotal) * 100))
    : busy
      ? 8
      : 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={modalRef}
        className="modal model-performance-modal"
        style={modalSizeStyle}
        title={t("settings.resizeHint")}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header model-performance-modal-header">
          <h2>{t("modelPerformance.title")}</h2>
          <button
            type="button"
            className="command-history-close-btn"
            onClick={onClose}
            aria-label={t("modelPerformance.close")}
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          <p className="model-performance-lead">{t("modelPerformance.lead")}</p>
          {error ? <p className="setup-error">{error}</p> : null}
          {busy ? (
            <div
              className="model-performance-progress"
              role="status"
              aria-live="polite"
              aria-busy="true"
            >
              <div className="model-performance-progress-label">
                {progressTotal > 0
                  ? t("modelPerformance.progressLabel", {
                      completed: progressCompleted,
                      total: progressTotal,
                      model: progress?.current_model || "…",
                    })
                  : t("modelPerformance.progressStarting")}
              </div>
              <div
                className="model-performance-progress-bar"
                aria-hidden="true"
              >
                <div
                  className="model-performance-progress-fill"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>
          ) : null}
          {models.length === 0 && !busy ? (
            <p className="command-history-empty">{t("modelPerformance.empty")}</p>
          ) : models.length > 0 ? (
            <div className="model-performance-table-wrap">
              <table className="model-performance-table">
                <thead>
                  <tr>
                    <th>{t("modelPerformance.columns.model")}</th>
                    <th>{t("modelPerformance.columns.accessible")}</th>
                    <th>{t("modelPerformance.columns.speed")}</th>
                    <th>{t("modelPerformance.columns.source")}</th>
                    <th>{t("modelPerformance.columns.updated")}</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((entry) => (
                    <tr key={entry.model}>
                      <td>
                        <strong>{entry.display_name || entry.model}</strong>
                        <div className="model-performance-model-id">{entry.model}</div>
                      </td>
                      <td>
                        <span
                          className={`model-performance-status model-performance-status--${
                            entry.accessible ? "yes" : "no"
                          }`}
                        >
                          {entry.accessible
                            ? t("modelPerformance.accessibleYes")
                            : t("modelPerformance.accessibleNo")}
                        </span>
                      </td>
                      <td>
                        <div className="model-performance-speed-cell">
                          <span>{formatTokensPerSecond(entry.tokens_per_second)}</span>
                          <PerformanceSparkline samples={entry.recent_samples ?? []} />
                        </div>
                      </td>
                      <td>{formatSourceLabel(entry.source, t)}</td>
                      <td>
                        {entry.last_measured_at
                          ? formatMessageTimestamp(entry.last_measured_at, intlLocale)
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
        <div className="modal-actions model-performance-actions">
          <p className="model-performance-benchmark-hint">{t("modelPerformance.benchmarkHint")}</p>
          <div className="model-performance-action-buttons">
            <button type="button" className="setup-action-btn" onClick={onRefresh} disabled={busy}>
              {busy ? t("modelPerformance.refreshing") : t("modelPerformance.refresh")}
            </button>
            <button type="button" onClick={onClose} disabled={busy}>
              {t("modelPerformance.close")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
