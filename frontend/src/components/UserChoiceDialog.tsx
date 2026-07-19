import { useEffect, useMemo, useState } from "react";
import type { ApprovalRequest, UserChoiceOption } from "../types";
import { useI18n } from "../hooks/useI18n";

interface UserChoiceDialogProps {
  approval: ApprovalRequest | null;
  processing?: boolean;
  onChoose: (approvalId: string, choiceId: string, comment?: string) => void;
  onDismiss: (approvalId: string) => void;
}

function readOptions(approval: ApprovalRequest): UserChoiceOption[] {
  const raw = approval.payload.options;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== "object") {
      return [];
    }
    const option = entry as Record<string, unknown>;
    const id = typeof option.id === "string" ? option.id : "";
    const label = typeof option.label === "string" ? option.label : "";
    if (!id || !label) {
      return [];
    }
    const parsed: UserChoiceOption = { id, label };
    if (typeof option.description === "string" && option.description) {
      parsed.description = option.description;
    }
    return [parsed];
  });
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
  grill_question: "userChoice.titles.grillQuestion",
  grill_plan_review: "userChoice.titles.grillPlanReview",
  generic: "userChoice.titles.generic",
};

const GRILL_RATIONALE_MARKERS = [
  /\n\nWhy this matters:\s*/i,
  /\n\nWarum das wichtig ist:\s*/i,
];

function splitGrillQuestion(text: string): { question: string; rationale: string | null } {
  for (const marker of GRILL_RATIONALE_MARKERS) {
    const match = marker.exec(text);
    if (match?.index !== undefined) {
      return {
        question: text.slice(0, match.index).trim(),
        rationale: text.slice(match.index + match[0].length).trim() || null,
      };
    }
  }
  return { question: text, rationale: null };
}

export function UserChoiceDialog({
  approval,
  processing = false,
  onChoose,
  onDismiss,
}: UserChoiceDialogProps) {
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

  const questionRaw =
    typeof approval.payload.question === "string"
      ? approval.payload.question
      : approval.description;
  const grillQuestion =
    kind === "grill_question" ? splitGrillQuestion(questionRaw) : null;
  const question = grillQuestion?.question ?? questionRaw;
  const questionRationale = grillQuestion?.rationale ?? null;
  const titleKey = kind && KIND_TITLE_KEYS[kind] ? KIND_TITLE_KEYS[kind] : "userChoice.title";

  const handleOptionClick = (choiceId: string) => {
    if (processing) {
      return;
    }
    if (choiceId === "custom_reply" && customInputEnabled) {
      setShowCustomInput(true);
      return;
    }
    onChoose(approval.id, choiceId);
  };

  const handleCustomSubmit = () => {
    if (processing) {
      return;
    }
    const comment = customText.trim();
    if (!comment) {
      return;
    }
    onChoose(approval.id, "custom_reply", comment);
    setCustomText("");
    setShowCustomInput(false);
  };

  const processingKey =
    kind === "grill_plan_review"
      ? "userChoice.processingPlan"
      : kind === "grill_question"
        ? "userChoice.processingQuestion"
        : "userChoice.processing";

  return (
    <div className="user-choice-overlay" role="presentation">
      <div
        className={`user-choice-dialog${processing ? " user-choice-dialog--processing" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-choice-title"
        aria-busy={processing}
      >
        <header className="user-choice-header">
          <h3 id="user-choice-title">{t(titleKey)}</h3>
          <button
            type="button"
            className="user-choice-close"
            aria-label={t("userChoice.dismiss")}
            onClick={() => onDismiss(approval.id)}
            disabled={processing}
          >
            ×
          </button>
        </header>
        <div className="user-choice-question-block">
          <p className="user-choice-question">{question}</p>
          {questionRationale ? (
            <p className="user-choice-question-rationale">
              <span className="user-choice-question-rationale-label">
                {t("userChoice.whyThisMatters")}
              </span>
              {questionRationale}
            </p>
          ) : null}
        </div>
        {processing ? (
          <div className="user-choice-processing" role="status">
            <span className="user-choice-processing-spinner" aria-hidden="true" />
            <p>{t(processingKey)}</p>
          </div>
        ) : showCustomInput && customInputEnabled ? (
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
            {options.map((option) => {
              const actionLabel =
                option.id === "accept_recommended"
                  ? t("userChoice.optionAcceptRecommended")
                  : option.id === "custom_reply"
                    ? t("userChoice.optionCustomReply")
                    : option.id === "abort"
                      ? t("userChoice.optionAbort")
                      : option.id === "approve_plan"
                        ? t("userChoice.optionApprovePlan")
                        : option.label;
              const hasPrimaryDescription =
                Boolean(option.description)
                && (option.id === "accept_recommended" || option.id === "approve_plan");

              return (
              <button
                key={option.id}
                type="button"
                className={`user-choice-option${
                  option.id === "abort" ? " user-choice-option--danger" : ""
                }${hasPrimaryDescription ? " user-choice-option--primary-description" : ""}`}
                onClick={() => handleOptionClick(option.id)}
              >
                {hasPrimaryDescription ? (
                  <>
                    <span className="user-choice-option-description user-choice-option-description--primary">
                      {option.description}
                    </span>
                    <span className="user-choice-option-label user-choice-option-label--action">
                      {actionLabel}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="user-choice-option-label">{actionLabel}</span>
                    {option.description ? (
                      <span className="user-choice-option-description">{option.description}</span>
                    ) : null}
                  </>
                )}
              </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
