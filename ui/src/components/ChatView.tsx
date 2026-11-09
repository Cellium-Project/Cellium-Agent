import React, { useState, useRef, useEffect, memo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from './ConfirmDialog';
import { useChat } from '../hooks/useChat';
import { useAppStore } from '../stores/appStore';
import { API, fetchJSON, postJSON } from '../utils/api';
import { Icons } from './Icons';
import { ChatMessage } from './ChatMessage';
import { CustomDropdown } from './CustomDropdown';
import type { Attachment, Message } from '../types';

type AgentMode = 'run' | 'plan' | 'delegate';

const toAgentMode = (mode?: string): AgentMode =>
  mode === 'plan' ? 'plan' : mode === 'delegate' ? 'delegate' : 'run';

const MODE_ORDER: AgentMode[] = ['run', 'plan', 'delegate'];

const MessageList = memo(({
  messages, streamingMessage,
  messagesEndRef, messagesContainerRef,
  isLoadingMessages, hasMoreHistory, currentSessionId,
  fetchMessages, isStreaming, onRewind,
}: {
  messages: Message[];
  streamingMessage: Message | null;
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  messagesContainerRef: React.RefObject<HTMLDivElement | null>;
  isLoadingMessages: boolean;
  hasMoreHistory: boolean;
  currentSessionId: string | null;
  fetchMessages: (sessionId: string, offset?: number) => Promise<void>;
  isStreaming: boolean;
  onRewind?: (id: string) => void;
}) => {
  const { t } = useTranslation();
  const prevSessionIdRef = useRef<string | null>(null);
  const historyLoadSnapshotRef = useRef<{ scrollTop: number; scrollHeight: number } | null>(null);
  const isLoadingHistoryRef = useRef<boolean>(false);
  const needsScrollToBottomRef = useRef<boolean>(false); // 切换对话后需要滚动到底部
  const loadMoreLockRef = useRef<boolean>(false);
  const lastLoadMoreRef = useRef<number>(0);

  const allMessages = streamingMessage
    ? [...messages, streamingMessage]
    : messages;

  useEffect(() => {
    const prevSession = prevSessionIdRef.current;
    const wasLoadingHistory = isLoadingHistoryRef.current;
    const needsScrollToBottom = needsScrollToBottomRef.current;

    prevSessionIdRef.current = currentSessionId;

    // 切换对话时重置状态，标记需要滚动到底部
    if (prevSession !== currentSessionId) {
      historyLoadSnapshotRef.current = null;
      isLoadingHistoryRef.current = false;
      needsScrollToBottomRef.current = true;
      return;
    }

    // 加载历史消息完成后恢复滚动位置
    if (wasLoadingHistory && !isLoadingMessages && historyLoadSnapshotRef.current !== null && messagesContainerRef.current) {
      const container = messagesContainerRef.current;
      const snapshot = historyLoadSnapshotRef.current;
      const newScrollHeight = container.scrollHeight;
      const addedHeight = newScrollHeight - snapshot.scrollHeight;
      container.scrollTop = snapshot.scrollTop + addedHeight;
      historyLoadSnapshotRef.current = null;
      isLoadingHistoryRef.current = false;
      return;
    }

    // 正在加载时不处理
    if (isLoadingMessages) {
      return;
    }

    // 切换对话后首次加载完成，滚动到底部
    if (needsScrollToBottom && allMessages.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
      needsScrollToBottomRef.current = false;
    }
  }, [currentSessionId, isLoadingMessages, allMessages.length, messagesContainerRef, messagesEndRef]);

  const handleScroll = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container || isLoadingMessages || isStreaming || !hasMoreHistory || !currentSessionId) return;

    if (container.scrollTop < 100) {
      if (loadMoreLockRef.current) return;
      const now = Date.now();
      if (now - lastLoadMoreRef.current < 400) return;
      lastLoadMoreRef.current = now;
      loadMoreLockRef.current = true;
      historyLoadSnapshotRef.current = {
        scrollTop: container.scrollTop,
        scrollHeight: container.scrollHeight,
      };
      isLoadingHistoryRef.current = true;
      fetchMessages(currentSessionId, messages.length);
      window.setTimeout(() => { loadMoreLockRef.current = false; }, 600);
    }
  }, [isLoadingMessages, isStreaming, hasMoreHistory, currentSessionId, fetchMessages, messages.length]);

  if (isLoadingMessages && messages.length === 0) {
    return (
      <div className="chat-messages">
        <div className="history-loading">
          <span className="loading-dots"><span></span><span></span><span></span></span>
          {t('chat.historyLoading')}
        </div>
      </div>
    );
  }

  return (
    <div className="chat-messages" ref={messagesContainerRef} onScroll={handleScroll}>
      {hasMoreHistory && isLoadingMessages && (
        <div className="load-more-trigger">
          <div className="loading-indicator">
            <span className="loading-dots"><span></span><span></span><span></span></span>
            {t('chat.loadingMessages')}
          </div>
        </div>
      )}
      {allMessages.map((msg, idx) => (
        <ChatMessage 
          key={msg.id || `${msg.role}-${idx}-${msg.content?.slice(0, 20)}`} 
          message={msg} 
          isStreaming={streamingMessage !== null && msg === streamingMessage}
          onRewind={onRewind}
        />
      ))}
      <div ref={messagesEndRef} />
    </div>
  );
});

export const ChatView: React.FC = () => {
  const { t } = useTranslation();
  const [inputValue, setInputValue] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [rewindTargetId, setRewindTargetId] = useState<string | null>(null);
  const [rewindNotice, setRewindNotice] = useState('');
  const [rewindFiles, setRewindFiles] = useState<{ path: string; added: number; removed: number; kind?: string; binary?: boolean }[]>([]);
  const [rewindSkipped, setRewindSkipped] = useState<string[]>([]);
  const [modelInfo, setModelInfo] = useState<{
    model: string; override: boolean; global_model: string;
    models: { name: string; model: string; base_url: string }[];
  }>({ model: '', override: false, global_model: '', models: [] });
  const rewindNoticeTimer = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { sendMessage, stopStreaming, messages, streamingMessage, isStreaming, rewindToMessage, previewRewind } = useChat();
  const {
    statusOnline, currentSessionId, isLoadingMessages, hasMoreHistory, fetchMessages,
    toggleMobileSidebar, agentMode, setAgentMode, sessions,
    delegationActivity, removeDelegationActivity, upsertDelegationActivity,
  } = useAppStore();
  const modeLabel = agentMode === 'plan'
    ? t('chat.modePlan')
    : agentMode === 'delegate' ? t('chat.modeDelegate') : t('chat.modeRun');
  const modeTitle = agentMode === 'plan'
    ? t('chat.modePlanTitle')
    : agentMode === 'delegate' ? t('chat.modeDelegateTitle') : t('chat.modeRunTitle');
  const modeClass = agentMode === 'plan' ? ' plan' : agentMode === 'delegate' ? ' delegate' : '';
  const headerTitle = sessions.find(s => s.session_id === currentSessionId)?.title?.trim() || t('chat.title');
  const TERMINAL_STATUS = ['done', 'error', 'timeout', 'cancelled', 'unknown'];

  // 完成/失败后短暂展示再自动消失
  useEffect(() => {
    const timers = delegationActivity
      .filter(a => TERMINAL_STATUS.includes(a.status))
      .map(a => window.setTimeout(
        () => removeDelegationActivity(a.task_id),
        a.status === 'done' ? 2500 : 4000,
      ));
    return () => timers.forEach(id => window.clearTimeout(id));
  }, [delegationActivity, removeDelegationActivity]);

  const delegationLabel = (a: typeof delegationActivity[number]) => {
    const name = a.target_title || a.target || '?';
    if (a.status === 'queued') return t('chat.delegationQueued', { session: name });
    if (TERMINAL_STATUS.includes(a.status)) {
      if (a.status === 'done' && a.success !== false) return t('chat.delegationDone', { session: name });
      return t('chat.delegationFailed', { session: name });
    }
    return t('chat.delegationRunning', { session: name });
  };

  // 多个委托：最多展示 3 条，超出折叠成 +N；推理只展示最新一条，节省纵向空间
  const visibleDelegations = delegationActivity.slice(-3);
  const hiddenDelegations = Math.max(0, delegationActivity.length - visibleDelegations.length);

  // 轮询兜底：父会话未在流式时也能拿到委托进度（WS 事件可能收不到）
  const missCountRef = useRef<Record<string, number>>({});
  useEffect(() => {
    if (!currentSessionId) return;
    let alive = true;
    const poll = async () => {
      try {
        const data = await fetchJSON<{ delegations: any[] }>(
          API.chatDelegations(currentSessionId)
        );
        if (!alive) return;
        const items = data.delegations || [];
        const liveIds = new Set(items.map((i: any) => String(i.task_id)));
        for (const item of items) {
          missCountRef.current[String(item.task_id)] = 0;
          upsertDelegationActivity({ ...item, status: item.status || 'running' });
        }
        // 连续两次轮询都查不到 → 视为已结束（done 事件可能丢失）
        for (const a of useAppStore.getState().delegationActivity) {
          if (TERMINAL_STATUS.includes(a.status) || liveIds.has(a.task_id)) continue;
          const n = (missCountRef.current[a.task_id] || 0) + 1;
          missCountRef.current[a.task_id] = n;
          if (n >= 2) {
            upsertDelegationActivity({
              task_id: a.task_id, target: a.target, target_title: a.target_title,
              status: 'done', success: true, elapsed_s: a.elapsed_s,
            });
          }
        }
      } catch {
        /* 忽略轮询失败 */
      }
    };
    poll();
    const timer = window.setInterval(poll, 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [currentSessionId, upsertDelegationActivity]);

  // 模式实时同步：初始拉取 + 订阅后端广播（TUI/WebUI 任一端口切换都会推送）
  useEffect(() => {
    if (!currentSessionId) return;
    fetchJSON<{ mode: string }>(API.sessionMode(currentSessionId))
      .then(r => setAgentMode(toAgentMode(r.mode)))
      .catch(() => {});
    fetchJSON<typeof modelInfo>(API.sessionModel(currentSessionId))
      .then(setModelInfo)
      .catch(() => {});

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/api/session-events/ws?session_id=${encodeURIComponent(currentSessionId)}`
    );
    const heartbeat = window.setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, 25000);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'subscribe', session_id: currentSessionId }));
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'chat_event' && msg.data?.type === 'mode_changed') {
          setAgentMode(toAgentMode(msg.data.mode));
        }
        if (msg.type === 'chat_event' && msg.data?.type === 'session_model_changed') {
          setModelInfo(prev => ({
            ...prev,
            model: msg.data.model || prev.model,
            override: Boolean(msg.data.override),
          }));
        }
      } catch {
        // 非 JSON 消息忽略
      }
    };
    return () => {
      window.clearInterval(heartbeat);
      ws.close();
    };
  }, [currentSessionId, setAgentMode]);

  useEffect(() => () => {
    if (rewindNoticeTimer.current) window.clearTimeout(rewindNoticeTimer.current);
  }, []);

  const handleRequestRewind = async (messageId: string) => {
    try {
      const preview = await previewRewind(messageId);
      setRewindFiles(preview.files || []);
      setRewindSkipped(preview.files_skipped || []);
      setRewindTargetId(messageId);
    } catch (error: any) {
      alert(error.message);
    }
  };

  const handleConfirmRewind = async () => {
    const targetId = rewindTargetId;
    setRewindTargetId(null);
    setRewindFiles([]);
    setRewindSkipped([]);
    if (!targetId) return;
    const result = await rewindToMessage(targetId);
    if (!result) return;
    const skipped = result.files_skipped?.length ?? 0;
    setRewindNotice(skipped > 0 ? `${skipped} 个文件未还原` : '');
    if (rewindNoticeTimer.current) window.clearTimeout(rewindNoticeTimer.current);
    rewindNoticeTimer.current = window.setTimeout(() => setRewindNotice(''), 5000);
  };

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    
    setUploading(true);
    const uploadedAttachments: Attachment[] = [];
    
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const formData = new FormData();
      formData.append('file', file);
      
      try {
        const response = await fetch('/api/upload', {
          method: 'POST',
          body: formData
        });
        
        if (!response.ok) throw new Error('上传失败');
        
        const result = await response.json();
        uploadedAttachments.push(result);
      } catch (error) {
        console.error('上传失败:', error);
      }
    }
    
    setAttachments(prev => [...prev, ...uploadedAttachments]);
    setUploading(false);
    e.target.value = '';
  };

  const handleRemoveAttachment = (index: number) => {
    setAttachments(prev => prev.filter((_, i) => i !== index));
  };

  const handleSend = () => {
    if (!inputValue.trim() && attachments.length === 0) return;
    sendMessage(inputValue.trim(), attachments);
    setInputValue('');
    setAttachments([]);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  };

  const toggleMode = () => {
    const prev = agentMode;
    const next = MODE_ORDER[(MODE_ORDER.indexOf(agentMode) + 1) % MODE_ORDER.length];
    setAgentMode(next);
    const sid = currentSessionId || 'default';
    postJSON(API.sessionMode(sid), { mode: next }).catch(() => setAgentMode(prev));
  };

  const handleModelChange = (value: string) => {
    const sid = currentSessionId || 'default';
    setModelInfo(prev => ({
      ...prev,
      model: value || prev.global_model,
      override: Boolean(value),
    }));
    postJSON(API.sessionModel(sid), { model: value }).catch(() => {
      fetchJSON<typeof modelInfo>(API.sessionModel(sid)).then(setModelInfo).catch(() => {});
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Tab' && e.shiftKey) { e.preventDefault(); toggleMode(); return; }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const handleInput = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px';
    }
  };

  return (
    <div className="chat-view">
      {/* Header */}
      <div className="chat-header">
        <h1 className="chat-header-title" title={headerTitle}>{headerTitle}</h1>
        {modelInfo.models.length > 0 && (
          <CustomDropdown
            className="header-model-dropdown"
            value={modelInfo.override ? modelInfo.model : ''}
            items={[
              {
                value: '',
                label: t('chat.modelFollowGlobal', { model: modelInfo.global_model || '-' }),
              },
              ...modelInfo.models.map(m => ({ value: m.name, label: m.name })),
            ]}
            onChange={handleModelChange}
            disabled={isStreaming}
            menuMinWidth={180}
          />
        )}
        <span className={`status ${statusOnline ? '' : 'error'}`}>{statusOnline ? t('common.online') : t('common.offline')}</span>
        <div className="chat-header-actions">
          <button
            className={`btn-mode mobile-only${modeClass}`}
            onClick={toggleMode}
            title={modeTitle}
          >
            {modeLabel}
          </button>
          <button
            className="icon-btn mobile-only"
            onClick={toggleMobileSidebar}
            title={t('sidebar.sessions')}
          >
            <Icons.Menu size={18} />
          </button>
        </div>
      </div>

      {/* 会话委托运行状态条 */}
      {delegationActivity.length > 0 && (
        <div className="delegation-banners">
          {visibleDelegations.map((a, idx) => {
            const running = !TERMINAL_STATUS.includes(a.status);
            const tool = a.current_tool || a.last_tool;
            const showReason = running && idx === visibleDelegations.length - 1;
            return (
              <div className={`delegation-banner ${running ? 'running' : a.status}`} key={a.task_id}>
                <div className="delegation-banner-line">
                  {running && <span className="delegation-banner-spinner" />}
                  <span className="delegation-banner-title">{delegationLabel(a)}</span>
                  {tool && <span className="delegation-banner-meta">{t('chat.delegationTool', { tool })}</span>}
                  {a.tool_calls ? (
                    <span className="delegation-banner-meta">{t('chat.delegationAttempt', { n: a.tool_calls })}</span>
                  ) : null}
                  {a.elapsed_s ? (
                    <span className="delegation-banner-meta">{Math.round(Number(a.elapsed_s))}s</span>
                  ) : null}
                  {a.error && <span className="delegation-banner-error">{a.error}</span>}
                </div>
                {showReason && a.last_reasoning && (
                  <div className="delegation-banner-reason">
                    {t('chat.delegationReasoning')}：{a.last_reasoning}
                  </div>
                )}
              </div>
            );
          })}
          {hiddenDelegations > 0 && (
            <div className="delegation-more">{t('chat.delegationMore', { n: hiddenDelegations })}</div>
          )}
        </div>
      )}
      <MessageList
        messages={messages}
        streamingMessage={streamingMessage}
        messagesEndRef={messagesEndRef}
        messagesContainerRef={messagesContainerRef}
        isLoadingMessages={isLoadingMessages}
        hasMoreHistory={hasMoreHistory}
        currentSessionId={currentSessionId}
        fetchMessages={fetchMessages}
        isStreaming={isStreaming}
        onRewind={handleRequestRewind}
      />

      <div className="chat-input-container">
        {attachments.length > 0 && (
          <div className="attachments-preview">
            {attachments.map((att, idx) => (
              <div key={idx} className="attachment-item">
                <div className="attachment-icon">
                  {att.file_type === 'image' ? (
                    <Icons.Image size={14} />
                  ) : (
                    <Icons.File size={14} />
                  )}
                </div>
                <span className="attachment-filename">{att.filename}</span>
                <button 
                  className="attachment-remove"
                  onClick={() => handleRemoveAttachment(idx)}
                  title={t('chat.removeAttachment')}
                >
                  <Icons.X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
        
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />
        
        <div className="chat-input-wrapper">
          <textarea
            ref={textareaRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={isStreaming ? t('chat.placeholderStreaming') : t('chat.placeholder')}
            rows={1}
          />
          <div className="input-actions">
            <button
              className={`btn-mode mode-desktop${modeClass}`}
              onClick={toggleMode}
              title={modeTitle}
            >
              {modeLabel}
            </button>
            <button
              className="btn-upload"
              onClick={handleFileSelect}
              disabled={uploading || isStreaming}
              title={t('chat.uploadFile')}
            >
              {uploading ? <Icons.Loader size={18} /> : <Icons.Plus size={18} />}
            </button>
            {isStreaming && (
              <button className="btn-stop" onClick={stopStreaming} title={t('chat.stopTitle')}>
                <Icons.Square size={18} />
              </button>
            )}
            <button
              className="btn-send"
              onClick={handleSend}
              disabled={(!inputValue.trim() && attachments.length === 0) || isStreaming}
              title={isStreaming ? t('chat.sendSupplementTitle') : t('chat.sendTitle')}
            >
              <Icons.Send size={18} />
            </button>
          </div>
        </div>
        <div className="input-footer">
          {t('chat.inputTip')}
        </div>
      </div>

      {rewindNotice && (
        <div className="rewind-files">
          <div className="rewind-file-warn">{rewindNotice}</div>
        </div>
      )}

      <ConfirmDialog
        open={Boolean(rewindTargetId)}
        title="撤回消息"
        icon={<Icons.Rewind size={15} />}
        confirmLabel="撤回"
        cancelLabel="取消"
        danger
        onConfirm={handleConfirmRewind}
        onCancel={() => setRewindTargetId(null)}
        message={
          <>
            <div>将撤回这条消息及其之后的所有对话</div>
            {rewindFiles.length > 0 ? (
              <div className="confirm-files">
                {rewindFiles.slice(0, 8).map((file, idx) => (
                  <div className="rewind-file-row" key={idx} title={file.path}>
                    <span className="rewind-file-path">
                      {file.path.split(/[\\/]/).slice(-2).join('/')}
                    </span>
                    {file.kind === 'restored' ? (
                      <span className="rewind-file-tag">还原</span>
                    ) : file.kind === 'deleted' ? (
                      <span className="rewind-file-tag tag-del">删除</span>
                    ) : file.binary ? (
                      <span className="rewind-file-tag">二进制</span>
                    ) : (
                      <>
                        <span className="rewind-file-add">+{file.added}</span>
                        <span className="rewind-file-del">−{file.removed}</span>
                      </>
                    )}
                  </div>
                ))}
                {rewindFiles.length > 8 && (
                  <div className="rewind-file-more">还有 {rewindFiles.length - 8} 个文件</div>
                )}
              </div>
            ) : (
              <div className="confirm-hint">无文件改动</div>
            )}
            {rewindSkipped.length > 0 && (
              <div className="rewind-file-warn">{rewindSkipped.length} 个文件无法回滚</div>
            )}
          </>
        }
      />
    </div>
  );
};
