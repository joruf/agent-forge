import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";

interface AboutModalProps {
  open: boolean;
  onClose: () => void;
}

export function AboutModal({ open, onClose }: AboutModalProps) {
  const { t } = useI18n();

  useEscapeClose(open, onClose);

  if (!open) {
    return null;
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal about-modal" onClick={(event) => event.stopPropagation()}>
        <h2>{t("about.title")}</h2>
        <div className="modal-body">
          <p className="about-lead">{t("about.lead")}</p>

          <dl className="about-details">
            <dt>{t("about.version")}</dt>
            <dd>0.1.0</dd>
            <dt>{t("about.developer")}</dt>
            <dd>Joachim Ruf</dd>
            <dt>{t("about.company")}</dt>
            <dd>Loresoft</dd>
          </dl>

          <p className="about-note">{t("about.note")}</p>
          <p className="about-note">{t("about.manualHint")}</p>
        </div>

        <div className="modal-actions">
          <a className="btn-primary about-manual-link" href="/docs/USER_MANUAL.html" target="_blank" rel="noreferrer">
            {t("about.openManual")}
          </a>
          <button type="button" onClick={onClose}>
            {t("about.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
