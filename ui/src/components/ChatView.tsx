import React, { useState, useRef, useEffect, memo } from 'react';
import { useTranslation } from 'react-i18next';
import { useChat } from '../hooks/useChat';
import { useAppStore } from '../stores/appStore';
import { Icons } from './Icons';
import { ChatMessage } from './ChatMessage';
import type { Attachment } from '../types';

const MessageList = memo(({
  messages, streamingMessage,
  messagesEndRef, messagesContainerRef,
  isLoadingMessages, hasMoreHistory, currentSessionId,
  fetchMessages,
}: {
  messages: ReturnType<typeof useChat>['messages'];
  streamingMessage: ReturnType<typeof useChat>['streamingMessage'];
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  messagesContainerRef: React.RefObject<HTMLDivElement | null>;
  isLoadingMessages: boolean;
  hasMoreHistory: boolean;
  currentSessionId: string | null;
  fetchMessages: (sessionId: string, offset?: number) => Promise<void>;
}) => {
  const { t } = useTranslation();
  const prevMessagesCountRef = useRef<number>(0);
  const wasEmptyRef = useRef<boolean>(true);

  useEffect(() => {
    const prevCount = prevMessagesCountRef.current;
    const wasEmpty = wasEmptyRef.current;
    prevMessagesCountRef.current = messages.length;
    wasEmptyRef.current = messages.length === 0;

    if ((messages.length > prevCount && messages.length - prevCount < 50) || (wasEmpty && messages.length > 0)) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
    }
  }, [messages]);

  const handleScroll = () => {
    if (!messagesContainerRef.current || isLoadingMessages || !hasMoreHistory || !currentSessionId) return;
    if (messagesContainerRef.current.scrollTop < 100) {
      fetchMessages(currentSessionId, messages.length);
    }
  };

  return (
    <div className="chat-messages" ref={messagesContainerRef} onScroll={handleScroll}>
      {isLoadingMessages && messages.length === 0 && (
        <div className="history-loading"><span className="loading-dots"><span></span><span></span><span></span></span> {t('chat.historyLoading')}</div>
      )}
      {messages.map((msg, idx) => (
        <ChatMessage 
          key={msg.id || `${msg.role}-${idx}-${msg.content?.slice(0, 20)}`} 
          message={msg} 
        />
      ))}
      {streamingMessage && <ChatMessage message={streamingMessage} />}
      <div ref={messagesEndRef} />
    </div>
  );
});

export const ChatView: React.FC = () => {
  const { t } = useTranslation();
  const [inputValue, setInputValue] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { sendMessage, stopStreaming, messages, streamingMessage, isStreaming } = useChat();
  const { statusOnline, currentSessionId, isLoadingMessages, hasMoreHistory, fetchMessages, hybridPhase, hybridMessage, hybridDescription, toggleMobileSidebar } = useAppStore();

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

  const handleKeyDown = (e: React.KeyboardEvent) => {
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
            className="icon-btn mobile-only"
            onClick={toggleMobileSidebar}
            title={t('sidebar.sessions')}
          >
            <Icons.Menu size={18} />
          </button>
        </div>
      </div>

      {/* Hybrid 状态指示器 */}
      {isStreaming && hybridPhase && (
        <div className="hybrid-status-bar">
          <span className="hybrid-phase">{hybridMessage}</span>
          {hybridDescription && <span className="hybrid-desc">{hybridDescription}</span>}
        </div>
      )}

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
    </div>
  );
};
