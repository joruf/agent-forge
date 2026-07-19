export type OrchestrationMode = "single" | "multi" | "quick" | "grill";
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
  grill_enabled: boolean;
}

export interface CommitNewChatPayload {
  execution_strategy: ExecutionStrategy;
  role_ids: string[];
  memory: ChatMemorySettings;
  grill_enabled: boolean;
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
  grill_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptCorrection {
  original: string;
  corrected: string;
  reason?: string;
}

export type TaskBoardStepStatus = "pending" | "active" | "done";

export interface TaskBoardStep {
  step_id: number;
  action: string;
  assignee: string;
  detail: string;
  path: string | null;
  status: TaskBoardStepStatus;
}

export interface TaskBoardSnapshot {
  task_type: string;
  complete: boolean;
  reason: string;
  targets: string[];
  steps: TaskBoardStep[];
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

export interface UserChoiceOption {
  id: string;
  label: string;
  description?: string;
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

export interface ReadinessReport {
  chat_ready: boolean;
  active_model: string;
  results: SetupTestResult[];
  summary: string;
  blocking_message?: string | null;
  blocking_id?: string | null;
}

export interface ContextPluginInfo {
  id: string;
  name: string;
  description: string;
  timing: string;
  api_name: string;
  api_url: string;
  api_key_required: boolean;
  license_note: string;
  trigger_keywords: string[];
  docs_url?: string;
}

export interface ContextSnapshot {
  mode?: string;
  text: string;
  required_plugins?: string[];
  results: Array<{
    plugin_id: string;
    plugin_name?: string;
    ok: boolean;
    text: string;
    error?: string | null;
    data?: Record<string, unknown>;
  }>;
  selection?: Array<{ plugin_id: string; reason: string }>;
  cached?: boolean;
  enabled?: boolean;
}

export interface ContextPluginRun {
  plugin_id: string;
  plugin_name: string;
  status: "running" | "ok" | "error";
  text?: string;
  error?: string | null;
  timestamp: string;
  reason?: string;
}

export type ShellCommandStatus = "success" | "failed" | "blocked" | "pending" | "denied";

export interface ShellCommandEntry {
  id: string;
  command: string;
  cwd?: string;
  status: ShellCommandStatus;
  success: boolean;
  exit_code?: number | null;
  agent_id?: string | null;
  agent_name?: string | null;
  approval_id?: string;
  output?: string;
  timestamp: string;
}

export type AgentActivityStatus = "running" | "done" | "failed";

export interface AgentActivityItem {
  id: string;
  agentId: string;
  agentName: string;
  description: string;
  status: AgentActivityStatus;
  tool?: string;
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
