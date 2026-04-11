import React, { useState, useRef } from 'react';
import { useAppStore } from '../stores/appStore';
import { API, patchJSON } from '../utils/api';
import { Icons } from './Icons';
import type { Session } from '../types';

function formatTimeAgo(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    const sec = (Date.now() - d.getTime()) / 1000;
    if (sec < 60) return '刚刚';
    if (sec < 3600) return Math.floor(sec / 60) + '分钟前';
    if (sec < 86400) return Math.floor(sec / 3600) + '小时前';
    return d.toLocaleDateString();
  } catch {
    return '';
  }
}

/** 根据元数据生成显示名称 */
function getSessionTitle(session: Session): string {
  if (session.title && session.title.trim()) return session.title.trim();
  if (session.session_id === 'default') return '默认会话';
  return `对话 ${(session.message_count || 0)}条`;
}

export const Sidebar: React.FC = () => {
  const {
    sessions,
    currentSessionId,
    sidebarCollapsed,
    createSession,
    switchSession,
    toggleSidebar,
    fetchSessions,
    setShowSettingsPage,
    showSettingsPage,
  } = useAppStore();

  React.useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleNewChat = async () => {
    await createSession();
  };

  const handleSessionClick = (sessionId: string) => {
    switchSession(sessionId);
  };

  return (
    <div className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <img src="/logo.png" alt="Cellium" />
          <h1>Cellium Agent</h1>
        </div>
        <button className="sidebar-toggle" onClick={toggleSidebar} title="折叠侧边栏">
          {sidebarCollapsed ? <Icons.Menu size={20} /> : <Icons.ChevronLeft size={20} />}
        </button>
      </div>

      <button className="btn-new-chat" onClick={handleNewChat}>
        <Icons.Plus size={18} />
        <span className="text-label">新建对话</span>
      </button>

      <div className="session-list">
        {sessions.length === 0 ? (
          <div className="session-empty">暂无历史会话</div>
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
        title={showSettingsPage ? "返回对话" : "设置"}
      >
        {showSettingsPage ? <Icons.Chat size={18} /> : <Icons.Settings size={18} />}
        <span className="text-label">{showSettingsPage ? "返回对话" : "设置"}</span>
      </button>
    </div>
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
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const timeLabel = formatTimeAgo(session.last_active || session.created_at);

  // 进入编辑模式
  const startEdit = (e: React.MouseEvent) => {
    e.stopPropagation(); // 阻止触发切换会话
    setEditing(true);
    setEditValue(session.title || '');
    setTimeout(() => inputRef.current?.select(), 50);
  };

  // 确认重命名
  const confirmRename = async (e?: React.KeyboardEvent) => {
    if (e && e.key !== 'Enter') return;
    const trimmed = editValue.trim();
    if (!trimmed) { setEditing(false); return; }

    try {
      await patchJSON(API.sessionRename(session.session_id), { title: trimmed });
      onRenamed();
    } catch (err) {
      console.error('重命名失败:', err);
    }
    setEditing(false);
  };

  // 取消编辑
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
        {getSessionTitle(session)}
      </span>
      <span className="session-time">{timeLabel}</span>
    </div>
  );
};
