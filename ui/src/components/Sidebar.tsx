import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../stores/appStore';
import { API, patchJSON } from '../utils/api';
import { Icons } from './Icons';
import type { Session, TimelineSegment, ToolTrace } from '../types';

function formatTimeAgo(isoStr: string, t: (key: string) => string): string {
  try {
    const d = new Date(isoStr);
    const sec = (Date.now() - d.getTime()) / 1000;
    if (sec < 60) return t('common.justNow');
    if (sec < 3600) return Math.floor(sec / 60) + t('common.minutesAgo');
    if (sec < 86400) return Math.floor(sec / 3600) + t('common.hoursAgo');
    return d.toLocaleDateString();
  } catch {
    return '';
  }
}

interface SchedulerContext {
  timeline: TimelineSegment[];
  traces: ToolTrace[];
}

/** 根据元数据生成显示名称 */
function getSessionTitle(session: Session, t: (key: string) => string): string {
  if (session.title && session.title.trim()) return session.title.trim();
  if (session.session_id === 'default') return t('session.defaultSession');
  return `${t('session.conversation')} ${(session.message_count || 0)}${t('session.messages')}`;
}

export const Sidebar: React.FC = () => {
  const {
    sessions,
    currentSessionId,
    sidebarCollapsed,
    mobileSidebarOpen,
    createSession,
    switchSession,
    toggleSidebar,
    toggleMobileSidebar,
    fetchSessions,
    updateSession,
    removeSession,
    setShowSettingsPage,
    showSettingsPage,
  } = useAppStore();
  
  const schedulerContextRef = useRef<SchedulerContext>({ timeline: [], traces: [] });

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    let isClosed = false;

    const connect = () => {
      if (isClosed) return;

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/session-events/ws`;

      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        console.log('[WS] Connected to session events');
        // 启动心跳
        heartbeatTimer = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'pong') return;
          if (data.type === 'connected') {
            console.log('[WS] Connected:', data.message);
            return;
          }
          if (data.type === 'chat_event') {
            if (data.data?.scheduler_task) {
              const eventSessionId = data.data.session_id;
              const { currentSessionId, addMessage, updateStreamingMessage, setIsStreaming } = useAppStore.getState();
              const ctx = schedulerContextRef.current;
              
              if (currentSessionId === eventSessionId) {
                if (data.data.type === 'hybrid_phase') {
                  ctx.timeline = [];
                  ctx.traces = [];
                  setIsStreaming(true);
                  updateStreamingMessage({
                    role: 'assistant',
                    content: '',
                    toolTraces: [],
                    timeline: [],
                  });
                  
                  if (data.data.scheduler_task_info) {
                    const info = data.data.scheduler_task_info;
                    addMessage({
                      role: 'user',
                      content: '',
                      type: 'scheduler_trigger',
                      schedulerTaskName: info.task_name,
                    });
                  }
                }
                
                if (data.data.type === 'thinking') {
                  const content = data.data.content || '';
                  const isSystemDefault = ['正在思考...', '正在压缩会话记忆...', '正在根据控制决策压缩上下文...', 
                                            '分析结果中...', '输出被截断，正在补充...', '正在分析工具定义...']
                                            .includes(content);
                  if (!isSystemDefault && content.trim()) {
                    ctx.timeline.push({ kind: 'thinking', content });
                    updateStreamingMessage({
                      role: 'assistant',
                      content: '',
                      toolTraces: ctx.traces,
                      timeline: [...ctx.timeline],
                    });
                  }
                }
                
                if (data.data.type === 'tool_start' && data.data.tool && data.data.arguments) {
                  ctx.timeline.push({
                    kind: 'tool',
                    tool: data.data.tool,
                    arguments: data.data.arguments,
                    duration_ms: 0,
                    description: data.data.description,
                    status: 'running',
                    call_id: data.data.call_id,
                  });
                  ctx.traces.push({
                    tool: data.data.tool,
                    arguments: data.data.arguments,
                    duration_ms: 0,
                    description: data.data.description,
                    call_id: data.data.call_id,
                  });
                  updateStreamingMessage({
                    role: 'assistant',
                    content: '',
                    toolTraces: [...ctx.traces],
                    timeline: [...ctx.timeline],
                  });
                }
                
                if (data.data.type === 'tool_result') {
                  const targetCallId = data.data.call_id;
                  if (targetCallId) {
                    for (let i = ctx.timeline.length - 1; i >= 0; i--) {
                      const seg = ctx.timeline[i];
                      if (seg.kind === 'tool' && seg.call_id === targetCallId && seg.status === 'running') {
                        seg.status = 'done';
                        seg.duration_ms = data.data.duration_ms || 0;
                        seg.result = data.data.result;
                        break;
                      }
                    }
                    for (let i = ctx.traces.length - 1; i >= 0; i--) {
                      if (ctx.traces[i].call_id === targetCallId) {
                        ctx.traces[i] = {
                          ...ctx.traces[i],
                          result: data.data.result,
                          duration_ms: data.data.duration_ms || 0,
                        };
                        break;
                      }
                    }
                    updateStreamingMessage({
                      role: 'assistant',
                      content: '',
                      toolTraces: [...ctx.traces],
                      timeline: [...ctx.timeline],
                    });
                  }
                }
                
                if (data.data.type === 'content_chunk' && data.data.content) {
                  const { streamingMessage: currentStreaming } = useAppStore.getState();
                  const currentContent = currentStreaming?.content || '';
                  updateStreamingMessage({
                    role: 'assistant',
                    content: currentContent + data.data.content,
                    toolTraces: ctx.traces,
                    timeline: ctx.timeline,
                  });
                } else if (data.data.type === 'done') {
                  const { streamingMessage: finalStreaming } = useAppStore.getState();
                  const finalContent = finalStreaming?.content || data.data.content || '';
                  if (finalContent.trim()) {
                    addMessage({
                      role: 'assistant',
                      content: finalContent,
                      toolTraces: ctx.traces,
                      timeline: ctx.timeline,
                    });
                  }
                  updateStreamingMessage(null);
                  setIsStreaming(false);
                }
              }
            }
            return;
          }
          if (data.type === 'session_created') {
            useAppStore.getState().fetchSessions();
          } else if (data.type === 'session_updated') {
            useAppStore.getState().updateSession(data.data);
            const { currentSessionId, messages, isStreaming } = useAppStore.getState();
            if (currentSessionId === data.data.session_id && !isStreaming && data.data.message_count !== messages.length) {
              useAppStore.getState().fetchMessages(data.data.session_id, 0, true);
            }
          } else if (data.type === 'session_deleted') {
            useAppStore.getState().removeSession(data.data.session_id);
          } else if (data.type === 'gene_created' || data.type === 'gene_evolved') {
            console.log('[WS] Gene created/updated/evolved:', data.data);
            window.dispatchEvent(new CustomEvent('gene-created', { detail: data.data }));
          }
        } catch (e) {
          console.error('[WS] parse error:', e);
        }
      };

      ws.onerror = () => {
        console.warn('[WS] Error, will reconnect...');
      };

      ws.onclose = () => {
        console.log('[WS] Disconnected');
        if (heartbeatTimer) {
          clearInterval(heartbeatTimer);
          heartbeatTimer = null;
        }
        if (!isClosed) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };
    };

    connect();

    return () => {
      isClosed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      ws?.close();
    };
  }, []);

  const { t } = useTranslation();

  const handleNewChat = async () => {
    await createSession();
  };

  const handleSessionClick = (sessionId: string) => {
    switchSession(sessionId);
  };

  return (
    <>
      {/* Mobile Overlay */}
      {mobileSidebarOpen && <div className="mobile-overlay show" onClick={toggleMobileSidebar} />}
      
      <div className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''} ${mobileSidebarOpen ? 'mobile-open' : ''}`}>
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <img src="/logo.png" alt="Cellium" />
          <h1>Cellium Agent</h1>
        </div>
        <button className="sidebar-toggle" onClick={toggleSidebar} title={sidebarCollapsed ? t('sidebar.expand') : t('sidebar.collapse')}>
          {sidebarCollapsed ? <Icons.Menu size={20} /> : <Icons.ChevronLeft size={20} />}
        </button>
      </div>

      <button className="btn-new-chat" onClick={handleNewChat}>
        <Icons.Plus size={18} />
        <span className="text-label">{t('sidebar.newChat')}</span>
      </button>

      <div className="session-list">
        {sessions.length === 0 ? (
          <div className="session-empty">{t('sidebar.emptySessions')}</div>
        ) : (
          sessions.map((session) => (
            <SessionItem
              key={session.session_id}
              session={session}
              isActive={session.session_id === currentSessionId}
              collapsed={sidebarCollapsed}
              onClick={() => handleSessionClick(session.session_id)}
              onRenamed={() => fetchSessions()}
            />
          ))
        )}
      </div>

      {/* 底部设置按钮 */}
      <button
        className={`sidebar-settings-btn ${showSettingsPage ? 'active' : ''}`}
        onClick={() => setShowSettingsPage(!showSettingsPage)}
        title={t('sidebar.settings')}
      >
        <Icons.Settings size={18} />
        <span className="text-label">{t('sidebar.settings')}</span>
      </button>
    </div>
    </>
  );
};

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  collapsed: boolean;
  onClick: () => void;
  onRenamed: () => void;
}

const SessionItem: React.FC<SessionItemProps> = ({
  session,
  isActive,
  collapsed,
  onClick,
  onRenamed,
}) => {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const timeLabel = formatTimeAgo(session.last_active || session.created_at, t);

  const startEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditing(true);
    setEditValue(session.title || '');
    setTimeout(() => inputRef.current?.select(), 50);
  };

  const confirmRename = async (e?: React.KeyboardEvent) => {
    if (e && e.key !== 'Enter') return;
    const trimmed = editValue.trim();
    if (!trimmed) { setEditing(false); return; }

    try {
      await patchJSON(API.sessionRename(session.session_id), { title: trimmed });
      onRenamed();
    } catch (err) {
      console.error(t('session.renameFailed'), err);
    }
    setEditing(false);
  };

  const cancelEdit = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') setEditing(false);
  };

  if (editing && !collapsed) {
    return (
      <div className={`session-item ${isActive ? 'active' : ''}`}>
        <input
          ref={inputRef}
          className="session-title-edit"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onKeyDown={(e) => { e.stopPropagation(); confirmRename(e); cancelEdit(e); }}
          onBlur={() => setEditing(false)}
          autoFocus
        />
      </div>
    );
  }

  return (
    <div
      className={`session-item ${isActive ? 'active' : ''}`}
      onClick={onClick}
      title={session.session_id}
    >
      <span className="session-title" onDoubleClick={startEdit}>
        {getSessionTitle(session, t)}
      </span>
      <span className="session-time">{timeLabel}</span>
    </div>
  );
};