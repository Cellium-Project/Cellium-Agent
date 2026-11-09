import React, { useState, useRef, useEffect, memo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from './ConfirmDialog';
import { useChat } from '../hooks/useChat';
import { useAppStore } from '../stores/appStore';
import { API, fetchJSON, postJSON } from '../utils/api';
import { Icons } from './Icons';
import { ChatMessage } from './ChatMessage';
import type { Attachment, Message } from '../types';

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
  const [rewindFiles, setRewindFiles] = useState<{ path: string; added: number; removed: number; kind?: string }[]>([]);
  const [rewindSkipped, setRewindSkipped] = useState<string[]>([]);
  const rewindNoticeTimer = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { sendMessage, stopStreaming, messages, streamingMessage, isStreaming, rewindToMessage, previewRewind } = useChat();
  const {
    statusOnline, currentSessionId, isLoadingMessages, hasMoreHistory, fetchMessages,
    toggleMobileSidebar, agentMode, setAgentMode,
  } = useAppStore();
  const planMode = agentMode === 'plan';

  // 模式实时同步：初始拉取 + 订阅后端广播（TUI/WebUI 任一端口切换都会推送）
  useEffect(() => {
    if (!currentSessionId) return;
    fetchJSON<{ mode: string }>(API.sessionMode(currentSessionId))
      .then(r => setAgentMode(r.mode === 'plan' ? 'plan' : 'run'))
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
          setAgentMode(msg.data.mode === 'plan' ? 'plan' : 'run');
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
    const next = planMode ? 'run' : 'plan';
    setAgentMode(next);
    const sid = currentSessionId || 'default';
    postJSON(API.sessionMode(sid), { mode: next }).catch(() => {});
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
        <h1 className="chat-header-title">{t('chat.title')}</h1>
        <span className={`status ${statusOnline ? '' : 'error'}`}>{statusOnline ? t('common.online') : t('common.offline')}</span>
        <div className="chat-header-actions">
          <button
            className={`btn-mode mobile-only${planMode ? ' plan' : ''}`}
            onClick={toggleMode}
            title={planMode ? t('chat.modePlanTitle') : t('chat.modeRunTitle')}
          >
            {planMode ? t('chat.modePlan') : t('chat.modeRun')}
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

      {/* Messages — 独立组件，流式更新不影响输入框 */}
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
              className={`btn-mode mode-desktop${planMode ? ' plan' : ''}`}
              onClick={toggleMode}
              title={planMode ? t('chat.modePlanTitle') : t('chat.modeRunTitle')}
            >
              {planMode ? t('chat.modePlan') : t('chat.modeRun')}
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
