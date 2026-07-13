export type OrchestrationMode = "single" | "multi" | "quick";
export type ExecutionStrategy = "auto" | "serial" | "parallel" | "hybrid";
export type ChatRunStatus = "running" | "completed";

export interface ChatMemorySettings {
  memory_tokens: number;
  memory_scope: "chat" | "global";
  enabled: boolean;
}

export interface NewChatDraft {
  mode: OrchestrationMode;
  execution_strategy: ExecutionStrategy;
  role_ids: string[];
  memory: ChatMemorySettings;
}

export interface CommitNewChatPayload {
  execution_strategy: ExecutionStrategy;
  role_ids: string[];
  memory: ChatMemorySettings;
}

export interface AgentRole {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  is_builtin: boolean;
}

export interface Chat {
  id: string;
  title: string;
  mode: OrchestrationMode;
  execution_strategy: ExecutionStrategy;
  role_ids: string[];
  memory: ChatMemorySettings;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  chat_id: string;
  role: "user" | "assistant" | "system" | "agent" | "tool";
  agent_id: string | null;
  agent_name: string | null;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AgentMessage {
  from_agent: string;
  to_agent: string | null;
  content: string;
  timestamp: string;
}

export interface ApprovalRequest {
  id: string;
  chat_id: string;
  action_type: string;
  description: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AppSettings {
  workspace_root: string;
  ollama_base_url: string;
  default_model: string;
  default_memory_tokens: number;
  llm_auto_routing: boolean;
  command_whitelist: string[];
  command_blacklist: string[];
  has_openai_key: boolean;
  has_anthropic_key: boolean;
  has_gemini_key: boolean;
  has_groq_key: boolean;
  has_mistral_key: boolean;
  ui_language: string;
}

export type CloudApiKeyField =
  | "openai_api_key"
  | "anthropic_api_key"
  | "gemini_api_key"
  | "groq_api_key"
  | "mistral_api_key";

export type SettingsSavePayload = Partial<
  AppSettings & {
    openai_api_key?: string;
    openai_api_base?: string;
    anthropic_api_key?: string;
    gemini_api_key?: string;
    groq_api_key?: string;
    mistral_api_key?: string;
    ui_language?: string;
  }
>;

export interface OrchestrationResult {
  chat_id: string;
  messages: Message[];
  agent_discussions: AgentMessage[];
  pending_approvals: ApprovalRequest[];
  title?: string;
  resolved_role_id?: string;
  effective_execution_strategy?: ExecutionStrategy;
}

export interface LLMRoutingInfo {
  auto_routing: boolean;
  default_model: string;
  installed: string[];
  routing: Record<string, string>;
  models: UserModel[];
  tasks: Record<
    string,
    {
      label: string;
      description: string;
      selected: string;
      routing_override: string;
      source?: string;
      model_id?: string;
      display_name?: string;
    }
  >;
}

export interface UserModel {
  id: string;
  ollama_tag: string;
  display_name: string;
  assigned_tasks: string[];
  enabled: boolean;
  notes: string;
  catalog_match?: string | null;
  family?: string;
  ram_gb?: string;
  created_at: string;
  updated_at: string;
}

export interface ModelSuggestion {
  ollama_tag: string;
  display_name: string;
  assigned_tasks: string[];
  catalog_match?: string | null;
  description: string;
  ram_gb: string;
  family: string;
}

export interface ModelCatalogEntry {
  id: string;
  patterns: string[];
  family: string;
  display_name: string;
  recommended_tasks: string[];
  ram_gb: string;
  description: string;
}

export interface SetupTestResult {
  id: string;
  label: string;
  ok: boolean | null;
  skipped?: boolean;
  warning?: boolean;
  message: string;
  models?: string[];
  count?: number;
  model?: string;
}

export interface SetupTestReport {
  all_required_ok: boolean;
  results: SetupTestResult[];
  summary: string;
  optional_issues: number;
}

export interface SetupStatus {
  completed: boolean;
  skipped: boolean;
  current_step: string;
  steps_done: string[];
  steps: string[];
  should_show_wizard: boolean;
  can_resume: boolean;
  last_test_results?: SetupTestReport;
}

export interface WsEvent {
  type: string;
  agent_id?: string;
  agent_name?: string;
  tool?: string;
  discussion?: AgentMessage;
  routing?: Record<string, unknown>;
  result?: OrchestrationResult;
  [key: string]: unknown;
}
