import { useEffect, useMemo, useState } from "react";
import type { ApprovalRequest, UserChoiceOption } from "../types";
import { useI18n } from "../hooks/useI18n";

interface UserChoiceDialogProps {
  approval: ApprovalRequest | null;
  onChoose: (approvalId: string, choiceId: string, comment?: string) => void;
  onDismiss: (approvalId: string) => void;
}

function readOptions(approval: ApprovalRequest): UserChoiceOption[] {
  const raw = approval.payload.options;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .map((entry) => {
      if (!entry || typeof entry !== "object") {
        return null;
      }
      const option = entry as Record<string, unknown>;
      const id = typeof option.id === "string" ? option.id : "";
      const label = typeof option.label === "string" ? option.label : "";
      if (!id || !label) {
        return null;
      }
      return {
        id,
        label,
        description: typeof option.description === "string" ? option.description : "",
      };
    })
    .filter((option): option is UserChoiceOption => option !== null);
}

function readKind(approval: ApprovalRequest): string | null {
  const kind = approval.payload.kind;
  return typeof kind === "string" && kind.trim() ? kind : null;
}

function allowsCustomInput(approval: ApprovalRequest, options: UserChoiceOption[]): boolean {
  if (approval.payload.allows_custom_input === true) {
    return true;
  }
  return options.some((option) => option.id === "custom_reply");
}

const KIND_TITLE_KEYS: Record<string, string> = {
  missing_content_tag: "userChoice.titles.missingContentTag",
  agent_blocked: "userChoice.titles.agentBlocked",
  agent_question: "userChoice.titles.agentQuestion",
  workflow_incomplete: "userChoice.titles.workflowIncomplete",
  generic: "userChoice.titles.generic",
};

export function UserChoiceDialog({ approval, onChoose, onDismiss }: UserChoiceDialogProps) {
  const { t } = useI18n();
  const [customText, setCustomText] = useState("");
  const [showCustomInput, setShowCustomInput] = useState(false);

  useEffect(() => {
    setCustomText("");
    setShowCustomInput(false);
  }, [approval?.id]);

  const options = useMemo(
    () => (approval ? readOptions(approval) : []),
    [approval],
  );
  const kind = approval ? readKind(approval) : null;
  const customInputEnabled = approval ? allowsCustomInput(approval, options) : false;

  if (!approval) {
    return null;
  }

  const question =
    typeof approval.payload.question === "string"
      ? approval.payload.question
      : approval.description;
  const titleKey = kind && KIND_TITLE_KEYS[kind] ? KIND_TITLE_KEYS[kind] : "userChoice.title";

  const handleOptionClick = (choiceId: string) => {
    if (choiceId === "custom_reply" && customInputEnabled) {
      setShowCustomInput(true);
      return;
    }
    onChoose(approval.id, choiceId);
  };

  const handleCustomSubmit = () => {
    const comment = customText.trim();
    if (!comment) {
      return;
    }
    onChoose(approval.id, "custom_reply", comment);
    setCustomText("");
    setShowCustomInput(false);
  };

  return (
    <div className="user-choice-overlay" role="presentation">
      <div
        className="user-choice-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-choice-title"
      >
        <header className="user-choice-header">
          <h3 id="user-choice-title">{t(titleKey)}</h3>
          <button
            type="button"
            className="user-choice-close"
            aria-label={t("userChoice.dismiss")}
            onClick={() => onDismiss(approval.id)}
          >
            ×
          </button>
        </header>
        <p className="user-choice-question">{question}</p>
        {showCustomInput && customInputEnabled ? (
          <div className="user-choice-custom">
            <label htmlFor="user-choice-custom-input">{t("userChoice.customInputLabel")}</label>
            <textarea
              id="user-choice-custom-input"
              className="user-choice-custom-input"
              rows={4}
              value={customText}
              onChange={(event) => setCustomText(event.target.value)}
              placeholder={t("userChoice.customInputPlaceholder")}
            />
            <div className="user-choice-custom-actions">
              <button
                type="button"
                className="user-choice-option"
                onClick={handleCustomSubmit}
                disabled={!customText.trim()}
              >
                {t("userChoice.customSubmit")}
              </button>
              <button
                type="button"
                className="user-choice-close"
                onClick={() => {
                  setShowCustomInput(false);
                  setCustomText("");
                }}
              >
                {t("userChoice.customCancel")}
              </button>
            </div>
          </div>
        ) : (
          <div className="user-choice-options">
            {options.map((option) => (
              <button
                key={option.id}
                type="button"
                className={`user-choice-option${
                  option.id === "abort" ? " user-choice-option--danger" : ""
                }`}
                onClick={() => handleOptionClick(option.id)}
              >
                <span className="user-choice-option-label">{option.label}</span>
                {option.description ? (
                  <span className="user-choice-option-description">{option.description}</span>
                ) : null}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
