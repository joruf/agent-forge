import type { TaskBoardSnapshot } from "../types";
import { useI18n } from "../hooks/useI18n";

interface TaskBoardPanelProps {
  snapshot: TaskBoardSnapshot;
  embedded?: boolean;
}

function actionLabelKey(action: string): string {
  return `taskBoard.action.${action}`;
}

export function TaskBoardPanel({ snapshot, embedded = false }: TaskBoardPanelProps) {
  const { t } = useI18n();

  return (
    <section
      className={`task-board-panel${embedded ? " task-board-panel--embedded" : ""}`}
      aria-label={t("taskBoard.title")}
      data-complete={snapshot.complete ? "true" : "false"}
    >
      <div className="task-board-header">
        <span className="task-board-title">{t("taskBoard.title")}</span>
        <span className="task-board-type">{t(`taskBoard.taskType.${snapshot.task_type}`)}</span>
        {snapshot.complete && (
          <span className="task-board-complete-badge">{t("taskBoard.complete")}</span>
        )}
      </div>
      {!snapshot.complete && snapshot.reason && (
        <p className="task-board-reason" role="status">{snapshot.reason}</p>
      )}
      <ol className="task-board-steps">
        {snapshot.steps.map((step) => (
          <li
            key={`${step.step_id}-${step.action}`}
            className={`task-board-step task-board-step-${step.status}`}
            data-status={step.status}
          >
            <span className="task-board-step-index">{step.step_id}</span>
            <div className="task-board-step-body">
              <div className="task-board-step-title">
                {t(actionLabelKey(step.action))}
                {step.path && <code className="task-board-step-path">{step.path}</code>}
              </div>
              {step.detail && (
                <div className="task-board-step-detail">{step.detail}</div>
              )}
            </div>
            <span className="task-board-step-status">
              {t(`taskBoard.status.${step.status}`)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
