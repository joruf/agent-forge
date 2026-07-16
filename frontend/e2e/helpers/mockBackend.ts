import type { Page } from "@playwright/test";

interface ChatMemorySettings {
  memory_tokens: number;
  memory_scope: "chat" | "global";
  enabled: boolean;
}

interface AgentRole {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  is_builtin: boolean;
}

interface Chat {
  id: string;
  title: string;
  mode: "single" | "multi" | "quick";
  execution_strategy: "auto" | "serial" | "parallel" | "hybrid";
  role_ids: string[];
  memory: ChatMemorySettings;
  created_at: string;
  updated_at: string;
}

interface Message {
  id: string;
  chat_id: string;
  role: "user" | "assistant" | "system" | "agent" | "tool";
  agent_id: string | null;
  agent_name: string | null;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

const DEFAULT_MEMORY: ChatMemorySettings = {
  memory_tokens: 32000,
  memory_scope: "chat",
  enabled: true,
};

const BUILTIN_ROLES: AgentRole[] = [
  {
    id: "developer",
    name: "Developer",
    description: "Writes and edits code in the workspace.",
    system_prompt: "You are a developer.",
    is_builtin: true,
  },
  {
    id: "reviewer",
    name: "Reviewer",
    description: "Reviews code for bugs and best practices.",
    system_prompt: "You are a reviewer.",
    is_builtin: true,
  },
  {
    id: "project_manager",
    name: "Project Manager",
    description: "Coordinates agents and involves the user.",
    system_prompt: "You are a project manager.",
    is_builtin: true,
  },
];

const DEFAULT_SETTINGS = {
  workspace_root: "/tmp/agentforge-workspace",
  ollama_base_url: "http://127.0.0.1:11434",
  default_model: "ollama/llama3.1:8b",
  default_memory_tokens: 32000,
  llm_auto_routing: true,
  command_whitelist: ["git", "npm", "python"],
  command_blacklist: ["rm", "sudo"],
  has_openai_key: false,
  has_anthropic_key: false,
  has_gemini_key: false,
  has_groq_key: false,
  has_mistral_key: false,
  ui_language: "en",
};

const DEFAULT_ROUTING = {
  auto_routing: true,
  default_model: "ollama/llama3.1:8b",
  installed: ["llama3.1:8b"],
  routing: { coding: "ollama/llama3.1:8b" },
  models: [],
  tasks: {
    coding: {
      label: "Coding",
      description: "Code generation",
      selected: "ollama/llama3.1:8b",
      routing_override: "ollama/llama3.1:8b",
    },
  },
};

const DEFAULT_SETUP_STATUS = {
  completed: true,
  skipped: false,
  current_step: "done",
  steps_done: ["welcome", "ollama", "models", "workspace", "done"],
  steps: ["welcome", "ollama", "models", "workspace", "done"],
  should_show_wizard: false,
  can_resume: false,
};

const DEFAULT_READINESS = {
  chat_ready: true,
  active_model: "ollama/llama3.1:8b",
  results: [],
  summary: "Models ready for chat",
  blocking_message: null,
  blocking_id: null,
};

export interface MockBackendOptions {
  chats?: Chat[];
  messagesByChat?: Record<string, Message[]>;
  customRoles?: AgentRole[];
  /** When true, WebSocket replies include prompt correction metadata. */
  promptCorrections?: boolean;
}

function jsonResponse(body: unknown, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

function nowIso(): string {
  return new Date().toISOString();
}

function createChat(overrides: Partial<Chat> = {}): Chat {
  const timestamp = nowIso();
  return {
    id: overrides.id ?? `chat-${Date.now()}`,
    title: overrides.title ?? "New Chat",
    mode: overrides.mode ?? "quick",
    execution_strategy: overrides.execution_strategy ?? "auto",
    role_ids: overrides.role_ids ?? [],
    memory: overrides.memory ?? { ...DEFAULT_MEMORY },
    created_at: overrides.created_at ?? timestamp,
    updated_at: overrides.updated_at ?? timestamp,
  };
}

export function createSampleChat(): Chat {
  return createChat({
    id: "chat-sample-1",
    title: "Sample Chat",
    mode: "quick",
  });
}

export function createSampleMessages(chatId: string): Message[] {
  const timestamp = nowIso();
  return [
    {
      id: "msg-user-1",
      chat_id: chatId,
      role: "user",
      agent_id: null,
      agent_name: null,
      content: "Hello from persisted history",
      metadata: {},
      created_at: timestamp,
    },
    {
      id: "msg-assistant-1",
      chat_id: chatId,
      role: "assistant",
      agent_id: null,
      agent_name: "Assistant",
      content: "Welcome back — mock backend is online.",
      metadata: {},
      created_at: timestamp,
    },
  ];
}

/**
 * Install Playwright route mocks for AgentForge REST and WebSocket APIs.
 *
 * @param page Playwright page under test
 * @param options Optional seed data for chats, messages, and roles
 */
export async function setupMockBackend(
  page: Page,
  options: MockBackendOptions = {},
): Promise<void> {
  const chats: Chat[] = [...(options.chats ?? [])];
  const messagesByChat: Record<string, Message[]> = {
    ...(options.messagesByChat ?? {}),
  };
  const customRoles: AgentRole[] = [...(options.customRoles ?? [])];
  const promptCorrections = options.promptCorrections ?? false;

  const listRoles = (): AgentRole[] => [...BUILTIN_ROLES, ...customRoles];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "") || "/";
    const method = request.method();

    if (path === "/health" && method === "GET") {
      await route.fulfill(jsonResponse({ status: "ok" }));
      return;
    }

    if (path === "/readiness" && method === "GET") {
      await route.fulfill(jsonResponse(DEFAULT_READINESS));
      return;
    }

    if (path === "/settings" && method === "GET") {
      await route.fulfill(jsonResponse(DEFAULT_SETTINGS));
      return;
    }

    if (path === "/settings" && method === "PATCH") {
      const patch = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill(jsonResponse({ ...DEFAULT_SETTINGS, ...patch }));
      return;
    }

    if (path === "/llm/routing" && method === "GET") {
      await route.fulfill(jsonResponse(DEFAULT_ROUTING));
      return;
    }

    if (path === "/setup/status" && method === "GET") {
      await route.fulfill(jsonResponse(DEFAULT_SETUP_STATUS));
      return;
    }

    if (path === "/context/catalog" && method === "GET") {
      await route.fulfill(jsonResponse({ plugins: [], enabled: [] }));
      return;
    }

    if (path === "/roles" && method === "GET") {
      await route.fulfill(jsonResponse(listRoles()));
      return;
    }

    if (path === "/roles" && method === "POST") {
      const body = request.postDataJSON() as Omit<AgentRole, "is_builtin">;
      if (listRoles().some((role) => role.id === body.id)) {
        await route.fulfill(jsonResponse({ detail: "Role already exists" }, 409));
        return;
      }
      const created: AgentRole = { ...body, is_builtin: false };
      customRoles.push(created);
      await route.fulfill(jsonResponse(created));
      return;
    }

    const roleMatch = path.match(/^\/roles\/([^/]+)$/);
    if (roleMatch) {
      const roleId = decodeURIComponent(roleMatch[1]);
      const existing = customRoles.find((role) => role.id === roleId);
      if (method === "PUT") {
        if (!existing) {
          await route.fulfill(jsonResponse({ detail: "Not found" }, 404));
          return;
        }
        const body = request.postDataJSON() as Pick<
          AgentRole,
          "name" | "description" | "system_prompt"
        >;
        Object.assign(existing, body);
        await route.fulfill(jsonResponse(existing));
        return;
      }
      if (method === "DELETE") {
        if (!existing) {
          await route.fulfill(jsonResponse({ detail: "Not found" }, 404));
          return;
        }
        const index = customRoles.findIndex((role) => role.id === roleId);
        customRoles.splice(index, 1);
        await route.fulfill(jsonResponse({ deleted: true }));
        return;
      }
    }

    if (path === "/chats" && method === "GET") {
      await route.fulfill(jsonResponse(chats));
      return;
    }

    if (path === "/chats" && method === "POST") {
      const body = request.postDataJSON() as Partial<Chat>;
      const chat = createChat({
        title: body.title ?? "New Chat",
        mode: body.mode ?? "quick",
        execution_strategy: body.execution_strategy ?? "auto",
        role_ids: body.role_ids ?? [],
        memory: body.memory ?? { ...DEFAULT_MEMORY },
      });
      chats.unshift(chat);
      messagesByChat[chat.id] = [];
      await route.fulfill(jsonResponse(chat));
      return;
    }

    const chatMatch = path.match(/^\/chats\/([^/]+)(\/messages|\/approvals)?$/);
    if (chatMatch) {
      const chatId = decodeURIComponent(chatMatch[1]);
      const suffix = chatMatch[2] ?? "";
      const chat = chats.find((entry) => entry.id === chatId);

      if (suffix === "/messages" && method === "GET") {
        await route.fulfill(jsonResponse(messagesByChat[chatId] ?? []));
        return;
      }

      if (suffix === "/approvals" && method === "GET") {
        await route.fulfill(jsonResponse([]));
        return;
      }

      if (!suffix && method === "GET" && chat) {
        await route.fulfill(jsonResponse(chat));
        return;
      }

      if (!suffix && method === "PATCH" && chat) {
        const patch = request.postDataJSON() as Partial<Chat>;
        Object.assign(chat, patch, { updated_at: nowIso() });
        await route.fulfill(jsonResponse(chat));
        return;
      }

      if (!suffix && method === "DELETE" && chat) {
        const index = chats.findIndex((entry) => entry.id === chatId);
        if (index >= 0) {
          chats.splice(index, 1);
        }
        delete messagesByChat[chatId];
        await route.fulfill(jsonResponse({ deleted: true }));
        return;
      }
    }

    await route.fulfill(jsonResponse({ detail: `Unmocked ${method} ${path}` }, 404));
  });

  await page.routeWebSocket(/\/api\/ws\/chats\//, (ws) => {
    ws.onMessage((message) => {
      let payload: { type?: string; content?: string; mode?: string; role_ids?: string[] };
      try {
        payload = JSON.parse(message) as typeof payload;
      } catch {
        return;
      }

      if (payload.type !== "message" || !payload.content) {
        return;
      }

      const wsUrl = ws.url();
      const chatId = decodeURIComponent(wsUrl.split("/").pop() ?? "chat-unknown");
      const userMessageId = `msg-user-${Date.now()}`;
      const assistantMessageId = `msg-assistant-${Date.now()}`;
      const timestamp = nowIso();

      if (promptCorrections) {
        ws.send(
          JSON.stringify({
            type: "prompt_normalized",
            message_id: userMessageId,
            prompt_corrections: [
              {
                original: "rd file",
                corrected: "read file",
                reason: "typo",
              },
            ],
            interpreted_request: "read file README.md",
          }),
        );
      }

      const userMessage: Message = {
        id: userMessageId,
        chat_id: chatId,
        role: "user",
        agent_id: null,
        agent_name: null,
        content: payload.content,
        metadata: promptCorrections
          ? {
              prompt_corrections: [
                {
                  original: "rd file",
                  corrected: "read file",
                  reason: "typo",
                },
              ],
              interpreted_request: "read file README.md",
            }
          : {},
        created_at: timestamp,
      };

      const assistantMessage: Message = {
        id: assistantMessageId,
        chat_id: chatId,
        role: "assistant",
        agent_id: null,
        agent_name: "Assistant",
        content: "Mock assistant reply for E2E.",
        metadata: {},
        created_at: timestamp,
      };

      messagesByChat[chatId] = [userMessage, assistantMessage];

      ws.send(
        JSON.stringify({
          type: "complete",
          result: {
            messages: [assistantMessage],
            agent_discussions: [],
            pending_approvals: [],
          },
        }),
      );
    });
  });
}

/**
 * Seed localStorage so the app skips the language picker and uses English.
 *
 * @param page Playwright page under test
 */
export async function seedEnglishLocale(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentforge-language", "en");
  });
}
