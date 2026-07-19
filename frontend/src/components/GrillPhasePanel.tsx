import { useI18n } from "../hooks/useI18n";

export type GrillPhase = "idea" | "clarify" | "plan" | "execute" | "test" | "done";

export interface GrillPhaseState {
  phase: GrillPhase;
  idea: string;
  questionCount: number;
  hasPlan: boolean;
  summary: string;
}

interface GrillPhasePanelProps {
  state: GrillPhaseState;
  embedded?: boolean;
  testEnabled?: boolean;
}

const PHASES: GrillPhase[] = ["idea", "clarify", "plan", "execute", "test", "done"];

const PHASE_LABEL_KEYS: Record<GrillPhase, string> = {
  idea: "grill.phases.idea",
  clarify: "grill.phases.clarify",
  plan: "grill.phases.plan",
  execute: "grill.phases.execute",
  test: "grill.phases.test",
  done: "grill.phases.done",
};

function phaseIndex(phase: GrillPhase): number {
  return PHASES.indexOf(phase);
}

export function GrillPhasePanel({
  state,
  embedded = false,
  testEnabled = true,
}: GrillPhasePanelProps) {
  const { t } = useI18n();

  const activeIndex = phaseIndex(state.phase);
  const visiblePhases = PHASES.filter((phase) => phase !== "test" || testEnabled);

  return (
    <section
      className={`grill-phase-panel${embedded ? " grill-phase-panel--embedded" : ""}`}
      aria-label={t("grill.title")}
    >
      <header className="grill-phase-header">
        <h3>{t("grill.title")}</h3>
        {state.idea ? <p className="grill-phase-idea">{state.idea}</p> : null}
      </header>
      <ol className="grill-phase-steps">
        {visiblePhases.map((phase) => {
          const index = phaseIndex(phase);
          let status = "pending";
          if (index < activeIndex) {
            status = "done";
          } else if (index === activeIndex) {
            status = "active";
          }
          return (
            <li key={phase} className={`grill-phase-step grill-phase-step--${status}`}>
              <span className="grill-phase-step-label">{t(PHASE_LABEL_KEYS[phase])}</span>
              {phase === "clarify" && state.questionCount > 0 ? (
                <span className="grill-phase-step-meta">
                  {t("grill.questionCount", { count: state.questionCount })}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
