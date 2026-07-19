import type { AgentActivityItem, TaskBoardSnapshot } from "../types";
import { useI18n } from "../hooks/useI18n";
import { AgentActivityPanel } from "./AgentActivityPanel";
import { GrillPhasePanel, type GrillPhaseState } from "./GrillPhasePanel";
import { TaskBoardPanel } from "./TaskBoardPanel";
import { shouldShowTaskBoard } from "../utils/taskBoard";

interface ChatWorkflowSidebarProps {
  grillActive: boolean;
  grillPhase: GrillPhaseState | null;
  taskBoard: TaskBoardSnapshot | null;
  agentActivities: AgentActivityItem[];
  testEnabled: boolean;
}

function WorkflowPlaceholder({ label }: { label: string }) {
  return <p className="workflow-sidebar-empty">{label}</p>;
}

function EmptyGrillSection({ label }: { label: string }) {
  const { t } = useI18n();
  return (
    <section className="grill-phase-panel grill-phase-panel--embedded" aria-label={t("grill.title")}>
      <header className="grill-phase-header">
        <h3>{t("grill.title")}</h3>
      </header>
      <WorkflowPlaceholder label={label} />
    </section>
  );
}

function EmptyTaskBoardSection({ label }: { label: string }) {
  const { t } = useI18n();
  return (
    <section className="task-board-panel task-board-panel--embedded" aria-label={t("taskBoard.title")}>
      <div className="task-board-header">
        <span className="task-board-title">{t("taskBoard.title")}</span>
      </div>
      <WorkflowPlaceholder label={label} />
    </section>
  );
}

export function ChatWorkflowSidebar({
  grillActive,
  grillPhase,
  taskBoard,
  agentActivities,
  testEnabled,
}: ChatWorkflowSidebarProps) {
  const { t } = useI18n();

  const grillContent = grillActive
    ? grillPhase
      ? <GrillPhasePanel state={grillPhase} embedded testEnabled={testEnabled} />
      : <EmptyGrillSection label={t("workflowSidebar.grillWaiting")} />
    : null;

  const taskPlanContent = shouldShowTaskBoard(taskBoard)
    ? <TaskBoardPanel snapshot={taskBoard!} embedded />
    : <EmptyTaskBoardSection label={t("workflowSidebar.taskPlanEmpty")} />;

  const activityContent = (
    <AgentActivityPanel
      items={agentActivities}
      embedded
      showEmpty
      emptyLabel={t("workflowSidebar.activityEmpty")}
    />
  );

  return (
    <aside className="chat-workflow-sidebar" aria-label={t("workflowSidebar.title")}>
      {grillContent ? (
        <section className="workflow-sidebar-block">
          {grillContent}
        </section>
      ) : null}
      <section className="workflow-sidebar-block">
        {taskPlanContent}
      </section>
      <section className="workflow-sidebar-block">
        {activityContent}
      </section>
    </aside>
  );
}
