import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentRole, AppSettings, Chat, ChatRunStatus, CommitNewChatPayload, LLMRoutingInfo, NewChatDraft, OrchestrationMode, ReadinessReport, SettingsSavePayload, SetupStatus } from "./types";
import { api } from "./services/api";
import { ModelReadinessBanner } from "./components/ModelReadinessBanner";
import { AboutModal } from "./components/AboutModal";
import { LanguagePicker } from "./components/LanguagePicker";
import { ModelsManagerModal } from "./components/ModelsManagerModal";
import { ChatPanel } from "./components/ChatPanel";
import { SettingsModal } from "./components/SettingsModal";
import { SetupWizard } from "./components/SetupWizard";
import { Sidebar } from "./components/Sidebar";
import { hasStoredLocale, useI18n } from "./hooks/useI18n";
import { resolveLocale, type Locale } from "./i18n";
import { useTheme } from "./hooks/useTheme";
import { useSidebarResize } from "./hooks/useSidebarResize";
import {
  DEFAULT_MULTI_ROLES,
  DEFAULT_SINGLE_ROLE,
} from "./constants/roles";

export default function App() {
  const { t, locale, setLocale } = useI18n();
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [roles, setRoles] = useState<AgentRole[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [modelsOpen, setModelsOpen] = useState(false);
  const [routing, setRouting] = useState<LLMRoutingInfo | null>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [languagePickerOpen, setLanguagePickerOpen] = useState(() => !hasStoredLocale());
  const [newChatDraft, setNewChatDraft] = useState<NewChatDraft | null>(null);
  const newChatDraftRef = useRef<NewChatDraft | null>(null);
  const [defaultMode, setDefaultMode] = useState<OrchestrationMode>("multi");
  const [backendOnline, setBackendOnline] = useState(false);
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [readinessBusy, setReadinessBusy] = useState(false);
  const [readinessExpanded, setReadinessExpanded] = useState(false);
  const [chatRunStatus, setChatRunStatus] = useState<Record<string, ChatRunStatus>>({});
  const setupDismissedRef = useRef(false);
  const languagePickerOpenRef = useRef(languagePickerOpen);
  languagePickerOpenRef.current = languagePickerOpen;
  const { theme, setTheme } = useTheme();
  const { width, startResize } = useSidebarResize();

  const activeChat = chats.find((c) => c.id === activeChatId) ?? null;

  useEffect(() => {
    newChatDraftRef.current = newChatDraft;
  }, [newChatDraft]);

  const loadBackendData = useCallback(async () => {
    const results = await Promise.allSettled([
      api.listChats(),
      api.listRoles(),
      api.getSettings(),
      api.getLLMRouting(),
      api.getSetupStatus(),
    ]);

    const [chatListResult, roleListResult, appSettingsResult, routingInfoResult, setupResult] =
      results;

    if (chatListResult.status === "fulfilled") {
      const chatList = chatListResult.value;
      setChats(chatList);
      setActiveChatId((current) => {
        if (current) {
          return current;
        }
        if (newChatDraftRef.current) {
          return null;
        }
        return chatList.length > 0 ? chatList[0].id : null;
      });
    }

    if (roleListResult.status === "fulfilled") {
      setRoles(roleListResult.value);
    }

    if (appSettingsResult.status === "fulfilled") {
      const appSettings = appSettingsResult.value;
      setSettings(appSettings);
      if (appSettings.ui_language && hasStoredLocale()) {
        setLocale(resolveLocale(appSettings.ui_language));
      }
    }

    if (routingInfoResult.status === "fulfilled") {
      setRouting(routingInfoResult.value);
    }

    if (setupResult.status === "fulfilled") {
      const setup = setupResult.value;
      setSetupStatus(setup);
      if (!setupDismissedRef.current && !languagePickerOpenRef.current) {
        setSetupOpen(setup.should_show_wizard);
      }
    }
  }, [setLocale]);

  const checkReadiness = useCallback(async () => {
    if (!backendOnline) {
      return;
    }
    setReadinessBusy(true);
    try {
      const report = await api.getReadiness(false);
      setReadiness(report);
      if (report.chat_ready) {
        setReadinessExpanded(false);
      }
    } catch {
      setReadiness(null);
    } finally {
      setReadinessBusy(false);
    }
  }, [backendOnline]);

  const refresh = useCallback(async () => {
    try {
      await api.health();
      setBackendOnline(true);
      await loadBackendData();
    } catch {
      setBackendOnline(false);
      setReadiness(null);
    }
  }, [loadBackendData]);

  useEffect(() => {
    if (backendOnline) {
      void checkReadiness();
    }
  }, [backendOnline, settings?.ollama_base_url, settings?.default_model, checkReadiness]);

  useEffect(() => {
    void refresh();
    const interval = setInterval(() => void refresh(), 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleNewChat = (mode: OrchestrationMode) => {
    setDefaultMode(mode);
    setNewChatDraft({
      mode,
      execution_strategy: "auto",
      role_ids:
        mode === "multi"
          ? [...DEFAULT_MULTI_ROLES]
          : mode === "quick"
            ? []
            : [DEFAULT_SINGLE_ROLE],
      memory: {
        enabled: true,
        memory_tokens: settings?.default_memory_tokens ?? 32000,
        memory_scope: "chat",
      },
    });
    setActiveChatId(null);
  };

  const handleSelectChat = (id: string) => {
    setNewChatDraft(null);
    setActiveChatId(id);
    setChatRunStatus((prev) => {
      if (prev[id] !== "completed") {
        return prev;
      }
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const handleChatRunStateChange = (chatId: string, status: ChatRunStatus | "idle") => {
    setChatRunStatus((prev) => {
      if (status === "idle") {
        if (!(chatId in prev)) {
          return prev;
        }
        const next = { ...prev };
        delete next[chatId];
        return next;
      }
      if (prev[chatId] === status) {
        return prev;
      }
      return { ...prev, [chatId]: status };
    });
  };

  const commitNewChatDraft = async (payload: CommitNewChatPayload): Promise<Chat> => {
    if (!newChatDraft) {
      throw new Error("No chat draft available.");
    }

    const chat = await api.createChat({
      mode: newChatDraft.mode,
      execution_strategy: payload.execution_strategy,
      role_ids: payload.role_ids,
      memory: payload.memory,
      title: "New Chat",
    });
    setChats((prev) => [chat, ...prev]);
    setActiveChatId(chat.id);
    setNewChatDraft(null);
    return chat;
  };

  const handleDeleteChat = async (id: string) => {
    await api.deleteChat(id);
    setChats((prev) => prev.filter((c) => c.id !== id));
    setChatRunStatus((prev) => {
      if (!(id in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[id];
      return next;
    });
    if (activeChatId === id) {
      setActiveChatId(null);
    }
  };

  const handleRenameChat = async (id: string, title: string) => {
    const updated = await api.updateChat(id, { title });
    setChats((prev) => prev.map((chat) => (chat.id === id ? updated : chat)));
  };

  const handleSaveSettings = async (
    data: SettingsSavePayload,
  ) => {
    const updated = await api.updateSettings(data);
    setSettings(updated);
    if (updated.ui_language) {
      setLocale(resolveLocale(updated.ui_language));
    }
    setRoles(await api.listRoles());
    setRouting(await api.getLLMRouting());
    await checkReadiness();
  };

  const handleLanguageChange = async (nextLocale: Locale) => {
    setLocale(nextLocale);
    if (!backendOnline) {
      return;
    }
    try {
      const updated = await api.updateSettings({ ui_language: nextLocale });
      setSettings(updated);
      const roleList = await api.listRoles();
      setRoles(roleList);
    } catch {
      // Keep the locally selected locale even if persistence fails temporarily.
    }
  };

  const handleLanguagePick = async (nextLocale: Locale) => {
    setLocale(nextLocale);
    setLanguagePickerOpen(false);
    try {
      if (backendOnline) {
        const updated = await api.updateSettings({ ui_language: nextLocale });
        setSettings(updated);
        setRoles(await api.listRoles());
        const setup = await api.getSetupStatus();
        setSetupStatus(setup);
        if (!setupDismissedRef.current && setup.should_show_wizard) {
          setSetupOpen(true);
        }
      }
    } catch {
      // Language is already stored locally; setup can continue on the next refresh.
    }
  };

  const handleResumeSetup = async () => {
    setupDismissedRef.current = false;
    const status = await api.resumeSetup();
    setSetupStatus(status);
    setSetupOpen(true);
  };

  const handleSetupClose = async () => {
    setupDismissedRef.current = true;
    setSetupOpen(false);
    setSetupStatus(await api.getSetupStatus());
  };

  const handleSetupComplete = async () => {
    setupDismissedRef.current = true;
    setSetupOpen(false);
    setSetupStatus(await api.getSetupStatus());
    await refresh();
  };

  if (!backendOnline) {
    return (
      <div className="offline-screen">
        <h1>{t("app.title")}</h1>
        <p>{t("app.backendOffline")}</p>
        <p>{t("app.startHint")}</p>
        <code>python3 run.py</code>
        <button type="button" onClick={() => void refresh()}>
          {t("app.reconnect")}
        </button>
      </div>
    );
  }

  return (
    <div className="app">
      <LanguagePicker open={languagePickerOpen} onSelect={(next) => void handleLanguagePick(next)} />
      <div className="sidebar-shell" style={{ width }}>
        <Sidebar
          chats={chats}
          activeChatId={activeChatId}
          chatRunStatus={chatRunStatus}
          mode={defaultMode}
          onSelectChat={handleSelectChat}
          onNewChat={handleNewChat}
          onDeleteChat={handleDeleteChat}
          onRenameChat={handleRenameChat}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenAbout={() => setAboutOpen(true)}
          onOpenModels={() => setModelsOpen(true)}
          showSetupResume={Boolean(setupStatus?.can_resume && !setupStatus.completed)}
          onResumeSetup={() => void handleResumeSetup()}
        />
      </div>
      <div
        className="sidebar-resizer"
        onMouseDown={startResize}
        role="separator"
        aria-orientation="vertical"
        aria-label={t("app.sidebarResize")}
        title={t("app.sidebarResize")}
      />
      <div className="chat-shell">
        <ModelReadinessBanner
          report={readiness}
          busy={readinessBusy}
          expanded={readinessExpanded}
          onToggleDetails={() => setReadinessExpanded((current) => !current)}
          onRecheck={() => void checkReadiness()}
          onOpenSettings={() => setSettingsOpen(true)}
        />
        <ChatPanel
          chat={activeChat}
          draft={newChatDraft}
          roles={roles}
          defaultMemoryTokens={settings?.default_memory_tokens ?? 32000}
          chatBlockedReason={
            readiness && !readiness.chat_ready
              ? readiness.blocking_message || readiness.summary
              : null
          }
          onCommitDraft={commitNewChatDraft}
          onChatUpdated={(updated) => {
            setChats((prev) =>
              prev.map((c) => (c.id === updated.id ? updated : c)),
            );
          }}
          onChatRunStateChange={handleChatRunStateChange}
        />
      </div>
      <SettingsModal
        settings={settings}
        roles={roles}
        theme={theme}
        locale={locale}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSave={handleSaveSettings}
        onThemeChange={setTheme}
        onLanguageChange={(next) => void handleLanguageChange(next)}
        routing={routing}
        onOpenModels={() => {
          setSettingsOpen(false);
          setModelsOpen(true);
        }}
      />
      <ModelsManagerModal
        open={modelsOpen}
        routing={routing}
        onClose={() => setModelsOpen(false)}
        onUpdated={setRouting}
      />
      <AboutModal open={aboutOpen} onClose={() => setAboutOpen(false)} />
      <SetupWizard
        open={setupOpen && !languagePickerOpen}
        status={setupStatus}
        settings={settings}
        onClose={() => void handleSetupClose()}
        onComplete={() => void handleSetupComplete()}
        onSettingsChange={setSettings}
      />
    </div>
  );
}
