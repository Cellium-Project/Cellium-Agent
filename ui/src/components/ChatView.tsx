import React, { useState, useRef, useEffect, memo } from 'react';
import { useTranslation } from 'react-i18next';
import { useChat } from '../hooks/useChat';
import { useAppStore } from '../stores/appStore';
import { Icons } from './Icons';
import { ChatMessage } from './ChatMessage';

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
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const { sendMessage, stopStreaming, messages, streamingMessage, isStreaming } = useChat();
  const { statusOnline, currentSessionId, isLoadingMessages, hasMoreHistory, fetchMessages } = useAppStore();

  const handleSend = () => {
    if (!inputValue.trim()) return;
    sendMessage(inputValue.trim());
    setInputValue('');
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
      />

      {/* Input — 不再随 streamingMessage 变化而重渲染 */}
      <div className="chat-input-container">
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
            {isStreaming && (
              <button className="btn-stop" onClick={stopStreaming} title={t('chat.stopTitle')}>
                <Icons.Square size={18} />
              </button>
            )}
            <button
              className="btn-send"
              onClick={handleSend}
              disabled={!inputValue.trim()}
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
