import type { ApprovalRequest } from "../types";
import { useI18n } from "../hooks/useI18n";

interface ApprovalPanelProps {
  approvals: ApprovalRequest[];
  onRespond: (id: string, approved: boolean) => void;
}

export function ApprovalPanel({ approvals, onRespond }: ApprovalPanelProps) {
  const { t } = useI18n();

  if (approvals.length === 0) {
    return null;
  }

  return (
    <div className="approval-panel">
      <h3>{t("approval.title")}</h3>
      {approvals.map((approval) => (
        <div key={approval.id} className="approval-item">
          <p>{approval.description}</p>
          <div className="approval-actions">
            <button
              type="button"
              className="btn-approve"
              onClick={() => onRespond(approval.id, true)}
            >
              {t("approval.approve")}
            </button>
            <button
              type="button"
              className="btn-deny"
              onClick={() => onRespond(approval.id, false)}
            >
              {t("approval.deny")}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
