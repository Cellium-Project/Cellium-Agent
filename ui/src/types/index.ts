// API Types

export interface Attachment {
  file_id: string;
  filename: string;
  file_type: string;
  file_size: number;
  url: string;
  local_path: string;  // 本地文件路径
  upload_time: string;
}

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
  call_id?: string;  
}

export type TimelineSegment =
  | { kind: 'text'; content: string }
  | { kind: 'thinking'; content: string }  
  | { kind: 'tool'; tool: string; arguments: Record<string, any>; duration_ms: number; description?: string; result?: any; status: 'running' | 'done' | 'error'; call_id?: string };  // ★ 唯一标识符

export interface Message {
  id?: string;  // 唯一标识符，用于 React key
  role: 'user' | 'assistant';
  content: string;
  toolTraces?: ToolTrace[];
  htmlContent?: string | null;
  timeline?: TimelineSegment[]; 
  type?: 'scheduler_trigger';  // 特殊消息类型
  schedulerTaskName?: string;  // 定时任务名称
  attachments?: Attachment[];  // 附件列表
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
  // 定时任务标记
  scheduler_task?: boolean;
  // 定时任务信息
  scheduler_task_info?: {
    task_id: string;
    task_name: string;
    triggered_at: string;
    run_count: number;
  };
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

