import { useEffect, useRef, useState } from "react";
import type {
  AgentRole,
  AppSettings,
  CloudApiKeyField,
  LLMRoutingInfo,
  SettingsSavePayload,
  SetupTestReport,
} from "../types";
import type { ThemeMode } from "../hooks/useTheme";
import { LOCALES, type Locale } from "../i18n";
import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { DEFAULT_MEMORY_TOKENS, normalizeMemoryTokens, type MemoryTokenOption } from "../constants/memory";
import { MemoryTokenSelect } from "./MemoryTokenSelect";
import { TestResultsList } from "./TestResultsList";
import { api } from "../services/api";

interface SettingsModalProps {
  settings: AppSettings | null;
  roles: AgentRole[];
  theme: ThemeMode;
  locale: Locale;
  open: boolean;
  onClose: () => void;
  onSave: (data: SettingsSavePayload) => void;
  onThemeChange: (theme: ThemeMode) => void;
  onLanguageChange: (locale: Locale) => void;
  routing: LLMRoutingInfo | null;
  onOpenModels: () => void;
}

const CLOUD_KEY_FIELDS: Array<{
  field: CloudApiKeyField;
  labelKey: "settings.openaiKey" | "settings.anthropicKey" | "settings.geminiKey" | "settings.groqKey" | "settings.mistralKey";
  hasKeyField: keyof AppSettings;
  placeholder: string;
}> = [
  { field: "openai_api_key", labelKey: "settings.openaiKey", hasKeyField: "has_openai_key", placeholder: "sk-..." },
  { field: "anthropic_api_key", labelKey: "settings.anthropicKey", hasKeyField: "has_anthropic_key", placeholder: "sk-ant-..." },
  { field: "gemini_api_key", labelKey: "settings.geminiKey", hasKeyField: "has_gemini_key", placeholder: "AI..." },
  { field: "groq_api_key", labelKey: "settings.groqKey", hasKeyField: "has_groq_key", placeholder: "gsk_..." },
  { field: "mistral_api_key", labelKey: "settings.mistralKey", hasKeyField: "has_mistral_key", placeholder: "..." },
];

export function SettingsModal({
  settings,
  roles,
  theme,
  locale,
  open,
  onClose,
  onSave,
  onThemeChange,
  onLanguageChange,
  routing,
  onOpenModels,
}: SettingsModalProps) {
  const { t } = useI18n();
  const formRef = useRef<HTMLFormElement>(null);
  const [defaultMemoryTokens, setDefaultMemoryTokens] = useState<MemoryTokenOption>(DEFAULT_MEMORY_TOKENS);
  const [autoRouting, setAutoRouting] = useState(true);
  const [testInference, setTestInference] = useState(true);
  const [modelTestReport, setModelTestReport] = useState<SetupTestReport | null>(null);
  const [modelTestBusy, setModelTestBusy] = useState(false);
  const [modelTestError, setModelTestError] = useState("");

  useEffect(() => {
    if (settings) {
      setDefaultMemoryTokens(normalizeMemoryTokens(settings.default_memory_tokens));
      setAutoRouting(settings.llm_auto_routing);
    }
  }, [settings]);

  useEffect(() => {
    if (!open) {
      setModelTestReport(null);
      setModelTestError("");
      setModelTestBusy(false);
    }
  }, [open]);

  useEscapeClose(open, onClose);

  if (!open || !settings) {
    return null;
  }

  const readFormValues = () => {
    const form = formRef.current;
    if (!form) {
      return {
        workspace_root: settings.workspace_root,
        ollama_base_url: settings.ollama_base_url,
        default_model: settings.default_model,
        cloudKeys: {} as Partial<Record<CloudApiKeyField, string>>,
      };
    }
    const data = new FormData(form);
    const cloudKeys: Partial<Record<CloudApiKeyField, string>> = {};
    for (const entry of CLOUD_KEY_FIELDS) {
      const value = String(data.get(entry.field) ?? "").trim();
      if (value) {
        cloudKeys[entry.field] = value;
      }
    }
    return {
      workspace_root: String(data.get("workspace_root") ?? ""),
      ollama_base_url: String(data.get("ollama_base_url") ?? ""),
      default_model: String(data.get("default_model") ?? ""),
      cloudKeys,
    };
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload: SettingsSavePayload = {
      workspace_root: String(form.get("workspace_root") ?? ""),
      ollama_base_url: String(form.get("ollama_base_url") ?? ""),
      default_model: String(form.get("default_model") ?? ""),
      default_memory_tokens: defaultMemoryTokens,
      llm_auto_routing: autoRouting,
      ui_language: locale,
    };
    for (const entry of CLOUD_KEY_FIELDS) {
      const value = String(form.get(entry.field) ?? "").trim();
      if (value) {
        payload[entry.field] = value;
      }
    }
    onSave(payload);
    onClose();
  };

  const handleTestModels = async () => {
    setModelTestBusy(true);
    setModelTestError("");
    setModelTestReport(null);
    const values = readFormValues();
    try {
      const report = await api.testModelAccess({
        ollama_base_url: values.ollama_base_url,
        default_model: values.default_model,
        ...values.cloudKeys,
        test_inference: testInference,
      });
      setModelTestReport(report);
    } catch (error) {
      setModelTestError(error instanceof Error ? error.message : t("settings.testModels"));
    } finally {
      setModelTestBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{t("settings.title")}</h2>
        <form ref={formRef} className="modal-form" onSubmit={handleSubmit}>
          <div className="modal-body">
          <label>
            {t("settings.language")}
            <select
              value={locale}
              onChange={(event) => onLanguageChange(event.target.value as Locale)}
            >
              {LOCALES.map((entry) => (
                <option key={entry.code} value={entry.code}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("settings.workspaceRoot")}
            <input
              name="workspace_root"
              defaultValue={settings.workspace_root}
            />
          </label>
          <label>
            {t("settings.ollamaUrl")}
            <input
              name="ollama_base_url"
              defaultValue={settings.ollama_base_url}
            />
          </label>
          <label>
            {t("settings.defaultModel")}
            <input
              name="default_model"
              defaultValue={settings.default_model}
              placeholder={t("settings.defaultModelHint")}
            />
          </label>
          <section className="settings-model-test">
            <div className="settings-model-test-header">
              <div>
                <h4>{t("settings.modelAccess")}</h4>
                <p className="settings-model-test-hint">{t("settings.modelAccessHint")}</p>
              </div>
              <button
                type="button"
                className="setup-action-btn"
                onClick={() => void handleTestModels()}
                disabled={modelTestBusy}
              >
                {modelTestBusy ? t("settings.testingModels") : t("settings.testModels")}
              </button>
            </div>
            <label className="checkbox-label settings-inference-toggle">
              <input
                type="checkbox"
                checked={testInference}
                onChange={(event) => setTestInference(event.target.checked)}
                disabled={modelTestBusy}
              />
              {t("settings.testInference")}
            </label>
            {modelTestReport && (
              <>
                <p className={`setup-summary ${modelTestReport.all_required_ok ? "ok" : "fail"}`}>
                  {modelTestReport.summary}
                </p>
                <TestResultsList report={modelTestReport} />
              </>
            )}
            {modelTestError && <p className="setup-error">{modelTestError}</p>}
          </section>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={autoRouting}
              onChange={(event) => setAutoRouting(event.target.checked)}
            />
            {t("settings.autoRouting")}
          </label>
          <section className="settings-cloud-providers">
            <h4>{t("settings.cloudProviders")}</h4>
            <p className="settings-cloud-providers-hint">{t("settings.cloudProvidersHint")}</p>
            {CLOUD_KEY_FIELDS.map((entry) => (
              <label key={entry.field}>
                {t(entry.labelKey)}
                <input
                  name={entry.field}
                  type="password"
                  autoComplete="off"
                  placeholder={settings[entry.hasKeyField] ? "••••••••" : entry.placeholder}
                />
              </label>
            ))}
          </section>
          <label>
            {t("settings.memoryTokens")}
            <MemoryTokenSelect
              value={defaultMemoryTokens}
              onChange={setDefaultMemoryTokens}
            />
          </label>
          <label>
            {t("settings.appearance")}
            <select
              value={theme}
              onChange={(event) => onThemeChange(event.target.value as ThemeMode)}
            >
              <option value="dark">{t("settings.darkMode")}</option>
              <option value="light">{t("settings.lightMode")}</option>
            </select>
          </label>
          <div className="models-open-row">
            <button type="button" className="btn-primary" onClick={onOpenModels}>
              {t("settings.manageModelsRouting")}
            </button>
            {routing && (
              <span className="routing-hint">
                {t("settings.modelsHint", {
                  models: routing.models.length,
                  installed: routing.installed.length,
                })}
              </span>
            )}
          </div>
          <div className="roles-preview">
            <h4>{t("settings.availableRoles", { count: roles.length })}</h4>
            <ul>
              {roles.map((role) => (
                <li key={role.id}>
                  <strong>{role.name}</strong> — {role.description}
                </li>
              ))}
            </ul>
          </div>
          </div>
          <div className="modal-actions">
            <button type="button" onClick={onClose}>
              {t("settings.cancel")}
            </button>
            <button type="submit" className="btn-primary">
              {t("settings.save")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
