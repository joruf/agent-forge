import { useCallback, useEffect, useState } from "react";
import type { AppSettings, SetupStatus, SetupTestReport } from "../types";
import { api } from "../services/api";
import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { TestResultsList } from "./TestResultsList";

interface SetupWizardProps {
  open: boolean;
  status: SetupStatus | null;
  settings: AppSettings | null;
  onClose: () => void;
  onComplete: () => void;
  onSettingsChange: (settings: AppSettings) => void;
}

export function SetupWizard({
  open,
  status,
  settings,
  onClose,
  onComplete,
  onSettingsChange,
}: SetupWizardProps) {
  const { t } = useI18n();
  const [step, setStep] = useState("welcome");
  const [ollamaUrl, setOllamaUrl] = useState("");
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [testReport, setTestReport] = useState<SetupTestReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [error, setError] = useState("");

  const stepLabel = (stepId: string) => t(`setup.steps.${stepId}`);

  useEffect(() => {
    if (status?.current_step) {
      setStep(status.current_step);
    }
    if (status?.last_test_results?.results) {
      setTestReport(status.last_test_results);
    }
  }, [status]);

  useEffect(() => {
    if (settings) {
      setOllamaUrl(settings.ollama_base_url);
      setWorkspaceRoot(settings.workspace_root);
    }
  }, [settings]);

  const steps = status?.steps ?? ["welcome", "ollama", "models", "openai", "workspace", "verify", "complete"];
  const stepIndex = steps.indexOf(step);
  const isLastStep = step === "complete";

  const persistStep = useCallback(async (nextStep: string) => {
    await api.updateSetupStep(nextStep);
    setStep(nextStep);
  }, []);

  const saveSettings = useCallback(async () => {
    const updated = await api.updateSettings({
      ollama_base_url: ollamaUrl,
      workspace_root: workspaceRoot,
      openai_api_key: openaiKey || undefined,
    });
    onSettingsChange(updated);
    return updated;
  }, [ollamaUrl, workspaceRoot, openaiKey, onSettingsChange]);

  const runTests = useCallback(async (testGenerate = true) => {
    setBusy(true);
    setError("");
    try {
      await saveSettings();
      const report = await api.runSetupTests({
        ollama_base_url: ollamaUrl,
        workspace_root: workspaceRoot,
        openai_api_key: openaiKey || undefined,
        test_generate: testGenerate,
      });
      setTestReport(report);
      return report;
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("setup.errors.testFailed"));
      return null;
    } finally {
      setBusy(false);
    }
  }, [ollamaUrl, workspaceRoot, openaiKey, saveSettings, t]);

  useEscapeClose(open, onClose);

  const handleSkip = async () => {
    setBusy(true);
    try {
      await api.skipSetup();
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const handleNext = async () => {
    setError("");
    const nextIndex = Math.min(stepIndex + 1, steps.length - 1);
    const nextStep = steps[nextIndex];

    setBusy(true);
    try {
      if (step === "ollama") {
        await saveSettings();
        await runTests(false);
      } else if (step === "openai" || step === "workspace") {
        await saveSettings();
      } else if (step === "verify") {
        const report = await runTests(true);
        if (!report?.all_required_ok) {
          setError(t("setup.errors.requiredFailed"));
          return;
        }
      } else if (step === "complete") {
        await api.completeSetup();
        onComplete();
        return;
      }

      await persistStep(nextStep);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("setup.errors.actionFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleBack = async () => {
    const prevIndex = Math.max(stepIndex - 1, 0);
    await persistStep(steps[prevIndex]);
  };

  const handleSyncModels = async () => {
    setBusy(true);
    setSyncMessage("");
    setError("");
    try {
      await saveSettings();
      const result = await api.setupSyncModels();
      setSyncMessage(
        result.count > 0
          ? t("setup.models.imported", { count: result.count, total: result.total })
          : t("setup.models.upToDate"),
      );
      await runTests(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("setup.errors.importFailed"));
    } finally {
      setBusy(false);
    }
  };

  if (!open || !status) {
    return null;
  }

  return (
    <div className="setup-overlay">
      <div className="setup-wizard">
        <header className="setup-header">
          <div>
            <h2>{t("setup.title")}</h2>
            <p className="setup-subtitle">
              {t("setup.stepOf", {
                current: stepIndex + 1,
                total: steps.length,
                label: stepLabel(step),
              })}
            </p>
          </div>
          <button
            type="button"
            className="setup-skip-btn"
            onClick={() => void handleSkip()}
            disabled={busy}
            title={t("setup.skip")}
          >
            {t("setup.skip")}
          </button>
        </header>

        <nav className="setup-steps" aria-label={t("setup.stepsAria")}>
          {steps.map((s, index) => (
            <span
              key={s}
              className={`setup-step-dot ${index <= stepIndex ? "active" : ""} ${s === step ? "current" : ""}`}
              title={stepLabel(s)}
            />
          ))}
        </nav>

        <div className="setup-body">
          {step === "welcome" && (
            <section className="setup-section">
              <h3>{t("setup.welcome.title")}</h3>
              <p>{t("setup.welcome.intro")}</p>
              <ul className="setup-checklist">
                <li>{t("setup.welcome.checkOllama")}</li>
                <li>{t("setup.welcome.checkRegistry")}</li>
                <li>{t("setup.welcome.checkOpenai")}</li>
                <li>{t("setup.welcome.checkWorkspace")}</li>
                <li>{t("setup.welcome.checkInference")}</li>
              </ul>
              <p className="setup-hint">{t("setup.welcome.hint")}</p>
            </section>
          )}

          {step === "ollama" && (
            <section className="setup-section">
              <h3>{t("setup.ollama.title")}</h3>
              <p>{t("setup.ollama.intro")}</p>
              <label>
                {t("setup.ollama.baseUrl")}
                <input
                  value={ollamaUrl}
                  onChange={(e) => setOllamaUrl(e.target.value)}
                  placeholder="http://192.168.1.10:11434"
                />
              </label>
              <button
                type="button"
                className="setup-action-btn"
                onClick={() => void runTests(false)}
                disabled={busy}
              >
                {t("setup.ollama.test")}
              </button>
              {testReport && <TestResultsList report={testReport} filter={["ollama"]} />}
            </section>
          )}

          {step === "models" && (
            <section className="setup-section">
              <h3>{t("setup.models.title")}</h3>
              <p>{t("setup.models.intro")}</p>
              <button
                type="button"
                className="setup-action-btn"
                onClick={() => void handleSyncModels()}
                disabled={busy}
              >
                {t("setup.models.import")}
              </button>
              {syncMessage && <p className="setup-success">{syncMessage}</p>}
              {testReport && (
                <TestResultsList report={testReport} filter={["model_registry", "ollama"]} />
              )}
            </section>
          )}

          {step === "openai" && (
            <section className="setup-section">
              <h3>{t("setup.openai.title")}</h3>
              <p>{t("setup.openai.intro")}</p>
              <label>
                {t("setup.openai.apiKey")}
                <input
                  type="password"
                  value={openaiKey}
                  onChange={(e) => setOpenaiKey(e.target.value)}
                  placeholder={settings?.has_openai_key ? t("setup.openai.alreadySet") : "sk-..."}
                />
              </label>
              <button
                type="button"
                className="setup-action-btn"
                onClick={() => void runTests(false)}
                disabled={busy}
              >
                {t("setup.openai.test")}
              </button>
              {testReport && <TestResultsList report={testReport} filter={["openai"]} />}
            </section>
          )}

          {step === "workspace" && (
            <section className="setup-section">
              <h3>{t("setup.workspace.title")}</h3>
              <p>{t("setup.workspace.intro")}</p>
              <label>
                {t("setup.workspace.path")}
                <input
                  value={workspaceRoot}
                  onChange={(e) => setWorkspaceRoot(e.target.value)}
                  placeholder="/home/user/Documents"
                />
              </label>
              <button
                type="button"
                className="setup-action-btn"
                onClick={() => void runTests(false)}
                disabled={busy}
              >
                {t("setup.workspace.test")}
              </button>
              {testReport && <TestResultsList report={testReport} filter={["workspace"]} />}
            </section>
          )}

          {step === "verify" && (
            <section className="setup-section">
              <h3>{t("setup.verify.title")}</h3>
              <p>{t("setup.verify.intro")}</p>
              <button
                type="button"
                className="setup-action-btn primary"
                onClick={() => void runTests(true)}
                disabled={busy}
              >
                {t("setup.verify.runAll")}
              </button>
              {testReport && (
                <>
                  <p className={`setup-summary ${testReport.all_required_ok ? "ok" : "fail"}`}>
                    {testReport.summary}
                    {testReport.optional_issues > 0 &&
                      t("setup.verify.optionalHints", { count: testReport.optional_issues })}
                  </p>
                  <TestResultsList report={testReport} />
                </>
              )}
            </section>
          )}

          {step === "complete" && (
            <section className="setup-section">
              <h3>{t("setup.complete.title")}</h3>
              <p>{t("setup.complete.intro")}</p>
              {testReport?.all_required_ok && (
                <p className="setup-success">{t("setup.complete.allPassed")}</p>
              )}
            </section>
          )}

          {error && <p className="setup-error">{error}</p>}
        </div>

        <footer className="setup-footer">
          <button
            type="button"
            className="setup-nav-btn"
            onClick={() => void handleBack()}
            disabled={busy || stepIndex === 0}
          >
            {t("setup.back")}
          </button>
          <button
            type="button"
            className="setup-nav-btn primary"
            onClick={() => void handleNext()}
            disabled={busy}
          >
            {isLastStep ? t("setup.finish") : t("setup.next")}
          </button>
        </footer>
      </div>
    </div>
  );
}
