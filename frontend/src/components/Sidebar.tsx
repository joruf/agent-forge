import { useEffect, useRef, useState } from "react";
import type { Chat, ChatRunStatus, OrchestrationMode } from "../types";
import { useI18n } from "../hooks/useI18n";
import { AgentRunningClock } from "./AgentRunningClock";
import { ChatCompletedCheck } from "./ChatCompletedCheck";

interface SidebarProps {
  chats: Chat[];
  activeChatId: string | null;
  chatRunStatus: Record<string, ChatRunStatus>;
  mode: OrchestrationMode;
  onSelectChat: (id: string) => void;
  onNewChat: (mode: OrchestrationMode) => void;
  onDeleteChat: (id: string) => void;
  onRenameChat: (id: string, title: string) => Promise<void>;
  onOpenSettings: () => void;
  onOpenAbout: () => void;
  onOpenModels: () => void;
  showSetupResume?: boolean;
  onResumeSetup?: () => void;
}

export function Sidebar({
  chats,
  activeChatId,
  chatRunStatus,
  mode,
  onSelectChat,
  onNewChat,
  onDeleteChat,
  onRenameChat,
  onOpenSettings,
  onOpenAbout,
  onOpenModels,
  showSetupResume,
  onResumeSetup,
}: SidebarProps) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const [editingChatId, setEditingChatId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const chatListRef = useRef<HTMLDivElement>(null);
  const skipBlurCommitRef = useRef(false);

  const scrollChatIntoView = (chatId: string) => {
    chatListRef.current
      ?.querySelector(`[data-chat-id="${chatId}"]`)
      ?.scrollIntoView({ block: "nearest" });
  };

  const selectChat = (chatId: string) => {
    onSelectChat(chatId);
    chatListRef.current?.focus({ preventScroll: true });
  };

  const handleChatListKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (menuOpen || editingChatId) {
      return;
    }

    if (event.key === "Delete" && activeChatId) {
      event.preventDefault();
      onDeleteChat(activeChatId);
      return;
    }

    if (chats.length === 0) {
      return;
    }

    const currentIndex = activeChatId
      ? chats.findIndex((chat) => chat.id === activeChatId)
      : -1;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      const nextIndex = currentIndex < 0 ? 0 : Math.min(currentIndex + 1, chats.length - 1);
      if (nextIndex === currentIndex) {
        return;
      }
      const nextChat = chats[nextIndex];
      selectChat(nextChat.id);
      scrollChatIntoView(nextChat.id);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      const nextIndex =
        currentIndex < 0 ? chats.length - 1 : Math.max(currentIndex - 1, 0);
      if (nextIndex === currentIndex) {
        return;
      }
      const nextChat = chats[nextIndex];
      selectChat(nextChat.id);
      scrollChatIntoView(nextChat.id);
    }
  };

  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (editingChatId) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [editingChatId]);

  const closeMenu = () => setMenuOpen(false);

  const startEditingTitle = (chat: Chat) => {
    setEditingChatId(chat.id);
    setEditTitle(chat.title);
  };

  const cancelEditingTitle = () => {
    setEditingChatId(null);
    setEditTitle("");
  };

  const commitEditingTitle = async (chat: Chat) => {
    if (editingChatId !== chat.id) {
      return;
    }
    const trimmed = editTitle.trim();
    setEditingChatId(null);
    setEditTitle("");
    if (!trimmed || trimmed === chat.title) {
      return;
    }
    await onRenameChat(chat.id, trimmed);
  };

  const handleOpenSettings = () => {
    closeMenu();
    onOpenSettings();
  };

  const handleOpenModels = () => {
    closeMenu();
    onOpenModels();
  };

  const handleOpenAbout = () => {
    closeMenu();
    onOpenAbout();
  };

  const handleOpenManual = () => {
    closeMenu();
    window.open("/docs/USER_MANUAL.html", "_blank", "noopener,noreferrer");
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <h1>{t("app.title")}</h1>
        <div className="sidebar-menu" ref={menuRef}>
          <button
            type="button"
            className={`sidebar-menu-trigger ${menuOpen ? "open" : ""}`}
            onClick={() => setMenuOpen((open) => !open)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={t("sidebar.openMenu")}
          >
            <span className="sidebar-menu-icon" aria-hidden="true">
              ⚙
            </span>
          </button>
          {menuOpen && (
            <div className="sidebar-menu-popup" role="menu" aria-label={t("sidebar.footerNav")}>
              <button type="button" className="sidebar-menu-item" role="menuitem" onClick={handleOpenSettings}>
                {t("sidebar.properties")}
              </button>
              <button type="button" className="sidebar-menu-item" role="menuitem" onClick={handleOpenModels}>
                {t("sidebar.manageModels")}
              </button>
              <button type="button" className="sidebar-menu-item" role="menuitem" onClick={handleOpenManual}>
                {t("sidebar.userManual")}
              </button>
              <button type="button" className="sidebar-menu-item" role="menuitem" onClick={handleOpenAbout}>
                {t("sidebar.about")}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="sidebar-actions">
        <button
          type="button"
          className={`mode-btn ${mode === "quick" ? "active" : ""}`}
          onClick={() => onNewChat("quick")}
          aria-label={t("sidebar.newQuickChat")}
          data-tooltip={t("sidebar.quickChatTooltip")}
        >
          {t("sidebar.newQuickChat")}
        </button>
        <button
          type="button"
          className={`mode-btn ${mode === "single" ? "active" : ""}`}
          onClick={() => onNewChat("single")}
          aria-label={t("sidebar.newSingleAgent")}
          data-tooltip={t("sidebar.singleAgentTooltip")}
        >
          {t("sidebar.newSingleAgent")}
        </button>
        <button
          type="button"
          className={`mode-btn ${mode === "multi" ? "active" : ""}`}
          onClick={() => onNewChat("multi")}
          aria-label={t("sidebar.newMultiAgent")}
          data-tooltip={t("sidebar.multiAgentTooltip")}
        >
          {t("sidebar.newMultiAgent")}
        </button>
      </div>

      <div
        ref={chatListRef}
        className="chat-list"
        role="listbox"
        tabIndex={0}
        aria-label={t("sidebar.chatList")}
        aria-activedescendant={activeChatId ? `chat-item-${activeChatId}` : undefined}
        onKeyDown={handleChatListKeyDown}
      >
        {chats.map((chat) => (
          <div
            key={chat.id}
            id={`chat-item-${chat.id}`}
            data-chat-id={chat.id}
            role="option"
            aria-selected={chat.id === activeChatId}
            className={`chat-item ${chat.id === activeChatId ? "active" : ""}${
              editingChatId === chat.id ? " editing" : ""
            }`}
          >
            {editingChatId === chat.id ? (
              <div className="chat-item-edit">
                <input
                  ref={titleInputRef}
                  type="text"
                  className="chat-title-input"
                  value={editTitle}
                  aria-label={t("sidebar.renameChat")}
                  onChange={(event) => setEditTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void commitEditingTitle(chat);
                    }
                    if (event.key === "Escape") {
                      event.preventDefault();
                      skipBlurCommitRef.current = true;
                      cancelEditingTitle();
                    }
                  }}
                  onBlur={() => {
                    if (skipBlurCommitRef.current) {
                      skipBlurCommitRef.current = false;
                      return;
                    }
                    void commitEditingTitle(chat);
                  }}
                />
                <span className="chat-mode">
                  {chat.mode === "multi"
                    ? t("chat.multiAgent")
                    : chat.mode === "quick"
                      ? t("chat.quickChat")
                      : t("chat.singleAgent")}
                </span>
              </div>
            ) : (
              <button
                type="button"
                className="chat-item-btn"
                onClick={() => selectChat(chat.id)}
              >
                <div className="chat-item-title-row">
                  <span
                    className="chat-title"
                    title={t("sidebar.renameChatHint")}
                    onDoubleClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      startEditingTitle(chat);
                    }}
                  >
                    {chat.title}
                  </span>
                  <span className="chat-item-status">
                    {chatRunStatus[chat.id] === "running" && (
                      <AgentRunningClock
                        className="chat-item-clock"
                        title={t("sidebar.chatRunning")}
                      />
                    )}
                    {chatRunStatus[chat.id] === "completed" && (
                      <ChatCompletedCheck title={t("sidebar.chatCompleted")} />
                    )}
                  </span>
                </div>
                <span className="chat-mode">
                  {chat.mode === "multi"
                    ? t("chat.multiAgent")
                    : chat.mode === "quick"
                      ? t("chat.quickChat")
                      : t("chat.singleAgent")}
                </span>
              </button>
            )}
            <button
              type="button"
              className="chat-delete"
              onClick={() => onDeleteChat(chat.id)}
              title={t("sidebar.deleteChat")}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      {showSetupResume && onResumeSetup && (
        <footer className="sidebar-footer">
          <button type="button" className="sidebar-link setup-resume-link" onClick={onResumeSetup}>
            {t("sidebar.resumeSetup")}
          </button>
        </footer>
      )}
    </aside>
  );
}
