// API Types

export interface Session {
  session_id: string;
  message_count: number;
  created_at: string;
  last_active: string;
  title?: string;
  age_seconds?: number;
  idle_seconds?: number;
}

export interface ToolTrace {
  tool: string;
  arguments: Record<string, any>;
  result?: any;
  duration_ms: number;
  description?: string;
  call_id?: string;  // ★ 唯一标识符，用于匹配并行工具调用
}

// ★ 有序时间线片段 — 支持文本和工具调用按时间顺序交错显示
export type TimelineSegment =
  | { kind: 'text'; content: string }
  | { kind: 'thinking'; content: string }  // ★ 思考过程，可折叠显示
  | { kind: 'tool'; tool: string; arguments: Record<string, any>; duration_ms: number; description?: string; result?: any; status: 'running' | 'done' | 'error'; call_id?: string };  // ★ 唯一标识符

export interface Message {
  id?: string;  // 唯一标识符，用于 React key
  role: 'user' | 'assistant';
  content: string;
  toolTraces?: ToolTrace[];
  htmlContent?: string | null;
  timeline?: TimelineSegment[];  // ★ 新增：有序时间线
}

export interface HistoryResponse {
  session_id: string;
  messages: Message[];
  count: number;
  total: number;
  has_more: boolean;
}

// SSE Event Types
export type SSEEventType =
  | 'thinking'
  | 'tool_start'
  | 'tool_result'
  | 'content_chunk'
  | 'done'
  | 'error'
  | 'stopped'
  | 'heuristic_redirect'
  | 'message_received'
  | 'supplement_injected'
  | 'hybrid_phase';

export interface SSEEvent {
  type: SSEEventType;
  event_id?: number;
  session_id?: string;
  content?: string;
  message?: string;
  tool?: string;
  arguments?: Record<string, any>;
  result?: any;
  duration_ms?: number;
  description?: string;
  call_id?: string;  // ★ 唯一标识符，用于匹配 tool_start 和 tool_result
  error?: string;
  reason?: string;
  tool_traces?: ToolTrace[];
  suggestions?: string[];
  // Hybrid 状态
  phase?: string;
}

// Model Config
export interface ModelConfig {
  id: string;
  name: string;
  provider: 'openai' | 'local';
  base_url: string;
  model: string;
  api_key?: string;
  temperature: number;
  timeout: number;
}

// Config API
export interface ConfigStatus {
  config_dir: string;
  auto_reload: boolean;
  loaded_sections: string[];
  files: string[];
}

export interface MemoryRecord {
  id: string;
  title: string;
  content: string;
  category: string;
  tags: string;
  source_file: string;
  schema_type: 'general' | 'profile' | 'project' | 'issue';
  memory_key: string;
  metadata: Record<string, any>;
  created_at?: string;
  updated_at?: string;
  sensitive: boolean;
  sensitivity_reason: string;
  status: string;
  revisions: number;
  merged_sources: string[];
  deleted_reason: string;
  merged_into: string;
  score: number;
}

export interface MemorySummaryItem {
  name: string;
  count: number;
}

export interface MemorySummary {
  total_records: number;
  active_records: number;
  deleted_records: number;
  forgotten_records: number;
  merged_records: number;
  sensitive_records: number;
  categories: MemorySummaryItem[];
  schemas: MemorySummaryItem[];
  catalog_file: string;
  memory_dir: string;
}

export interface MemoryQueryResponse {
  mode: 'list' | 'search';
  query: string;
  total: number;
  items: MemoryRecord[];
  filters: {
    category?: string | null;
    schema_type?: string | null;
    include_sensitive: boolean;
    include_deleted: boolean;
    limit: number;
  };
}

