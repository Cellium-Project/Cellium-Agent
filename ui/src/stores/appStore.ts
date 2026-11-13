import { create } from 'zustand';
import { Message, Session, ModelConfig, DelegationActivity } from '../types';
import { API, fetchJSON, postJSON } from '../utils/api';
import i18n from '../i18n';

export type Theme = 'light' | 'dark' | 'auto';
export type Language = 'zh-CN' | 'zh-TW' | 'en';

interface AppState {
  // Sessions
  sessions: Session[];
  currentSessionId: string | null;
  isLoadingSession: boolean;

  // Messages
  messages: Message[];
  isLoadingMessages: boolean;
  hasMoreHistory: boolean;
  historyOffset: number;

  // Streaming
  isStreaming: boolean;
  streamingMessage: Message | null;
  hasRunningTask: boolean;

  // Agent mode (run / plan / delegate)
  agentMode: 'run' | 'plan' | 'delegate';

  // 会话委托进行中的状态条
  delegationActivity: DelegationActivity[];
  upsertDelegationActivity: (event: {
    task_id?: string; target?: string; target_title?: string; name?: string;
    purpose?: string; status?: string; current_tool?: string; last_tool?: string;
    last_reasoning?: string; tool_calls?: number; elapsed_s?: number;
    error?: string; success?: boolean; stale?: boolean;
  }) => void;
  removeDelegationActivity: (taskId: string) => void;
  clearDelegationActivity: () => void;

  // Models
  savedModels: ModelConfig[];
  currentModelId: string | null;

  // UI
  sidebarCollapsed: boolean;
  statusOnline: boolean;
  showSettingsPage: boolean;
  settingsTab: string;
  mobileSidebarOpen: boolean;

  // Theme & Language
  theme: Theme;
  language: Language;

  // Actions
  setCurrentSessionId: (id: string | null) => void;
  setSessions: (sessions: Session[]) => void;
  addSession: (session: Session) => void;
  removeSession: (id: string) => void;
  updateSession: (session: Session) => void;

  setMessages: (messages: Message[]) => void;
  addMessage: (message: Message) => void;
  prependMessages: (messages: Message[]) => void;
  upsertMessage: (message: Message) => void;
  markLastUserRewindable: (turnId: string) => void;
  rewindMessagesFrom: (messageId: string) => void;
  updateStreamingMessage: (message: Message | null) => void;
  commitStreamingMessage: (message: Message) => void;
  setHasMoreHistory: (hasMore: boolean) => void;
  setHistoryOffset: (offset: number) => void;

  setIsStreaming: (streaming: boolean) => void;
  setIsLoadingMessages: (loading: boolean) => void;
  setHasRunningTask: (running: boolean) => void;
  setAgentMode: (mode: 'run' | 'plan' | 'delegate') => void;

  setSavedModels: (models: ModelConfig[]) => void;
  setCurrentModelId: (id: string | null) => void;

  toggleSidebar: () => void;
  toggleMobileSidebar: () => void;
  setStatusOnline: (online: boolean) => void;
  setShowSettingsPage: (show: boolean) => void;
  setSettingsTab: (tab: string) => void;

  // Theme & Language Actions
  setTheme: (theme: Theme) => void;
  setLanguage: (lang: Language) => void;
  initTheme: () => void;

  // Async Actions
  fetchSessions: () => Promise<void>;
  createSession: () => Promise<string>;
  switchSession: (sessionId: string) => Promise<void>;
  fetchMessages: (sessionId: string, offset?: number, force?: boolean) => Promise<void>;
  checkTaskStatus: (sessionId: string) => Promise<{ hasRunningTask: boolean; lastEventId: number }>; 
  stopTask: (sessionId: string) => Promise<void>;  
}

let streamMessageSeq = 0;
const newStreamMessageId = () => `stream-${Date.now().toString(36)}-${++streamMessageSeq}`;

const optimisticSessions = new Map<string, { session: Session; ts: number }>();

export const useAppStore = create<AppState>((set, get) => ({
  // Initial State
  sessions: [],
  currentSessionId: null,
  isLoadingSession: false,
  messages: [],
  isLoadingMessages: false,
  hasMoreHistory: false,  // 初始化为 false，防止初始加载时触发滚动加载
  historyOffset: 0,
  isStreaming: false,
  streamingMessage: null,
  hasRunningTask: false,
  agentMode: 'run',
  delegationActivity: [],
  savedModels: [],
  currentModelId: null,
  sidebarCollapsed: false,
  statusOnline: false,
  showSettingsPage: false,
  settingsTab: 'model',
  mobileSidebarOpen: false,

  // Theme & Language Initial State
  theme: (localStorage.getItem('theme') as Theme) || 'auto',
  language: (localStorage.getItem('language') as Language) || 'zh-CN',

  // Actions
  setCurrentSessionId: (id) => set({ currentSessionId: id }),
  setSessions: (sessions) => set({ sessions }),
  addSession: (session) => set((state) => ({
    sessions: [session, ...state.sessions],
  })),
  removeSession: (id) => set((state) => ({
    sessions: state.sessions.filter((s) => s.session_id !== id),
  })),
  updateSession: (updated) => set((state) => ({
    sessions: state.sessions.map((s) =>
      s.session_id === updated.session_id ? { ...s, ...updated } : s
    ),
  })),

  setMessages: (messages) => set({ messages }),
  upsertDelegationActivity: (event) => set((state) => {
    const id = event.task_id || `session:${event.target || event.name || ''}`;
    if (!id || id === 'session:') return {};
    const idx = state.delegationActivity.findIndex(a => a.task_id === id);
    const base = idx >= 0 ? state.delegationActivity[idx] : null;
    const terminal = new Set(['done', 'error', 'timeout', 'cancelled', 'unknown']);
    const incomingStatus = event.status ?? base?.status ?? 'running';
    const status = base?.status && terminal.has(base.status) && !terminal.has(incomingStatus)
      ? base.status
      : incomingStatus;
    const item: DelegationActivity = {
      task_id: id,
      target: event.target ?? base?.target ?? '',
      target_title: event.target_title ?? base?.target_title,
      purpose: event.purpose ?? base?.purpose,
      status,
      current_tool: event.current_tool ?? (event.status ? undefined : base?.current_tool),
      last_tool: event.last_tool ?? base?.last_tool,
      last_reasoning: event.last_reasoning ?? base?.last_reasoning,
      tool_calls: event.tool_calls ?? base?.tool_calls,
      elapsed_s: event.elapsed_s ?? base?.elapsed_s,
      error: event.error ?? (event.status ? undefined : base?.error),
      success: event.success ?? base?.success,
      stale: event.stale ?? (event.status ? false : base?.stale),
      updatedAt: Date.now(),
    };
    const next = idx >= 0
      ? state.delegationActivity.map((a, i) => (i === idx ? item : a))
      : [...state.delegationActivity, item];
    return { delegationActivity: next };
  }),
  removeDelegationActivity: (taskId) => set((state) => ({
    delegationActivity: state.delegationActivity.filter(a => a.task_id !== taskId),
  })),
  clearDelegationActivity: () => set({ delegationActivity: [] }),
  addMessage: (message) => set((state) => ({
    messages: [...state.messages, message],
  })),
  prependMessages: (messages) => set((state) => ({
    messages: [...messages, ...state.messages],
  })),
  upsertMessage: (message) => set((state) => {
    const idx = state.messages.findIndex((m) => m.id === message.id);
    if (idx < 0) {
      return { messages: [...state.messages, message] };
    }
    const next = [...state.messages];
    next[idx] = message;
    return { messages: next };
  }),
  markLastUserRewindable: (turnId) => set((state) => {
    for (let i = state.messages.length - 1; i >= 0; i--) {
      const m = state.messages[i];
      if (m.role !== 'user') continue;
      const next = [...state.messages];
      next[i] = { ...m, id: m.id || turnId, can_rewind: true };
      return { messages: next };
    }
    return {};
  }),
  rewindMessagesFrom: (messageId) => set((state) => {
    const idx = state.messages.findIndex((m) => m.id === messageId);
    if (idx < 0) return {};
    return {
      messages: state.messages.slice(0, idx),
      streamingMessage: null,
      isStreaming: false,
      hasRunningTask: false,
    };
  }),
  // 流式消息保持稳定 id，落地时沿用同一 key，避免整行 DOM 重挂载导致闪烁
  updateStreamingMessage: (message) => set((state) => {
    if (!message) return { streamingMessage: null };
    const id = message.id || state.streamingMessage?.id || newStreamMessageId();
    return { streamingMessage: { ...message, id } };
  }),
  // 落地与清空在同一次更新内完成，避免出现"流式已清空、正式消息未到"的中间帧
  commitStreamingMessage: (message) => set((state) => {
    const id = message.id || state.streamingMessage?.id;
    const committed = id ? { ...message, id } : message;
    const exists = Boolean(id) && state.messages.some((m) => m.id === id);
    return {
      messages: exists ? state.messages : [...state.messages, committed],
      streamingMessage: null,
    };
  }),
  setHasMoreHistory: (hasMore) => set({ hasMoreHistory: hasMore }),
  setHistoryOffset: (offset) => set({ historyOffset: offset }),

  setIsStreaming: (streaming) => set({ isStreaming: streaming }),
  setIsLoadingMessages: (loading) => set({ isLoadingMessages: loading }),
  setHasRunningTask: (running) => set({ hasRunningTask: running }),
  setAgentMode: (mode) => set({
    agentMode: mode === 'plan' || mode === 'delegate' ? mode : 'run',
  }),

  setSavedModels: (models) => set({ savedModels: models }),
  setCurrentModelId: (id) => set({ currentModelId: id }),

  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  toggleMobileSidebar: () => set((state) => ({ mobileSidebarOpen: !state.mobileSidebarOpen })),
  setStatusOnline: (online) => set({ statusOnline: online }),
  setShowSettingsPage: (show) => set({ showSettingsPage: show }),
  setSettingsTab: (tab) => set({ settingsTab: tab }),

  // Theme & Language Actions
  setTheme: (theme) => {
    localStorage.setItem('theme', theme);
    set({ theme });
    
    // Apply theme
    const root = document.documentElement;
    if (theme === 'dark') {
      root.setAttribute('data-theme', 'dark');
    } else if (theme === 'light') {
      root.removeAttribute('data-theme');
    } else {
      // Auto - follow system
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      if (prefersDark) {
        root.setAttribute('data-theme', 'dark');
      } else {
        root.removeAttribute('data-theme');
      }
    }
  },
  
  setLanguage: (lang) => {
    localStorage.setItem('language', lang);
    set({ language: lang });
    i18n.changeLanguage(lang);
  },
  
  initTheme: () => {
    const { theme } = get();
    const root = document.documentElement;
    
    if (theme === 'dark') {
      root.setAttribute('data-theme', 'dark');
    } else if (theme === 'light') {
      root.removeAttribute('data-theme');
    } else {
      // Auto
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      if (prefersDark) {
        root.setAttribute('data-theme', 'dark');
      } else {
        root.removeAttribute('data-theme');
      }
      
      // Listen for system theme changes
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        const currentTheme = get().theme;
        if (currentTheme === 'auto') {
          if (e.matches) {
            root.setAttribute('data-theme', 'dark');
          } else {
            root.removeAttribute('data-theme');
          }
        }
      });
    }
    
    // Init language
    const { language } = get();
    i18n.changeLanguage(language);
  },

  // Async Actions
  fetchSessions: async () => {
    try {
      const data = await fetchJSON<{ sessions: Session[]; total: number }>(API.sessionList);
      const remote = data.sessions || [];
      const now = Date.now();
      const remoteIds = new Set(remote.map((s) => s.session_id));
      for (const [id, entry] of optimisticSessions) {
        if (remoteIds.has(id) || now - entry.ts > 60000) optimisticSessions.delete(id);
      }
      const extras = Array.from(optimisticSessions.values())
        .filter((e) => !remoteIds.has(e.session.session_id))
        .map((e) => e.session);
      set({ sessions: extras.length ? [...extras, ...remote] : remote });
    } catch (error) {
      console.error('Failed to fetch sessions:', error);
    }
  },

  createSession: async () => {
    try {
      const data = await postJSON<{ session_id: string; created_at: string }>(
        API.sessionCreate,
        {}
      );
      const newSession: Session = {
        session_id: data.session_id,
        message_count: 0,
        created_at: data.created_at,
        last_active: data.created_at,
      };
      optimisticSessions.set(data.session_id, { session: newSession, ts: Date.now() });
      set((state) => ({
        sessions: [newSession, ...state.sessions],
        currentSessionId: data.session_id,
        messages: [],
        historyOffset: 0,
        hasMoreHistory: false,
        delegationActivity: [],
      }));
      postJSON(API.sessionActivate(data.session_id, 'webui'), {}).catch(() => {});
      return data.session_id;
    } catch (error) {
      console.error('Failed to create session:', error);
      throw error;
    }
  },

  switchSession: async (sessionId: string) => {
    const { currentSessionId, fetchMessages } = get();
    if (sessionId === currentSessionId) return;

    set({
      currentSessionId: sessionId,
      messages: [],
      historyOffset: 0,
      hasMoreHistory: false,  // 切换会话时先设为 false，等 fetchMessages 返回后再更新
      streamingMessage: null,
      isStreaming: false,
      delegationActivity: [],
      hasRunningTask: false,
    });

    postJSON(API.sessionActivate(sessionId, 'webui'), {}).catch(() => {});
    await fetchMessages(sessionId, 0);
  },

  fetchMessages: async (sessionId: string, offset = 0, force = false) => {
    const state = get();

    // 防重：如果当前已经在加载，直接返回
    if (!force && offset === 0 && state.messages.length > 0 && state.currentSessionId === sessionId) {
      return;
    }

    // 如果没有更多历史，跳过
    if (!force && offset > 0 && !state.hasMoreHistory) {
      return;
    }

    set({ isLoadingMessages: true });
    try {
      const limit = 150;
      const url = `${API.sessionHistory(sessionId)}?limit=${limit}&offset=${offset}`;
      const data = await fetchJSON<{
        session_id: string;
        messages: Message[];
        count: number;
        total: number;
        has_more: boolean;
        next_offset: number;
      }>(url);

      if (get().currentSessionId !== sessionId) {
        return;
      }

      const nextOffset = typeof data.next_offset === 'number' ? data.next_offset : data.count;

      if (offset === 0) {
        set((state) => {
          if (data.messages.length > 0) {
            // 刷新/重连时可能与流式恢复并发：服务端历史必须落地，
            // 只保留服务端还没有的本地消息（刚发出的、尚未归档的）
            const serverIds = new Set(data.messages.map((m) => m.id).filter(Boolean));
            const serverKeys = new Set(
              data.messages.map((m) => `${m.role}|${(m.content || '').trim()}`)
            );
            const localOnly = state.messages.filter((m) => {
              if (m.id) return !serverIds.has(m.id);
              return !serverKeys.has(`${m.role}|${(m.content || '').trim()}`);
            });
            return {
              messages: [...data.messages, ...localOnly],
              historyOffset: nextOffset,
              hasMoreHistory: data.has_more,
            };
          }
          return {
            messages: state.messages,
            historyOffset: nextOffset,
            hasMoreHistory: data.has_more,
          };
        });
      } else {
        set((state) => {
          const seen = new Set(state.messages.map(m => m.id).filter(Boolean));
          const incoming = data.messages.filter(m => !m.id || !seen.has(m.id));
          return {
            messages: [...incoming, ...state.messages],
            historyOffset: Math.max(state.historyOffset, nextOffset),
            hasMoreHistory: data.has_more,
          };
        });
      }
    } catch (error) {
      console.error('Failed to fetch messages:', error);
    } finally {
      set({ isLoadingMessages: false });
    }
  },

  checkTaskStatus: async (sessionId: string) => {
    try {
      const data = await fetchJSON<{
        session_id: string;
        has_running_task: boolean;
        task_status: string | null;
        last_event_id?: number;
      }>(API.chatStatus(sessionId));

      set({ hasRunningTask: data.has_running_task });
      return { hasRunningTask: data.has_running_task, lastEventId: Number(data.last_event_id) || 0 };
    } catch (error) {
      console.error('Failed to check task status:', error);
      return { hasRunningTask: false, lastEventId: 0 };
    }
  },

  stopTask: async (sessionId: string) => {
    try {
      await postJSON(API.chatStop, { session_id: sessionId });
      set({ hasRunningTask: false });
    } catch (error) {
      console.error('Failed to stop task:', error);
    }
  },
}));