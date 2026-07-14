import { create } from 'zustand';
import { Message, Session, ModelConfig } from '../types';
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
  updateStreamingMessage: (message: Message | null) => void;
  setHasMoreHistory: (hasMore: boolean) => void;
  setHistoryOffset: (offset: number) => void;

  setIsStreaming: (streaming: boolean) => void;
  setIsLoadingMessages: (loading: boolean) => void;
  setHasRunningTask: (running: boolean) => void;

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
  checkTaskStatus: (sessionId: string) => Promise<boolean>; 
  stopTask: (sessionId: string) => Promise<void>;  
}

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
      hasMoreHistory: false,  // 切换会话时先设为 false，等 fetchMessages 返回后再更新
      streamingMessage: null,
    });

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
      }>(url);

      if (get().currentSessionId !== sessionId) {
        return;
      }

      if (offset === 0) {
        set((state) => {
          if (state.isStreaming || state.streamingMessage) {
            return {
              historyOffset: data.count,
              hasMoreHistory: data.has_more,
            };
          }

          if (data.messages.length > 0) {
            return {
              messages: data.messages,
              historyOffset: data.count,
              hasMoreHistory: data.has_more,
            };
          }
          return {
            messages: state.messages,
            historyOffset: data.count,
            hasMoreHistory: data.has_more,
          };
        });
      } else {
        set((state) => {
          const getMsgKey = (m: Message) => `${m.role}:${(m.content || '').replace(/\s+/g, ' ').trim().slice(0, 30)}`;
          const existingKeys = new Set(state.messages.map(getMsgKey));
          const newMessages = data.messages.filter(m => !existingKeys.has(getMsgKey(m)));
          return {
            messages: [...newMessages, ...state.messages],
            historyOffset: state.historyOffset + data.count,
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
      }>(API.chatStatus(sessionId));

      set({ hasRunningTask: data.has_running_task });
      return data.has_running_task;
    } catch (error) {
      console.error('Failed to check task status:', error);
      return false;
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