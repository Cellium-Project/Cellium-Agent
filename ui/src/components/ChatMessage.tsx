import React, { memo, useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import type { Message, TimelineSegment } from '../types';
import { Icons } from './Icons';
import { Collapsible } from './Collapsible';

marked.setOptions({ gfm: true });

function safeRenderMarkdown(content: string): string {
  if (!content) return '';
  const rawHtml = marked.parse(content) as string;
  return DOMPurify.sanitize(rawHtml);
}

interface ChatMessageProps {
  message: Message;
}

export const ChatMessage = memo<ChatMessageProps>(({ message }) => {
  const isUser = message.role === 'user';

  return (
    <div className={`message-row ${message.role}`}>
      <div className="message-wrapper">
        <div className="message-avatar">
          {isUser ? <Icons.User size={20} /> : <Icons.Bot size={20} />}
        </div>
        <div className={`message-content ${isUser ? 'user-content' : 'markdown-body'}`}>
          {isUser ? (
            <div className="user-text">{message.content}</div>
          ) : (
            renderTimeline(message)
          )}
        </div>
      </div>
    </div>
  );
});

function renderTimeline(message: Message): React.ReactNode {
  if (message.timeline && message.timeline.length > 0) {
    return (
      <>
        {message.timeline.map((segment, idx) => (
          <TimelineItem key={idx} segment={segment} />
        ))}
      </>
    );
  }

  return (
    <>
      {message.toolTraces && message.toolTraces.length > 0 && (
        <div className="tool-traces-wrap">
          {message.toolTraces.map((trace, idx) => (
            <ToolTraceCard key={idx} trace={trace} />
          ))}
        </div>
      )}
      <div
        className="assistant-text"
        dangerouslySetInnerHTML={{
          __html: safeRenderMarkdown(message.content),
        }}
      />
    </>
  );
}

function TimelineItem({ segment }: { segment: TimelineSegment }): React.ReactNode {
  if (segment.kind === 'text') {
    return (
      <div
        className="assistant-text"
        dangerouslySetInnerHTML={{
          __html: safeRenderMarkdown(segment.content),
        }}
      />
    );
  }

  if (segment.kind === 'thinking') {
    return (
      <div
        className="assistant-text thinking-text"
        dangerouslySetInnerHTML={{
          __html: safeRenderMarkdown(segment.content),
        }}
      />
    );
  }

  return (
    <ToolTraceCard
      trace={{
        tool: segment.tool,
        arguments: segment.arguments,
        result: segment.result,
        duration_ms: segment.duration_ms,
        description: segment.description,
      }}
      status={segment.status}
    />
  );
}

interface ToolTraceCardProps {
  trace: {
    tool: string;
    arguments: Record<string, any>;
    result?: any;
    duration_ms: number;
    description?: string;
  };
  status?: 'running' | 'done' | 'error';
}

const ToolTraceCard: React.FC<ToolTraceCardProps> = ({ trace, status }) => {
  const { t } = useTranslation();
  const argsStr = JSON.stringify(trace.arguments || {}, null, 2);
  const resultPreview = makeResultPreview(trace.result, t);
  
  const cmdPreview = (() => {
    try {
      if (trace.arguments?.command) {
        const cmd = String(trace.arguments.command);
        return cmd.length > 80 ? cmd.slice(0, 80) + '...' : cmd;
      }
      if (trace.arguments?.url) {
        const url = String(trace.arguments.url);
        return url.length > 80 ? url.slice(0, 80) + '...' : url;
      }
      return '';
    } catch {
      return '';
    }
  })();
  
  const [elapsedMs, setElapsedMs] = useState(0);
  const startTimeRef = useRef(Date.now());
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  
  useEffect(() => {
    if (status === 'running') {
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startTimeRef.current);
      }, 100);
      return () => {
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      };
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
  }, [status]);
  
  const displayMs = status === 'running' ? elapsedMs : (trace.duration_ms || 0);
  const durStr = displayMs >= 1000 ? `${(displayMs / 1000).toFixed(1)}s` : `${displayMs}ms`;

  return (
    <>
      {trace.description && (
        <div className="tool-description">{escapeHtml(String(trace.description))}</div>
      )}
      <div className={`tool-trace ${status === 'running' ? 'tool-running' : ''}`}>
        <div className="tool-trace-header">
          <span className="tool-trace-name">{escapeHtml(String(trace.tool || 'unknown'))}</span>
          {status === 'running' && (
            <span className="tool-status-running">
              <span className="loading-pulse"></span>
              {t('chat.executing')} {durStr}
            </span>
          )}
          {status !== 'running' && <span className="tool-trace-time">{durStr}</span>}
        </div>
        {cmdPreview && (
          <div className="tool-cmd-preview">
            <code>{escapeHtml(cmdPreview)}</code>
          </div>
        )}
        <Collapsible 
          summary={t('chat.paramsAndResult')} 
          defaultOpen={status === 'running' || (status === 'done' && trace.result?.error)}
        >
          <pre className="tool-args">{escapeHtml(argsStr)}</pre>
          {status !== 'running' && <div className="tool-result">{resultPreview}</div>}
          {status === 'running' && <div className="tool-result"><span className="status-dot dot-running"></span>{t('chat.waitingForResult')}</div>}
        </Collapsible>
      </div>
    </>
  );
};

function makeResultPreview(result: any, t?: (key: string) => string): React.ReactNode {
  const translate = t || ((key: string) => key);
  if (!result) return <span>({translate('common.empty')})</span>;

  if (result.error) {
    return (
      <>
        <span className="status-dot dot-error"></span>
        <span style={{ color: 'var(--text-error)' }}>{translate('common.error')}: {escapeHtml(result.error)}</span>
      </>
    );
  }

  if (result.success !== undefined) {
    return result.success ? (
      <>
        <span className="status-dot dot-success"></span>
        <span style={{ color: 'var(--accent-success)' }}>{translate('common.completed')}</span>
      </>
    ) : (
      <span style={{ color: 'var(--text-secondary)' }}>{translate('common.completed')}</span>
    );
  }

  const text = typeof result === 'object'
    ? result.output || JSON.stringify(result).slice(0, 150)
    : String(result).slice(0, 150);

  return (
    <>
      <span className="status-dot dot-success"></span>
      {escapeHtml(text)}
    </>
  );
}

const HTML_ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function escapeHtml(str: string): string {
  if (!str) return '';
  return str.replace(/[&<>"']/g, (char) => HTML_ESCAPE_MAP[char] || char);
}
