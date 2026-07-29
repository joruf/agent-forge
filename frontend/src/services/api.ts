import type {
  AgentRole,
  AppSettings,
  ApprovalRequest,
  Chat,
  ChatMemorySettings,
  ExecutionStrategy,
  LLMRoutingInfo,
  Message,
  ModelCatalogEntry,
  ModelPerformanceEntry,
  ModelPerformanceProgress,
  ModelPerformanceReport,
  ModelSuggestion,
  OrchestrationMode,
  OrchestrationResult,
  SettingsSavePayload,
  SetupStatus,
  SetupTestReport,
  ReadinessReport,
  ContextPluginInfo,
  ContextSnapshot,
  UserModel,
} from "../types";

const API_BASE =
  import.meta.env.VITE_API_BASE ??
  (import.meta.env.DEV ? "/api" : "http://127.0.0.1:8765/api");

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  getReadiness: (includeInference = false) =>
    request<ReadinessReport>(`/readiness?include_inference=${includeInference ? "true" : "false"}`),

  getContextCatalog: () =>
    request<{ plugins: ContextPluginInfo[]; enabled: string[] }>("/context/catalog"),

  getContextStartup: (refresh = false) =>
    request<ContextSnapshot>(`/context/startup?refresh=${refresh ? "true" : "false"}`),

  getSettings: () => request<AppSettings>("/settings"),

  updateSettings: (data: SettingsSavePayload) =>
    request<AppSettings>("/settings", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  testModelAccess: (data: {
    ollama_base_url?: string;
    default_model?: string;
    openai_api_key?: string;
    anthropic_api_key?: string;
    gemini_api_key?: string;
    groq_api_key?: string;
    mistral_api_key?: string;
    test_inference?: boolean;
  }) =>
    request<SetupTestReport>("/settings/test-models", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getLLMRouting: () => request<LLMRoutingInfo>("/llm/routing"),

  getModelPerformance: () => request<ModelPerformanceReport>("/llm/performance"),

  runModelPerformanceBenchmark: () =>
    request<ModelPerformanceReport>("/llm/performance/benchmark", {
      method: "POST",
    }),

  runStartupModelPerformanceBenchmark: () =>
    request<{ started: boolean }>("/llm/performance/benchmark/startup", {
      method: "POST",
    }),

  runModelPerformanceBenchmarkStream: async (
    onProgress: (progress: ModelPerformanceProgress, entry?: ModelPerformanceEntry) => void,
  ): Promise<ModelPerformanceReport> => {
    const response = await fetch(`${API_BASE}/llm/performance/benchmark/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || response.statusText);
    }
    if (!response.body) {
      throw new Error("Missing benchmark stream body");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalReport: ModelPerformanceReport | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }
        const event = JSON.parse(line) as {
          type: string;
          completed?: number;
          total?: number;
          current_model?: string;
          entry?: ModelPerformanceEntry;
          report?: ModelPerformanceReport;
        };
        if (event.type === "progress") {
          onProgress(
            {
              completed: Number(event.completed ?? 0),
              total: Number(event.total ?? 0),
              current_model: String(event.current_model ?? ""),
            },
            event.entry,
          );
        } else if (event.type === "complete" && event.report) {
          finalReport = event.report;
          onProgress(
            {
              completed: event.report.total_count,
              total: event.report.total_count,
              current_model: "",
            },
          );
        }
      }
    }

    if (!finalReport) {
      throw new Error("Benchmark stream ended without a result");
    }
    return finalReport;
  },

  getModelCatalog: () =>
    request<{ tasks: Record<string, { label: string; description: string }>; entries: ModelCatalogEntry[] }>(
      "/llm/catalog",
    ),

  listUserModels: () => request<UserModel[]>("/llm/registry"),

  suggestModel: (ollama_tag: string) =>
    request<ModelSuggestion>("/llm/registry/suggest", {
      method: "POST",
      body: JSON.stringify({ ollama_tag, auto_suggest: true }),
    }),

  createUserModel: (data: {
    ollama_tag: string;
    display_name?: string;
    assigned_tasks?: string[];
    enabled?: boolean;
    notes?: string;
    auto_suggest?: boolean;
  }) =>
    request<UserModel>("/llm/registry", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateUserModel: (
    id: string,
    data: Partial<{
      ollama_tag: string;
      display_name: string;
      assigned_tasks: string[];
      enabled: boolean;
      notes: string;
    }>,
  ) =>
    request<UserModel>(`/llm/registry/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteUserModel: (id: string) =>
    request<{ deleted: boolean }>(`/llm/registry/${id}`, { method: "DELETE" }),

  updateRouting: (routing: Record<string, string>) =>
    request<{ routing: Record<string, string>; tasks: LLMRoutingInfo["tasks"] }>(
      "/llm/routing",
      { method: "PATCH", body: JSON.stringify({ routing }) },
    ),

  syncOllamaModels: () =>
    request<{ added: UserModel[]; count: number; models: UserModel[] }>(
      "/llm/registry/sync",
      { method: "POST" },
    ),

  listOllamaModels: (refresh = false) =>
    request<{ models: string[]; ollama_base_url: string; count: number }>(
      `/llm/models?refresh=${refresh ? "true" : "false"}`,
    ),

  listRoles: () => request<AgentRole[]>("/roles"),

  createRole: (role: Omit<AgentRole, "is_builtin">) =>
    request<AgentRole>("/roles", {
      method: "POST",
      body: JSON.stringify(role),
    }),

  updateRole: (
    id: string,
    data: Pick<AgentRole, "name" | "description" | "system_prompt">,
  ) =>
    request<AgentRole>(`/roles/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteRole: (id: string) =>
    request<{ deleted: boolean }>(`/roles/${id}`, { method: "DELETE" }),

  listChats: () => request<Chat[]>("/chats"),

  createChat: (data: {
    title?: string;
    mode?: OrchestrationMode;
    execution_strategy?: ExecutionStrategy;
    role_ids?: string[];
    memory?: ChatMemorySettings;
    grill_enabled?: boolean;
  }) =>
    request<Chat>("/chats", {
      method: "POST",
      body: JSON.stringify({
        title: data.title ?? "New Chat",
        mode: data.mode ?? "single",
        execution_strategy: data.execution_strategy ?? "auto",
        role_ids: data.role_ids ?? [],
        memory: data.memory,
        grill_enabled: data.grill_enabled ?? false,
      }),
    }),

  getChat: (id: string) => request<Chat>(`/chats/${id}`),

  updateChat: (
    id: string,
    data: Partial<{
      title: string;
      mode: OrchestrationMode;
      execution_strategy: ExecutionStrategy;
      role_ids: string[];
      memory: ChatMemorySettings;
      grill_enabled: boolean;
    }>,
  ) =>
    request<Chat>(`/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteChat: (id: string) =>
    request<{ deleted: boolean }>(`/chats/${id}`, { method: "DELETE" }),

  listMessages: (chatId: string) =>
    request<Message[]>(`/chats/${chatId}/messages`),

  sendMessage: (
    chatId: string,
    content: string,
    mode?: OrchestrationMode,
    roleIds?: string[],
    retry = false,
  ) =>
    request<OrchestrationResult>(`/chats/${chatId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, mode, role_ids: roleIds, retry }),
    }),

  listApprovals: (chatId: string) =>
    request<ApprovalRequest[]>(`/chats/${chatId}/approvals`),

  respondApproval: (
    chatId: string,
    approvalId: string,
    approved: boolean,
    comment = "",
    choiceId?: string,
  ) =>
    request<{ approved: boolean }>(
      `/chats/${chatId}/approvals/${approvalId}`,
      {
        method: "POST",
        body: JSON.stringify({
          approved,
          comment,
          ...(choiceId ? { choice_id: choiceId } : {}),
        }),
      },
    ),

  wsUrl: (chatId: string) => {
    const configured = import.meta.env.VITE_API_BASE?.trim();
    if (configured && /^https?:\/\//i.test(configured)) {
      const base = configured.replace(/^http/i, "ws").replace(/\/api\/?$/, "");
      return `${base}/api/ws/chats/${chatId}`;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/api/ws/chats/${chatId}`;
  },

  getSetupStatus: () => request<SetupStatus>("/setup/status"),

  updateSetupStep: (step: string) =>
    request<SetupStatus>("/setup/step", {
      method: "PATCH",
      body: JSON.stringify({ step }),
    }),

  skipSetup: () => request<SetupStatus>("/setup/skip", { method: "POST" }),

  resumeSetup: () => request<SetupStatus>("/setup/resume", { method: "POST" }),

  completeSetup: () => request<SetupStatus>("/setup/complete", { method: "POST" }),

  runSetupTests: (data: {
    ollama_base_url?: string;
    workspace_root?: string;
    openai_api_key?: string;
    test_generate?: boolean;
  }) =>
    request<SetupTestReport>("/setup/test", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  setupSyncModels: () =>
    request<{ added: UserModel[]; count: number; total: number }>(
      "/setup/sync-models",
      { method: "POST" },
    ),
};
