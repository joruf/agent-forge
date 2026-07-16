import { useEffect, useRef, useState } from "react";
import type {
  AgentMessage,
  AgentRole,
  ApprovalRequest,
  Chat,
  ChatRunStatus,
  CommitNewChatPayload,
  ContextPluginRun,
  ExecutionStrategy,
  Message,
  NewChatDraft,
  PromptCorrection,
  ShellCommandEntry,
  TaskBoardSnapshot,
} from "../types";
import { api } from "../services/api";
import { useI18n } from "../hooks/useI18n";
import { AgentHistory, type ActiveAgentInfo } from "./AgentHistory";
import { AgentRunningClock } from "./AgentRunningClock";
import { ApprovalPanel } from "./ApprovalPanel";
import { UserChoiceDialog } from "./UserChoiceDialog";
import { ContextPluginLog } from "./ContextPluginLog";
import { CommandHistoryModal } from "./CommandHistoryModal";
import { TaskBoardPanel } from "./TaskBoardPanel";
import { ExpandableText } from "./ExpandableText";
import { DEFAULT_MULTI_ROLES, normalizeSingleRoleIds, SINGLE_AUTO_ROLE, sortSdlcRoles } from "../constants/roles";
import { normalizeMemoryTokens } from "../constants/memory";
import { formatMessageTimestamp } from "../utils/formatMessageTimestamp";
import {
  collectShellCommands,
  countExecutedShellCommands,
  isShellCommandMessage,
} from "../utils/shellCommands";
import {
  playNotificationPing,
  unlockNotificationAudio,
} from "../utils/notificationSound";
import { parseTaskBoardEvent, shouldShowTaskBoard } from "../utils/taskBoard";

interface WorkingAgentInfo {
  roleName: string;
  model: string | null;
}

function formatRoutingModel(model: string): string {
  return model.replace(/^ollama\//, "");
}

function readPromptCorrections(message: Message): PromptCorrection[] {
  const raw = message.metadata?.prompt_corrections;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== "object") {
      return [];
    }
    const original = String((entry as PromptCorrection).original ?? "").trim();
    const corrected = String((entry as PromptCorrection).corrected ?? "").trim();
    if (!original || !corrected || original === corrected) {
      return [];
    }
    return [{
      original,
      corrected,
      reason: typeof (entry as PromptCorrection).reason === "string"
        ? (entry as PromptCorrection).reason
        : undefined,
    }];
  });
}

function readInterpretedRequest(message: Message): string | null {
  const raw = message.metadata?.interpreted_request;
  if (typeof raw !== "string") {
    return null;
  }
  const interpreted = raw.trim();
  if (!interpreted || interpreted === message.content.trim()) {
    return null;
  }
  return interpreted;
}

interface ChatPanelProps {
  chat: Chat | null;
  draft: NewChatDraft | null;
  roles: AgentRole[];
  defaultMemoryTokens: number;
  chatBlockedReason?: string | null;
  onCommitDraft: (payload: CommitNewChatPayload) => Promise<Chat>;
  onChatUpdated: (chat: Chat) => void;
  onChatRunStateChange: (chatId: string, status: ChatRunStatus | "idle") => void;
}

export function ChatPanel({
  chat,
  draft,
  roles,
  defaultMemoryTokens,
  chatBlockedReason = null,
  onCommitDraft,
  onChatUpdated,
  onChatRunStateChange,
}: ChatPanelProps) {
  const { t, intlLocale } = useI18n();
  const [messages, setMessages] = useState<Message[]>([]);
  const [discussions, setDiscussions] = useState<AgentMessage[]>([]);
  const [liveDiscussions, setLiveDiscussions] = useState<AgentMessage[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [activeUserChoice, setActiveUserChoice] = useState<ApprovalRequest | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [contextPluginRuns, setContextPluginRuns] = useState<ContextPluginRun[]>([]);
  const [pendingShellCommands, setPendingShellCommands] = useState<ShellCommandEntry[]>([]);
  const [commandHistoryOpen, setCommandHistoryOpen] = useState(false);
  const [messageErrors, setMessageErrors] = useState<Record<string, string>>({});
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [executionStrategy, setExecutionStrategy] = useState<ExecutionStrategy>("auto");
  const bottomRef = useRef<HTMLDivElement>(null);
  const messagesRef = useRef<HTMLElement>(null);
  const stickToBottomRef = useRef(true);
  const wsRef = useRef<WebSocket | null>(null);
  const pendingPromptRef = useRef<{ messageId: string; content: string } | null>(null);
  const loadingRef = useRef(false);
  const activeRunRef = useRef(0);
  const runTimeoutRef = useRef<number | null>(null);
  const copyFeedbackTimeoutRef = useRef<number | null>(null);
  const stoppedByUserRef = useRef(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [workingAgent, setWorkingAgent] = useState<WorkingAgentInfo | null>(null);
  const [activeAgents, setActiveAgents] = useState<Map<string, ActiveAgentInfo>>(new Map());
  const [streamingContent, setStreamingContent] = useState<string | null>(null);
  const [taskBoard, setTaskBoard] = useState<TaskBoardSnapshot | null>(null);
  const panelMode = chat?.mode ?? draft?.mode ?? "single";
  const isQuickMode = panelMode === "quick";
  const shellCommandEntries = collectShellCommands(
    messages,
    approvals.filter((approval) => approval.action_type === "command"),
    pendingShellCommands,
  );
  const commandApprovals = approvals.filter((approval) => approval.action_type === "command");
  const executedShellCommandCount = countExecutedShellCommands(shellCommandEntries);
  const chatMemorySettings = {
    enabled: true,
    memory_tokens: normalizeMemoryTokens(defaultMemoryTokens),
    memory_scope: "chat" as const,
  };
  const orderedRoles = sortSdlcRoles(roles);
  const activeRoleIds =
    panelMode === "quick"
      ? []
      : panelMode === "single"
        ? normalizeSingleRoleIds(selectedRoles)
        : selectedRoles.length > 0
          ? selectedRoles
          : [...DEFAULT_MULTI_ROLES];

  const resolveRoleName = (roleId: string) =>
    orderedRoles.find((role) => role.id === roleId)?.name ?? roleId;

  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  useEffect(() => {
    return () => {
      if (copyFeedbackTimeoutRef.current !== null) {
        window.clearTimeout(copyFeedbackTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const unlock = () => {
      unlockNotificationAudio();
    };
    document.addEventListener("pointerdown", unlock, { once: true });
    document.addEventListener("keydown", unlock, { once: true });
    return () => {
      document.removeEventListener("pointerdown", unlock);
      document.removeEventListener("keydown", unlock);
    };
  }, []);

  useEffect(() => {
    stickToBottomRef.current = true;
    setPendingShellCommands([]);
    setTaskBoard(null);
    if (!chat && !draft) {
      setMessages([]);
      setDiscussions([]);
      setLiveDiscussions([]);
      setApprovals([]);
      setMessageErrors({});
      setSubmitError("");
      setExecutionStrategy("auto");
      return;
    }

    if (chat) {
      if (chat.mode !== "quick") {
        setSelectedRoles(
          chat.mode === "single"
            ? normalizeSingleRoleIds(chat.role_ids)
            : chat.role_ids.length > 0
              ? chat.role_ids
              : [...DEFAULT_MULTI_ROLES],
        );
      } else {
        setSelectedRoles([]);
      }
      setExecutionStrategy(chat.execution_strategy);

      void (async () => {
        if (loadingRef.current) {
          return;
        }
        const [msgs, pending] = await Promise.all([
          api.listMessages(chat.id),
          api.listApprovals(chat.id),
        ]);
        setMessages(msgs);
        setApprovals(pending);
      })();
      return;
    }

    setSelectedRoles(
      draft!.mode === "quick"
        ? []
        : draft!.mode === "single"
          ? normalizeSingleRoleIds(draft!.role_ids)
          : draft!.role_ids.length > 0
            ? draft!.role_ids
            : [...DEFAULT_MULTI_ROLES],
    );
    setExecutionStrategy(draft!.execution_strategy);
    setMessages([]);
    setDiscussions([]);
    setLiveDiscussions([]);
    setApprovals([]);
    setMessageErrors({});
    setSubmitError("");
    setContextPluginRuns([]);
  }, [chat?.id, draft]);

  useEffect(() => {
    return () => {
      activeRunRef.current += 1;
      if (runTimeoutRef.current !== null) {
        window.clearTimeout(runTimeoutRef.current);
        runTimeoutRef.current = null;
      }
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [chat?.id]);

  useEffect(() => {
    const container = messagesRef.current;
    if (!container) {
      return undefined;
    }

    const handleScroll = () => {
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      stickToBottomRef.current = distanceFromBottom < 96;
    };

    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, [chat?.id, draft?.mode]);

  useEffect(() => {
    if (stickToBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, liveDiscussions, streamingContent, contextPluginRuns]);

  const toggleRole = (roleId: string) => {
    setSelectedRoles((prev) =>
      prev.includes(roleId)
        ? prev.filter((id) => id !== roleId)
        : [...prev, roleId],
    );
  };

  const selectSingleRole = (roleId: string) => {
    setSelectedRoles([roleId]);
  };

  const copyMessageText = async (messageId: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }

    setCopiedMessageId(messageId);
    if (copyFeedbackTimeoutRef.current !== null) {
      window.clearTimeout(copyFeedbackTimeoutRef.current);
    }
    copyFeedbackTimeoutRef.current = window.setTimeout(() => {
      setCopiedMessageId(null);
      copyFeedbackTimeoutRef.current = null;
    }, 900);
  };

  const applyResolvedRole = (roleId: string) => {
    setSelectedRoles([roleId]);
    setWorkingAgent((prev) => ({
      roleName: resolveRoleName(roleId),
      model: prev?.model ?? null,
    }));
    if (chat) {
      onChatUpdated({ ...chat, role_ids: [roleId] });
    }
  };

  const saveChatSettings = async (targetChat: Chat) => {
    const roleIds =
      targetChat.mode === "quick"
        ? []
        : targetChat.mode === "single"
          ? normalizeSingleRoleIds(selectedRoles)
          : selectedRoles;
    const updated = await api.updateChat(targetChat.id, {
      execution_strategy: executionStrategy,
      role_ids: roleIds,
      memory: chatMemorySettings,
    });
    onChatUpdated(updated);
    return updated;
  };

  const ensureActiveChat = async (): Promise<Chat> => {
    if (chat) {
      return chat;
    }
    if (!draft) {
      throw new Error("No active chat.");
    }
    return onCommitDraft({
      role_ids:
        draft.mode === "quick"
          ? []
          : draft.mode === "single"
            ? normalizeSingleRoleIds(selectedRoles)
            : selectedRoles,
      execution_strategy: executionStrategy,
      memory: chatMemorySettings,
    });
  };

  const handleApproval = async (id: string, approved: boolean) => {
    if (!chat) return;
    await api.respondApproval(chat.id, id, approved);
    setApprovals((prev) => prev.filter((a) => a.id !== id));
    const msgs = await api.listMessages(chat.id);
    setMessages(msgs);
  };

  const handleUserChoice = async (approvalId: string, choiceId: string, comment = "") => {
    if (!chat) return;
    await api.respondApproval(chat.id, approvalId, true, comment, choiceId);
    setApprovals((prev) => prev.filter((approval) => approval.id !== approvalId));
    setActiveUserChoice(null);
    setLoading(false);
    setWorkingAgent(null);
    setActiveAgents(new Map());
    setStreamingContent(null);
    onChatRunStateChange(chat.id, "idle");
    const msgs = await api.listMessages(chat.id);
    setMessages(msgs);
  };

  const handleUserChoiceDismiss = async (approvalId: string) => {
    if (!chat) return;
    await api.respondApproval(chat.id, approvalId, false);
    setApprovals((prev) => prev.filter((approval) => approval.id !== approvalId));
    setActiveUserChoice(null);
    setLoading(false);
    setWorkingAgent(null);
    setActiveAgents(new Map());
    setStreamingContent(null);
    onChatRunStateChange(chat.id, "idle");
    const msgs = await api.listMessages(chat.id);
    setMessages(msgs);
  };

  const appendUserMessage = (targetChat: Chat, content: string): string => {
    const messageId = `temp-${Date.now()}`;
    const userMsg: Message = {
      id: messageId,
      chat_id: targetChat.id,
      role: "user",
      agent_id: null,
      agent_name: null,
      content,
      metadata: {},
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    return messageId;
  };

  const markPromptFailed = async (errorText: string, targetChat?: Chat) => {
    const pending = pendingPromptRef.current;
    let targetMessageId: string | null = pending?.messageId ?? null;

    if (targetChat) {
      try {
        const msgs = await api.listMessages(targetChat.id);
        const lastUser = [...msgs].reverse().find((message) => message.role === "user");
        if (lastUser && (!pending || lastUser.content.trim() === pending.content.trim())) {
          targetMessageId = lastUser.id;
          setMessages(msgs);
        } else if (!pending) {
          setMessages(msgs);
        }
      } catch {
        // Keep pending temp message association below.
      }
    }

    if (targetMessageId) {
      setMessageErrors((prev) => {
        const next = { ...prev, [targetMessageId!]: errorText };
        if (pending && pending.messageId !== targetMessageId) {
          delete next[pending.messageId];
        }
        return next;
      });
      setSubmitError("");
      pendingPromptRef.current = null;
      return;
    }

    setSubmitError(errorText);
    pendingPromptRef.current = null;
  };

  const clearMessageError = (messageId: string) => {
    setMessageErrors((prev) => {
      if (!(messageId in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[messageId];
      return next;
    });
  };

  const executePrompt = async (
    content: string,
    options: { appendUser?: boolean; retry?: boolean } = {},
  ): Promise<boolean> => {
    if (!chat && !draft) {
      return false;
    }

    let activeChat: Chat;
    try {
      activeChat = await ensureActiveChat();
    } catch {
      setSubmitError(t("chat.agentFailed"));
      return false;
    }

    const appendUser = options.appendUser ?? !options.retry;
    setSubmitError("");
    setLoading(true);
    onChatRunStateChange(activeChat.id, "running");
    setLiveDiscussions([]);
    setStreamingContent(null);
    setTaskBoard(null);
    setActiveAgents(new Map());
    if (activeChat.mode === "quick") {
      setWorkingAgent({
        roleName: t("chat.quickAssistant"),
        model: null,
      });
    } else if (activeChat.mode === "single" && activeRoleIds[0] !== SINGLE_AUTO_ROLE) {
      setWorkingAgent({
        roleName: resolveRoleName(activeRoleIds[0]),
        model: null,
      });
    } else {
      setWorkingAgent(null);
    }

    try {
      await saveChatSettings(activeChat);
    } catch {
      setLoading(false);
      setWorkingAgent(null);
      onChatRunStateChange(activeChat.id, "idle");
      void markPromptFailed(t("chat.agentFailed"), activeChat);
      return false;
    }

    if (appendUser) {
      stickToBottomRef.current = true;
      const messageId = appendUserMessage(activeChat, content);
      pendingPromptRef.current = { messageId, content };
    }

    try {
      activeRunRef.current += 1;
      const runId = activeRunRef.current;
      let completed = false;

      if (runTimeoutRef.current !== null) {
        window.clearTimeout(runTimeoutRef.current);
      }
      runTimeoutRef.current = window.setTimeout(() => {
        if (runId !== activeRunRef.current || completed) {
          return;
        }
        wsRef.current?.close();
        setLoading(false);
        setWorkingAgent(null);
        onChatRunStateChange(activeChat.id, "idle");
        void markPromptFailed(t("chat.agentTimeout"), activeChat);
      }, 600_000);

      const finishLoading = (outcome: "completed" | "idle" = "idle") => {
        if (runId !== activeRunRef.current) {
          return;
        }
        completed = true;
        if (runTimeoutRef.current !== null) {
          window.clearTimeout(runTimeoutRef.current);
          runTimeoutRef.current = null;
        }
        setLoading(false);
        setWorkingAgent(null);
        setActiveAgents(new Map());
        setStreamingContent(null);
        onChatRunStateChange(
          activeChat.id,
          outcome === "completed" ? "completed" : "idle",
        );
      };

      const markAgentRunning = (agentId: string, agentName: string) => {
        if (runId !== activeRunRef.current) {
          return;
        }
        setActiveAgents(new Map([
          [agentId, { roleId: agentId, roleName: agentName, model: null }],
        ]));
        setWorkingAgent({ roleName: agentName, model: null });
      };

      const payload = JSON.stringify({
        type: "message",
        content,
        mode: activeChat.mode,
        role_ids: activeRoleIds,
        retry: options.retry ?? false,
      });

      const bindWebSocketHandlers = (ws: WebSocket) => {
        ws.onmessage = (event) => {
          if (runId !== activeRunRef.current) {
            return;
          }
          const data = JSON.parse(event.data as string) as {
            type: string;
            message?: string;
            content?: string;
            title?: string;
            role_id?: string;
            agent_id?: string;
            agent_name?: string;
            plugin_id?: string;
            plugin_name?: string;
            ok?: boolean;
            text?: string;
            error?: string | null;
            reason?: string;
            approval_id?: string;
            command?: string;
            cwd?: string;
            timestamp?: string;
            entry?: ShellCommandEntry;
            required_plugins?: string[];
            results?: Array<{
              plugin_id: string;
              plugin_name?: string;
              ok: boolean;
              text: string;
              error?: string | null;
            }>;
            selection?: Array<{ plugin_id: string; reason: string }>;
            routing?: { model?: string };
            success?: boolean;
            message_id?: string;
            prompt_corrections?: PromptCorrection[];
            interpreted_request?: string;
            discussion?: AgentMessage;
            result?: {
              messages: Message[];
              agent_discussions: AgentMessage[];
              pending_approvals: ApprovalRequest[];
              title?: string;
              resolved_role_id?: string;
            };
          };

          if (data.type === "context_plugins_started") {
            setContextPluginRuns([]);
          }

          if (data.type === "context_plugin_start" && data.plugin_id && data.plugin_name) {
            setContextPluginRuns((prev) => [
              ...prev,
              {
                plugin_id: data.plugin_id!,
                plugin_name: data.plugin_name!,
                status: "running",
                timestamp: new Date().toISOString(),
                reason: data.reason,
              },
            ]);
          }

          if (data.type === "context_plugin_complete" && data.plugin_id && data.plugin_name) {
            setContextPluginRuns((prev) =>
              prev.map((run) =>
                run.plugin_id === data.plugin_id
                  ? {
                      ...run,
                      status: data.ok ? "ok" : "error",
                      text: data.text,
                      error: data.error,
                    }
                  : run,
              ),
            );
          }

          if (data.type === "user_choice_pending" && data.approval_id) {
            void api.listApprovals(activeChat.id).then((pending) => {
              setApprovals(pending);
              const match = pending.find(
                (approval) => approval.id === data.approval_id,
              );
              if (match) {
                setActiveUserChoice(match);
                finishLoading("idle");
              }
            });
          }

          if (data.type === "shell_command_pending" && data.command && data.approval_id) {
            setPendingShellCommands((prev) => [
              ...prev.filter((entry) => entry.approval_id !== data.approval_id),
              {
                id: `pending-${data.approval_id}`,
                command: String(data.command),
                cwd: data.cwd ? String(data.cwd) : undefined,
                status: "pending",
                success: false,
                exit_code: null,
                agent_id: data.agent_id ? String(data.agent_id) : null,
                agent_name: data.agent_name ? String(data.agent_name) : null,
                approval_id: String(data.approval_id),
                timestamp: String(data.timestamp ?? new Date().toISOString()),
              },
            ]);
          }

          if (data.type === "shell_command_recorded") {
            if (data.entry && typeof data.entry === "object") {
              const entry = data.entry as ShellCommandEntry;
              setPendingShellCommands((prev) =>
                prev.filter((pending) => pending.approval_id !== entry.approval_id),
              );
            }
            void api.listMessages(activeChat.id).then(setMessages);
          }

          if (data.type === "content_delta" && data.content) {
            setStreamingContent((prev) => `${prev ?? ""}${data.content}`);
          }

          if (data.type === "title_updated" && data.title) {
            void api.getChat(activeChat.id).then(onChatUpdated);
          }

          if (data.type === "role_resolved" && data.role_id) {
            applyResolvedRole(data.role_id);
          }

          if (data.type === "agent_start" && data.agent_id && data.agent_name) {
            markAgentRunning(data.agent_id, data.agent_name);
          }

          if (data.type === "agent_end" && data.agent_id) {
            setActiveAgents((prev) => {
              const next = new Map(prev);
              next.delete(data.agent_id!);
              if (next.size === 0) {
                setWorkingAgent(null);
              }
              return next;
            });
          }

          if (data.type === "model_selected") {
            const model = data.routing?.model
              ? formatRoutingModel(String(data.routing.model))
              : null;
            const agentId = data.agent_id ?? "assistant";
            const agentName =
              data.agent_name
              ?? (data.agent_id ? resolveRoleName(data.agent_id) : t("chat.assistant"));
            setWorkingAgent({ roleName: agentName, model });
            setActiveAgents((prev) => {
              const existing = prev.get(agentId);
              if (!existing) {
                return prev;
              }
              const next = new Map(prev);
              next.set(agentId, { ...existing, model });
              return next;
            });
          }

          if (data.type === "user_intervention" && data.content) {
            setLiveDiscussions((prev) => [
              ...prev,
              {
                from_agent: t("chat.you"),
                to_agent: "team",
                content: data.content!,
                timestamp: new Date().toISOString(),
              },
            ]);
          }

          if (data.type === "prompt_normalized" && Array.isArray(data.prompt_corrections)) {
            setMessages((prev) => {
              const messageId = typeof data.message_id === "string" ? data.message_id : null;
              let targetIndex = messageId
                ? prev.findIndex((message) => message.id === messageId)
                : -1;
              if (targetIndex < 0) {
                for (let index = prev.length - 1; index >= 0; index -= 1) {
                  if (prev[index].role === "user") {
                    targetIndex = index;
                    break;
                  }
                }
              }
              if (targetIndex < 0) {
                return prev;
              }
              return prev.map((message, index) => (
                index === targetIndex
                  ? {
                      ...message,
                      id: messageId ?? message.id,
                      metadata: {
                        ...message.metadata,
                        prompt_corrections: data.prompt_corrections,
                        interpreted_request: data.interpreted_request,
                      },
                    }
                  : message
              ));
            });
          }

          if (data.type === "task_board_updated") {
            const snapshot = parseTaskBoardEvent(data);
            if (snapshot) {
              setTaskBoard(snapshot);
            }
          }

          if (data.type === "user_message") {
            void api.listMessages(activeChat.id).then(setMessages);
          }

          if (data.type === "approval_result") {
            if (data.approval_id) {
              setPendingShellCommands((prev) =>
                prev.filter((entry) => entry.approval_id !== data.approval_id),
              );
              setActiveUserChoice((prev) =>
                prev?.id === data.approval_id ? null : prev,
              );
            }
            void Promise.all([
              api.listMessages(activeChat.id).then(setMessages),
              api.listApprovals(activeChat.id).then(setApprovals),
            ]);
          }

          if (data.type === "agent_message" && data.discussion) {
            setLiveDiscussions((prev) => [...prev, data.discussion!]);
          }

          if (data.type === "error") {
            void markPromptFailed(data.message || t("chat.agentFailed"), activeChat);
            finishLoading("idle");
            wsRef.current?.close();
            wsRef.current = null;
            return;
          }

          if (data.type === "stopped") {
            pendingPromptRef.current = null;
            finishLoading("idle");
            void api.listMessages(activeChat.id).then(setMessages);
            return;
          }

          if (data.type === "complete" && data.result) {
            const needsAttention =
              (data.result.pending_approvals?.length ?? 0) > 0
              || (data.result.messages?.length ?? 0) > 0;
            if (needsAttention) {
              playNotificationPing();
            }
            pendingPromptRef.current = null;
            setMessageErrors({});
            void api.listMessages(activeChat.id).then((msgs) => {
              setMessages(msgs);
            });
            setDiscussions(data.result.agent_discussions);
            setLiveDiscussions([]);
            setApprovals(data.result.pending_approvals);
            const pendingChoice = data.result.pending_approvals.find(
              (approval) => approval.action_type === "user_choice",
            );
            if (pendingChoice) {
              setActiveUserChoice(pendingChoice);
            }
            if (data.result.resolved_role_id) {
              applyResolvedRole(data.result.resolved_role_id);
            }
            if (data.result.title) {
              onChatUpdated({ ...activeChat, title: data.result.title });
            } else {
              void api.getChat(activeChat.id).then(onChatUpdated);
            }
            finishLoading("completed");
          }
        };

        ws.onclose = () => {
          if (runId !== activeRunRef.current || completed) {
            return;
          }
          finishLoading("idle");
          if (!stoppedByUserRef.current) {
            void markPromptFailed(t("chat.agentFailed"), activeChat);
          }
          stoppedByUserRef.current = false;
          wsRef.current = null;
        };

        ws.onerror = () => {
          if (runId !== activeRunRef.current || completed) {
            return;
          }
        };
      };

      const existing = wsRef.current;
      if (existing?.readyState === WebSocket.OPEN) {
        bindWebSocketHandlers(existing);
        existing.send(payload);
        return true;
      }

      if (existing && existing.readyState !== WebSocket.CLOSED) {
        existing.close();
      }

      const ws = new WebSocket(api.wsUrl(activeChat.id));
      wsRef.current = ws;
      bindWebSocketHandlers(ws);
      ws.onopen = () => {
        if (runId !== activeRunRef.current) {
          return;
        }
        ws.send(payload);
      };
    } catch {
      setLoading(false);
      setWorkingAgent(null);
      onChatRunStateChange(activeChat.id, "idle");
      void markPromptFailed(t("chat.agentFailed"), activeChat);
      return false;
    }

    return true;
  };

  const retryPrompt = (messageId: string, content: string) => {
    if (loading) return;
    clearMessageError(messageId);
    pendingPromptRef.current = { messageId, content };
    void executePrompt(content, { retry: true, appendUser: false });
  };

  const sendIntervention = (content: string) => {
    if (!chat || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      void markPromptFailed(t("chat.agentFailed"), chat ?? undefined);
      return;
    }

    appendUserMessage(chat, content);
    wsRef.current.send(
      JSON.stringify({
        type: "intervention",
        content,
        mode: chat.mode,
        role_ids: activeRoleIds,
      }),
    );
  };

  const stopAgents = () => {
    stoppedByUserRef.current = true;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "stop" }));
      return;
    }
    setLoading(false);
    setWorkingAgent(null);
  };

  const sendMessage = async () => {
    if ((!chat && !draft) || !input.trim() || chatBlockedReason) return;

    const content = input.trim();

    if (loading && chat) {
      setInput("");
      sendIntervention(content);
      return;
    }

    const started = await executePrompt(content, { appendUser: true });
    if (started) {
      setInput("");
    }
  };

  if (!chat && !draft) {
    return (
      <main className="chat-panel empty">
        <p>{t("chat.empty")}</p>
      </main>
    );
  }

  const lastUserMessageId = (() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === "user") {
        return messages[index].id;
      }
    }
    return null;
  })();

  return (
    <main className="chat-panel">
      <header className="chat-header">
        <div className="chat-header-top">
          <div className="chat-header-title">
            <h2>{chat?.title ?? t("chat.newChatTitle")}</h2>
            <span className="badge">
              {panelMode === "multi"
                ? t("chat.multiAgent")
                : isQuickMode
                  ? t("chat.quickChat")
                  : t("chat.singleAgent")}
            </span>
            {isQuickMode && (
              <p className="quick-chat-hint">{t("chat.quickChatDescription")}</p>
            )}
          </div>
          {!isQuickMode && (
            <div className="memory-controls">
              <label>
                {t("chat.executionStrategy")}
                <select
                  value={executionStrategy}
                  onChange={(e) => setExecutionStrategy(e.target.value as ExecutionStrategy)}
                >
                  <option value="auto">{t("chat.executionStrategyAuto")}</option>
                  <option value="serial">{t("chat.executionStrategySerial")}</option>
                  <option value="parallel">{t("chat.executionStrategyParallel")}</option>
                  <option value="hybrid">{t("chat.executionStrategyHybrid")}</option>
                </select>
              </label>
              <div className="command-history-control">
                <button
                  type="button"
                  className="command-history-btn"
                  title={t("shellCommands.open")}
                  aria-label={t("shellCommands.openWithCount", { count: executedShellCommandCount })}
                  onClick={() => setCommandHistoryOpen(true)}
                >
                  <svg className="command-history-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                    <rect x="3" y="4" width="18" height="14" rx="2" ry="2" />
                    <path d="M7 8h10" />
                    <path d="M7 12h6" />
                    <path d="M7 16h8" />
                  </svg>
                </button>
                <span className="command-history-count" aria-hidden="true">
                  {executedShellCommandCount}
                </span>
              </div>
            </div>
          )}
        </div>
        {!isQuickMode && (
        <div className="role-selector">
          <span className="role-selector-label">{t("chat.selectRole")}</span>
          {panelMode === "single" && (
            <label
              className="role-chip role-chip-auto"
              title={t("chat.autoRoleDescription")}
              data-tooltip={t("chat.autoRoleDescription")}
            >
              <input
                type="radio"
                name="single-agent-role"
                checked={activeRoleIds[0] === SINGLE_AUTO_ROLE}
                onChange={() => selectSingleRole(SINGLE_AUTO_ROLE)}
              />
              {t("chat.autoRole")}
            </label>
          )}
          {orderedRoles.map((role) => (
            <label
              key={role.id}
              className="role-chip"
              title={role.description}
              data-tooltip={role.description}
            >
              <input
                type={panelMode === "single" ? "radio" : "checkbox"}
                name={panelMode === "single" ? "single-agent-role" : undefined}
                checked={
                  panelMode === "single"
                    ? activeRoleIds[0] === role.id
                    : selectedRoles.includes(role.id)
                }
                onChange={() =>
                  panelMode === "single"
                    ? selectSingleRole(role.id)
                    : toggleRole(role.id)
                }
              />
              {role.name}
            </label>
          ))}
        </div>
        )}
      </header>

      <ApprovalPanel approvals={commandApprovals} onRespond={handleApproval} />
      <UserChoiceDialog
        approval={activeUserChoice}
        onChoose={handleUserChoice}
        onDismiss={handleUserChoiceDismiss}
      />

      <div className="chat-body">
        <section className="messages" ref={messagesRef}>
          {messages.filter((msg) => !isShellCommandMessage(msg)).map((msg) => {
            const routing = msg.metadata?.routing as { model?: string; task?: string } | undefined;
            const modelLabel = routing?.model
              ? String(routing.model).replace(/^ollama\//, "")
              : null;
            const errorText = messageErrors[msg.id];
            const promptCorrections = msg.role === "user" ? readPromptCorrections(msg) : [];
            const interpretedRequest = msg.role === "user" ? readInterpretedRequest(msg) : null;
            const isLastUserMessage = msg.role === "user" && msg.id === lastUserMessageId;

            return (
            <div key={msg.id} className={`message message-${msg.role}`}>
              <div className="message-header">
                <span>
                  {msg.agent_name ?? (msg.role === "user" ? t("chat.you") : t("chat.assistant"))}
                </span>
                {modelLabel && <span className="message-model">{modelLabel}</span>}
                <span className="message-meta">
                  <span className="message-time">
                    {formatMessageTimestamp(msg.created_at, intlLocale)}
                  </span>
                  <button
                    type="button"
                    className={`message-copy-btn${
                      copiedMessageId === msg.id ? " message-copy-btn--copied" : ""
                    }`}
                    title={t("chat.copyMessage")}
                    aria-label={t("chat.copyMessage")}
                    onClick={() => void copyMessageText(msg.id, msg.content)}
                  >
                    <svg
                      className="message-copy-icon"
                      viewBox="0 0 24 24"
                      aria-hidden="true"
                      focusable="false"
                    >
                      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                    </svg>
                  </button>
                  {isLastUserMessage && (
                    <button
                      type="button"
                      className="message-copy-btn message-resend-btn"
                      title={t("chat.resend")}
                      aria-label={t("chat.resend")}
                      disabled={loading}
                      onClick={() => retryPrompt(msg.id, msg.content)}
                    >
                      <svg
                        className="message-copy-icon"
                        viewBox="0 0 24 24"
                        aria-hidden="true"
                        focusable="false"
                      >
                        <path d="M1 4v6h6" />
                        <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                      </svg>
                    </button>
                  )}
                </span>
              </div>
              <ExpandableText text={msg.content} previewLength={500} />
              {promptCorrections.length > 0 && (
                <div className="message-prompt-corrections" role="note">
                  <div className="message-prompt-corrections-title">
                    {t("chat.promptCorrectedTitle")}
                  </div>
                  <ul className="message-prompt-corrections-list">
                    {promptCorrections.map((correction) => (
                      <li key={`${correction.original}-${correction.corrected}`}>
                        <code>{correction.original}</code>
                        <span aria-hidden="true"> → </span>
                        <code>{correction.corrected}</code>
                      </li>
                    ))}
                  </ul>
                  <div className="message-prompt-corrections-hint">
                    {t("chat.promptCorrectedHint")}
                  </div>
                </div>
              )}
              {interpretedRequest && (
                <div className="message-interpreted-request" role="note">
                  <div className="message-interpreted-request-title">
                    {t("taskBoard.interpretedRequestTitle")}
                  </div>
                  <p className="message-interpreted-request-text">{interpretedRequest}</p>
                </div>
              )}
              {msg.role === "user" && errorText && (
                <div className="message-user-error">
                  <div className="message-user-error-text">{errorText}</div>
                  {!isLastUserMessage && (
                    <button
                      type="button"
                      className="btn-try-again"
                      disabled={loading}
                      onClick={() => retryPrompt(msg.id, msg.content)}
                    >
                      {t("chat.tryAgain")}
                    </button>
                  )}
                </div>
              )}
            </div>
            );
          })}
          {shouldShowTaskBoard(taskBoard) && taskBoard && (
            <TaskBoardPanel snapshot={taskBoard} />
          )}
          <ContextPluginLog runs={contextPluginRuns} />
          {loading && streamingContent !== null && (
            <div className="message message-assistant message-streaming">
              <div className="message-header">
                <span>{workingAgent?.roleName ?? t("chat.assistant")}</span>
                {workingAgent?.model && (
                  <span className="message-model">{workingAgent.model}</span>
                )}
                <span className="message-header-actions">
                  <span className="agent-history-icon">
                    <AgentRunningClock title={t("agentHistory.running")} />
                  </span>
                </span>
              </div>
              <ExpandableText text={streamingContent} previewLength={500} />
            </div>
          )}
          {loading && streamingContent === null && (
            <div className="message message-assistant message-loading">
              <div className="message-header message-loading-meta">
                <span>{workingAgent?.roleName ?? t("chat.assistant")}</span>
                {workingAgent?.model && (
                  <span className="message-model">{workingAgent.model}</span>
                )}
                <span className="message-header-actions">
                  <span className="agent-history-icon">
                    <AgentRunningClock title={t("agentHistory.running")} />
                  </span>
                </span>
              </div>
              <div className="message-loading-text">
                {panelMode === "multi"
                  ? t("chat.agentWorkingMulti")
                  : isQuickMode
                    ? t("chat.agentWorkingQuick")
                    : t("chat.agentWorking")}
              </div>
            </div>
          )}
          {submitError && <div className="message message-error">{submitError}</div>}
          <div ref={bottomRef} />
        </section>

        {panelMode === "multi" && (
          <AgentHistory
            discussions={discussions}
            liveEvents={liveDiscussions}
            activeAgents={activeAgents}
          />
        )}
      </div>

      <footer className="chat-input">
        {chatBlockedReason && (
          <p className="chat-blocked-notice">{chatBlockedReason}</p>
        )}
        <textarea
          value={input}
          disabled={Boolean(chatBlockedReason)}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (chatBlockedReason) {
              return;
            }
            if (e.key !== "Enter" || e.nativeEvent.isComposing) {
              return;
            }
            if (e.shiftKey || e.ctrlKey || e.metaKey) {
              return;
            }
            e.preventDefault();
            void sendMessage();
          }}
          placeholder={
            chatBlockedReason
              ? t("readiness.chatBlocked")
              : loading
              ? isQuickMode
                ? t("chat.inputPlaceholderQuick")
                : panelMode === "multi"
                  ? t("chat.inputPlaceholderMultiActive")
                  : t("chat.inputPlaceholderActive")
              : isQuickMode
                ? t("chat.inputPlaceholderQuick")
                : t("chat.inputPlaceholder")
          }
          rows={3}
        />
        <div className="chat-input-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => void sendMessage()}
            disabled={!input.trim() || Boolean(chatBlockedReason)}
          >
            {t("chat.send")}
          </button>
          {loading && (
            <button
              type="button"
              className="btn-stop"
              onClick={stopAgents}
            >
              {t("chat.stop")}
            </button>
          )}
        </div>
      </footer>
      <CommandHistoryModal
        open={commandHistoryOpen}
        entries={shellCommandEntries}
        onClose={() => setCommandHistoryOpen(false)}
      />
    </main>
  );
}
