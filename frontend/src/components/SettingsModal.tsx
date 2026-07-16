import { useEffect, useRef, useState } from "react";
import type {
  AgentRole,
  AppSettings,
  CloudApiKeyField,
  ContextPluginInfo,
  LLMRoutingInfo,
  SettingsSavePayload,
  SetupTestReport,
} from "../types";
import type { ThemeMode } from "../hooks/useTheme";
import { LOCALES, type Locale } from "../i18n";
import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { useSettingsModalSize } from "../hooks/useSettingsModalSize";
import { DEFAULT_MEMORY_TOKENS, normalizeMemoryTokens, type MemoryTokenOption } from "../constants/memory";
import { MemoryTokenSelect } from "./MemoryTokenSelect";
import { TestResultsList } from "./TestResultsList";
import { ContextPluginsList } from "./ContextPluginsList";
import { RoleEditorPanel } from "./RoleEditorPanel";
import { api } from "../services/api";

interface SettingsModalProps {
  settings: AppSettings | null;
  roles: AgentRole[];
  onRolesChanged: (roles: AgentRole[]) => void;
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

type SettingsTabId = "general" | "models" | "cloud" | "memory" | "context" | "agents" | "security";

const SETTINGS_TABS: SettingsTabId[] = ["general", "models", "cloud", "memory", "context", "agents", "security"];

function parseCommandList(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function formatCommandList(commands: string[]): string {
  return commands.join("\n");
}

export function SettingsModal({
  settings,
  roles,
  onRolesChanged,
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
  const { modalRef, modalSizeStyle } = useSettingsModalSize(open);
  const [defaultMemoryTokens, setDefaultMemoryTokens] = useState<MemoryTokenOption>(DEFAULT_MEMORY_TOKENS);
  const [autoRouting, setAutoRouting] = useState(true);
  const [testInference, setTestInference] = useState(true);
  const [modelTestReport, setModelTestReport] = useState<SetupTestReport | null>(null);
  const [modelTestBusy, setModelTestBusy] = useState(false);
  const [modelTestError, setModelTestError] = useState("");
  const [contextPlugins, setContextPlugins] = useState<ContextPluginInfo[]>([]);
  const [enabledContextPlugins, setEnabledContextPlugins] = useState<string[]>([]);
  const [contextPluginsError, setContextPluginsError] = useState("");
  const [activeTab, setActiveTab] = useState<SettingsTabId>("general");
  const [commandWhitelist, setCommandWhitelist] = useState("");
  const [commandBlacklist, setCommandBlacklist] = useState("");

  useEffect(() => {
    if (open) {
      setActiveTab("general");
    }
  }, [open]);

  useEffect(() => {
    if (settings) {
      setDefaultMemoryTokens(normalizeMemoryTokens(settings.default_memory_tokens));
      setAutoRouting(settings.llm_auto_routing);
      setCommandWhitelist(formatCommandList(settings.command_whitelist));
      setCommandBlacklist(formatCommandList(settings.command_blacklist));
    }
  }, [settings]);

  useEffect(() => {
    if (!open) {
      setModelTestReport(null);
      setModelTestError("");
      setModelTestBusy(false);
      return;
    }

    void api.getContextCatalog()
      .then((catalog) => {
        setContextPlugins(catalog.plugins);
        setEnabledContextPlugins(catalog.enabled);
        setContextPluginsError("");
      })
      .catch(() => {
        setContextPlugins([]);
        setEnabledContextPlugins([]);
        setContextPluginsError(t("settings.contextPluginsLoadError"));
      });
  }, [open, t]);

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
      command_whitelist: parseCommandList(commandWhitelist),
      command_blacklist: parseCommandList(commandBlacklist),
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
      <div
        ref={modalRef}
        className="modal settings-modal"
        style={modalSizeStyle}
        onClick={(e) => e.stopPropagation()}
        title={t("settings.resizeHint")}
      >
        <h2>{t("settings.title")}</h2>
        <form ref={formRef} className="modal-form" onSubmit={handleSubmit}>
          <div
            className="settings-tabs"
            role="tablist"
            aria-label={t("settings.title")}
          >
            {SETTINGS_TABS.map((tabId) => (
              <button
                key={tabId}
                type="button"
                role="tab"
                id={`settings-tab-${tabId}`}
                aria-selected={activeTab === tabId}
                aria-controls={`settings-panel-${tabId}`}
                className={`settings-tab${activeTab === tabId ? " settings-tab--active" : ""}`}
                onClick={() => setActiveTab(tabId)}
              >
                {t(`settings.tabs.${tabId}`)}
              </button>
            ))}
          </div>
          <div className="modal-body">
          <div
            id="settings-panel-general"
            role="tabpanel"
            aria-labelledby="settings-tab-general"
            hidden={activeTab !== "general"}
            className="settings-tab-panel"
          >
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
            {t("settings.appearance")}
            <select
              value={theme}
              onChange={(event) => onThemeChange(event.target.value as ThemeMode)}
            >
              <option value="dark">{t("settings.darkMode")}</option>
              <option value="light">{t("settings.lightMode")}</option>
            </select>
          </label>
          <label>
            {t("settings.workspaceRoot")}
            <input
              name="workspace_root"
              defaultValue={settings.workspace_root}
            />
          </label>
          </div>
          <div
            id="settings-panel-models"
            role="tabpanel"
            aria-labelledby="settings-tab-models"
            hidden={activeTab !== "models"}
            className="settings-tab-panel"
          >
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
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={autoRouting}
              onChange={(event) => setAutoRouting(event.target.checked)}
            />
            {t("settings.autoRouting")}
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
          </div>
          <div
            id="settings-panel-cloud"
            role="tabpanel"
            aria-labelledby="settings-tab-cloud"
            hidden={activeTab !== "cloud"}
            className="settings-tab-panel"
          >
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
          </div>
          <div
            id="settings-panel-memory"
            role="tabpanel"
            aria-labelledby="settings-tab-memory"
            hidden={activeTab !== "memory"}
            className="settings-tab-panel"
          >
          <section className="settings-memory">
            <h4>{t("settings.memoryTokens")}</h4>
            <p className="settings-memory-hint">{t("settings.memoryTokensHint")}</p>
            <label>
              {t("settings.memoryTokensSize")}
              <MemoryTokenSelect
                value={defaultMemoryTokens}
                onChange={setDefaultMemoryTokens}
              />
            </label>
            <p className="settings-memory-note">{t("settings.memoryPerChatOnly")}</p>
          </section>
          </div>
          <div
            id="settings-panel-context"
            role="tabpanel"
            aria-labelledby="settings-tab-context"
            hidden={activeTab !== "context"}
            className="settings-tab-panel"
          >
          <section className="settings-context-plugins">
            <h4>{t("settings.contextPlugins")}</h4>
            <p className="settings-context-plugins-hint">{t("settings.contextPluginsHint")}</p>
            <ContextPluginsList plugins={contextPlugins} enabled={enabledContextPlugins} />
            {contextPluginsError && <p className="setup-error">{contextPluginsError}</p>}
          </section>
          </div>
          <div
            id="settings-panel-agents"
            role="tabpanel"
            aria-labelledby="settings-tab-agents"
            hidden={activeTab !== "agents"}
            className="settings-tab-panel"
          >
          <RoleEditorPanel roles={roles} onRolesChanged={onRolesChanged} />
          </div>
          <div
            id="settings-panel-security"
            role="tabpanel"
            aria-labelledby="settings-tab-security"
            hidden={activeTab !== "security"}
            className="settings-tab-panel"
          >
          <section className="settings-shell-commands">
            <h4>{t("settings.shellCommandsTitle")}</h4>
            <p className="settings-shell-commands-hint">{t("settings.shellCommandsHint")}</p>
            <div className="settings-shell-command-rules">
              <p>{t("settings.shellCommandsRulesIntro")}</p>
              <ul>
                <li>{t("settings.shellCommandsRuleWhitelist")}</li>
                <li>{t("settings.shellCommandsRuleBlacklist")}</li>
                <li>{t("settings.shellCommandsRuleApproval")}</li>
              </ul>
            </div>
            <div className="settings-shell-command-group">
              <h5>{t("settings.shellCommandsWhitelist")}</h5>
              <div className="settings-shell-command-badges settings-shell-command-badges--allowed">
                {parseCommandList(commandWhitelist).map((command) => (
                  <span key={`allowed-${command}`} className="settings-shell-command-badge">
                    {command}
                  </span>
                ))}
              </div>
              <label>
                {t("settings.shellCommandsWhitelistEdit")}
                <textarea
                  value={commandWhitelist}
                  onChange={(event) => setCommandWhitelist(event.target.value)}
                  rows={8}
                  spellCheck={false}
                />
              </label>
            </div>
            <div className="settings-shell-command-group">
              <h5>{t("settings.shellCommandsBlacklist")}</h5>
              <div className="settings-shell-command-badges settings-shell-command-badges--blocked">
                {parseCommandList(commandBlacklist).map((command) => (
                  <span key={`blocked-${command}`} className="settings-shell-command-badge">
                    {command}
                  </span>
                ))}
              </div>
              <label>
                {t("settings.shellCommandsBlacklistEdit")}
                <textarea
                  value={commandBlacklist}
                  onChange={(event) => setCommandBlacklist(event.target.value)}
                  rows={8}
                  spellCheck={false}
                />
              </label>
            </div>
          </section>
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
