import { create } from 'zustand';
import { Message, Session, ModelConfig } from '../types';
import { API, fetchJSON, postJSON } from '../utils/api';

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
  hasRunningTask: boolean;  // ★ 新增：后台任务运行状态

  // Models
  savedModels: ModelConfig[];
  currentModelId: string | null;

  // UI
  sidebarCollapsed: boolean;
  statusOnline: boolean;
  showSettingsPage: boolean;
  settingsTab: string;

  // Actions
  setCurrentSessionId: (id: string | null) => void;
  setSessions: (sessions: Session[]) => void;
  addSession: (session: Session) => void;
  removeSession: (id: string) => void;

  setMessages: (messages: Message[]) => void;
  addMessage: (message: Message) => void;
  prependMessages: (messages: Message[]) => void;
  updateStreamingMessage: (message: Message | null) => void;
  setHasMoreHistory: (hasMore: boolean) => void;
  setHistoryOffset: (offset: number) => void;

  setIsStreaming: (streaming: boolean) => void;
  setIsLoadingMessages: (loading: boolean) => void;
  setHasRunningTask: (running: boolean) => void;  // ★ 新增

  setSavedModels: (models: ModelConfig[]) => void;
  setCurrentModelId: (id: string | null) => void;

  toggleSidebar: () => void;
  setStatusOnline: (online: boolean) => void;
  setShowSettingsPage: (show: boolean) => void;
  setSettingsTab: (tab: string) => void;

  // Async Actions
  fetchSessions: () => Promise<void>;
  createSession: () => Promise<string>;
  switchSession: (sessionId: string) => Promise<void>;
  fetchMessages: (sessionId: string, offset?: number) => Promise<void>;
  checkTaskStatus: (sessionId: string) => Promise<boolean>;  // ★ 新增
  stopTask: (sessionId: string) => Promise<void>;  // ★ 新增
}

export const useAppStore = create<AppState>((set, get) => ({
  // Initial State
  sessions: [],
  currentSessionId: null,
  isLoadingSession: false,
  messages: [],
  isLoadingMessages: false,
  hasMoreHistory: true,
  historyOffset: 0,
  isStreaming: false,
  streamingMessage: null,
  hasRunningTask: false,  // ★ 新增
  savedModels: [],
  currentModelId: null,
  sidebarCollapsed: false,
  statusOnline: false,
  showSettingsPage: false,
  settingsTab: 'model',

  // Actions
  setCurrentSessionId: (id) => set({ currentSessionId: id }),
  setSessions: (sessions) => set({ sessions }),
  addSession: (session) => set((state) => ({
    sessions: [session, ...state.sessions],
  })),
  removeSession: (id) => set((state) => ({
    sessions: state.sessions.filter((s) => s.session_id !== id),
  })),

  setMessages: (messages) => set({ messages }),
  addMessage: (message) => set((state) => ({
    messages: [...state.messages, message],
  })),
  prependMessages: (messages) => set((state) => ({
    messages: [...messages, ...state.messages],
  })),
  updateStreamingMessage: (message) => set({ streamingMessage: message }),
  setHasMoreHistory: (hasMore) => set({ hasMoreHistory: hasMore }),
  setHistoryOffset: (offset) => set({ historyOffset: offset }),

  setIsStreaming: (streaming) => set({ isStreaming: streaming }),
  setIsLoadingMessages: (loading) => set({ isLoadingMessages: loading }),
  setHasRunningTask: (running) => set({ hasRunningTask: running }),

  setSavedModels: (models) => set({ savedModels: models }),
  setCurrentModelId: (id) => set({ currentModelId: id }),

  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setStatusOnline: (online) => set({ statusOnline: online }),
  setShowSettingsPage: (show) => set({ showSettingsPage: show }),
  setSettingsTab: (tab) => set({ settingsTab: tab }),

  // Async Actions
  fetchSessions: async () => {
    try {
      const data = await fetchJSON<{ sessions: Session[]; total: number }>(API.sessionList);
      set({ sessions: data.sessions || [] });
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
      set((state) => ({
        sessions: [newSession, ...state.sessions],
        currentSessionId: data.session_id,
        messages: [],
        historyOffset: 0,
        hasMoreHistory: false,
      }));
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
      hasMoreHistory: true,
    });

    await fetchMessages(sessionId, 0);
  },

  fetchMessages: async (sessionId: string, offset = 0) => {
    const state = get();
    
    // ★ 防重：如果当前没有更多历史或者已经在加载，直接返回
    if (offset === 0 && state.messages.length > 0 && state.currentSessionId === sessionId) {
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
      }>(url);

      // ★ 检查 session 是否已切换（如果在请求期间用户切换了 session）
      if (get().currentSessionId !== sessionId) {
        return;
      }

      if (offset === 0) {
        // Initial load - 直接替换消息
        set({
          messages: data.messages,
          historyOffset: data.count,
          hasMoreHistory: data.has_more,
        });
      } else {
        // Prepend older messages
        set((state) => ({
          messages: [...data.messages, ...state.messages],
          historyOffset: state.historyOffset + data.count,
          hasMoreHistory: data.has_more,
        }));
      }
    } catch (error) {
      console.error('Failed to fetch messages:', error);
    } finally {
      set({ isLoadingMessages: false });
    }
  },

  // ★ 新增：检查后台任务状态
  checkTaskStatus: async (sessionId: string) => {
    try {
      const data = await fetchJSON<{
        session_id: string;
        has_running_task: boolean;
        task_status: string | null;
      }>(API.chatStatus(sessionId));

      set({ hasRunningTask: data.has_running_task });
      return data.has_running_task;
    } catch (error) {
      console.error('Failed to check task status:', error);
      return false;
    }
  },

  // ★ 新增：停止后台任务
  stopTask: async (sessionId: string) => {
    try {
      await postJSON(API.chatStop, { session_id: sessionId });
      set({ hasRunningTask: false, isStreaming: false, streamingMessage: null });
    } catch (error) {
      console.error('Failed to stop task:', error);
    }
  },
}));