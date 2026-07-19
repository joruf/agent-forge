import type { Chat, NewChatDraft } from "../types";

/**
 * Return whether the chat uses the grill clarify-and-plan workflow.
 *
 * @param chat Active chat, if any
 * @param draft New-chat draft, if any
 * @return True when grill mode is enabled or legacy grill chat mode is active
 */
export function isGrillChat(chat: Chat | null, draft: NewChatDraft | null): boolean {
  if (chat?.mode === "grill" || chat?.grill_enabled) {
    return true;
  }
  return Boolean(draft?.grill_enabled);
}

/**
 * Format the sidebar or header mode label, including an optional grill suffix.
 *
 * @param chat Chat metadata
 * @param labels Localized base labels keyed by mode
 * @return Mode label text
 */
export function formatChatModeLabel(
  chat: Chat,
  labels: {
    quickChat: string;
    singleAgent: string;
    multiAgent: string;
    grillMode: string;
    grillBadge: string;
  },
): string {
  if (chat.mode === "quick") {
    return labels.quickChat;
  }
  if (chat.mode === "grill") {
    return labels.grillMode;
  }
  const base = chat.mode === "multi" ? labels.multiAgent : labels.singleAgent;
  return chat.grill_enabled ? `${base} · ${labels.grillBadge}` : base;
}
