import type { ReadinessReport } from "../types";
import { useI18n } from "../hooks/useI18n";
import { TestResultsList } from "./TestResultsList";

interface ModelReadinessBannerProps {
  report: ReadinessReport | null;
  busy: boolean;
  expanded: boolean;
  onToggleDetails: () => void;
  onRecheck: () => void;
  onOpenSettings: () => void;
}

export function ModelReadinessBanner({
  report,
  busy,
  expanded,
  onToggleDetails,
  onRecheck,
  onOpenSettings,
}: ModelReadinessBannerProps) {
  const { t } = useI18n();

  if (!report || report.chat_ready) {
    return null;
  }

  const setupReport = {
    all_required_ok: false,
    results: report.results,
    summary: report.summary,
    optional_issues: report.results.filter((item) => item.ok === false || item.warning).length,
  };

  return (
    <section className="model-readiness-banner" aria-live="polite">
      <div className="model-readiness-banner-main">
        <div>
          <strong>{t("readiness.title")}</strong>
          <p>{report.blocking_message || report.summary}</p>
          {report.active_model && (
            <p className="model-readiness-model">
              {t("readiness.activeModel", { model: report.active_model })}
            </p>
          )}
        </div>
        <div className="model-readiness-actions">
          <button type="button" onClick={onRecheck} disabled={busy}>
            {busy ? t("readiness.checking") : t("readiness.recheck")}
          </button>
          <button type="button" onClick={onOpenSettings}>
            {t("readiness.openSettings")}
          </button>
          <button type="button" onClick={onToggleDetails}>
            {expanded ? t("readiness.hideDetails") : t("readiness.showDetails")}
          </button>
        </div>
      </div>
      {expanded && (
        <div className="model-readiness-details">
          <TestResultsList report={setupReport} />
        </div>
      )}
    </section>
  );
}
