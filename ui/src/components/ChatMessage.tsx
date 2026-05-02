import React, { memo, useState, useEffect, useRef, useMemo } from 'react';
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

/** Check if a parsed JSON object looks like a "thought" block (reasoning/plan/action) */
function isThoughtJson(obj: any): boolean {
  return typeof obj === 'object' && obj !== null &&
    ('reasoning' in obj || 'plan' in obj || 'action' in obj);
}

/**
 * Split content into segments: plain text and JSON thought blocks.
 * Detects both ```json ... ``` fenced blocks AND raw JSON objects
 * that look like thought blocks ({reasoning, plan, action, ...}).
 */
function splitJsonBlocks(content: string): Array<{ type: 'text' | 'json'; content: string }> {
  const segments: Array<{ type: 'text' | 'json'; content: string }> = [];

  // Phase 1: split by ```json ... ``` fences
  const fencedSegments: Array<{ type: 'text' | 'json'; content: string }> = [];
  const jsonBlockRegex = /```json\s*([\s\S]*?)\s*```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = jsonBlockRegex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      fencedSegments.push({ type: 'text', content: content.slice(lastIndex, match.index) });
    }
    fencedSegments.push({ type: 'json', content: match[1].trim() });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < content.length) {
    fencedSegments.push({ type: 'text', content: content.slice(lastIndex) });
  }
  if (fencedSegments.length === 0) {
    fencedSegments.push({ type: 'text', content });
  }

  // Phase 2: for each text segment, detect raw JSON thought blocks
  // Pattern: a JSON object starting with { that contains reasoning/plan/action keys
  for (const seg of fencedSegments) {
    if (seg.type === 'json') {
      segments.push(seg);
      continue;
    }

    const text = seg.content;
    // Try to find a raw JSON thought object in the text
    const rawJsonResult = extractRawThoughtJson(text);
    if (rawJsonResult) {
      // Text before the JSON
      if (rawJsonResult.before.trim()) {
        segments.push({ type: 'text', content: rawJsonResult.before });
      }
      segments.push({ type: 'json', content: rawJsonResult.json });
      // Text after the JSON
      if (rawJsonResult.after.trim()) {
        segments.push({ type: 'text', content: rawJsonResult.after });
      }
    } else {
      segments.push(seg);
    }
  }

  return segments;
}

/**
 * Try to extract a raw JSON thought block from text content.
 * Returns { before, json, after } or null if not found.
 */
function extractRawThoughtJson(text: string): { before: string; json: string; after: string } | null {
  // Quick check: if content doesn't contain a {, skip
  const firstBrace = text.indexOf('{');
  if (firstBrace === -1) return null;

  // Try to find a balanced JSON object starting at each {
  for (let startIdx = firstBrace; startIdx < text.length; startIdx++) {
    if (text[startIdx] !== '{') continue;

    let depth = 0;
    let inString = false;
    let escape = false;

    for (let i = startIdx; i < text.length; i++) {
      const ch = text[i];

      if (escape) {
        escape = false;
        continue;
      }
      if (ch === '\\' && inString) {
        escape = true;
        continue;
      }
      if (ch === '"') {
        inString = !inString;
        continue;
      }
      if (inString) continue;

      if (ch === '{') depth++;
      else if (ch === '}') depth--;

      if (depth === 0) {
        // Found a balanced JSON object
        const jsonCandidate = text.slice(startIdx, i + 1);
        try {
          const parsed = JSON.parse(jsonCandidate);
          if (isThoughtJson(parsed)) {
            return {
              before: text.slice(0, startIdx),
              json: jsonCandidate,
              after: text.slice(i + 1),
            };
          }
        } catch {
          // Not valid JSON, continue searching
        }
        break; // This { ... } was balanced but not a thought JSON, stop here
      }
    }
  }

  return null;
}

/** Render content that may contain JSON blocks — JSON blocks are collapsible */
function renderContentWithCollapsibleJson(content: string): React.ReactNode {
  const segments = splitJsonBlocks(content);
  if (segments.length === 0) {
    return (
      <div
        className="assistant-text"
        dangerouslySetInnerHTML={{ __html: safeRenderMarkdown(content) }}
      />
    );
  }

  // If no JSON blocks found, just render as markdown
  if (segments.every(s => s.type === 'text')) {
    return (
      <div
        className="assistant-text"
        dangerouslySetInnerHTML={{ __html: safeRenderMarkdown(content) }}
      />
    );
  }

  return (
    <>
      {segments.map((seg, idx) => {
        if (seg.type === 'text') {
          if (!seg.content.trim()) return null;
          return (
            <div
              key={idx}
              className="assistant-text"
              dangerouslySetInnerHTML={{ __html: safeRenderMarkdown(seg.content) }}
            />
          );
        }

        // JSON block — render as collapsible
        return <JsonBlockCard key={idx} jsonStr={seg.content} />;
      })}
    </>
  );
}

/** Collapsible card for a JSON reasoning/plan block */
const JsonBlockCard: React.FC<{ jsonStr: string }> = ({ jsonStr }) => {
  const { t } = useTranslation();

  const { label, summaryText, prettyJson } = useMemo(() => {
    let parsed: any = null;
    try {
      parsed = JSON.parse(jsonStr);
    } catch {
      // Not valid JSON — just show as-is
      return {
        label: 'JSON',
        summaryText: jsonStr.slice(0, 60) + (jsonStr.length > 60 ? '...' : ''),
        prettyJson: jsonStr,
      };
    }

    const label = parsed.action
      ? String(parsed.action)
      : parsed.reasoning
        ? '💭 reasoning'
        : 'JSON';

    const summaryParts: string[] = [];
    if (parsed.reasoning) {
      summaryParts.push(String(parsed.reasoning).slice(0, 80));
    }
    if (parsed.confidence !== undefined) {
      summaryParts.push(`confidence: ${parsed.confidence}`);
    }
    if (parsed.estimated_steps !== undefined) {
      summaryParts.push(`steps: ${parsed.estimated_steps}`);
    }

    return {
      label,
      summaryText: summaryParts.join(' · ') || jsonStr.slice(0, 60),
      prettyJson: JSON.stringify(parsed, null, 2),
    };
  }, [jsonStr]);

  return (
    <div className="json-block-card">
      <Collapsible
        summary={
          <span className="json-block-summary">
            <span className="json-block-label">{label}</span>
            <span className="json-block-preview">{summaryText}</span>
          </span>
        }
        defaultOpen={false}
      >
        <pre className="json-block-content">{prettyJson}</pre>
      </Collapsible>
    </div>
  );
};

interface ChatMessageProps {
  message: Message;
}

export const ChatMessage = memo<ChatMessageProps>(({ message }) => {
  const { t } = useTranslation();
  const isUser = message.role === 'user';
  
  if (message.type === 'scheduler_trigger') {
    return (
      <div className="message-row scheduler-trigger">
        <div className="scheduler-trigger-bubble">
          <Icons.Clock size={16} />
          <span>{t('common.schedulerTrigger')}：{message.schedulerTaskName}</span>
        </div>
      </div>
    );
  }

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
    // Merge consecutive text segments before rendering.
    // Streaming splits content into many tiny chunks; we must
    // reassemble them so JSON detection sees the full block.
    // Note: thinking segments are NOT merged, they are rendered separately.
    type ToolSegment = Extract<TimelineSegment, { kind: 'tool' }>;
    type ThinkingSegment = Extract<TimelineSegment, { kind: 'thinking' }>;
    type GroupItem =
      | { kind: 'merged-text'; contents: string[] }
      | ToolSegment
      | ThinkingSegment;

    const groups: GroupItem[] = [];

    for (const segment of message.timeline) {
      if (segment.kind === 'text') {
        const last = groups[groups.length - 1];
        if (last && last.kind === 'merged-text') {
          last.contents.push(segment.content);
        } else {
          groups.push({ kind: 'merged-text', contents: [segment.content] });
        }
      } else if (segment.kind === 'thinking') {
        // thinking segments are kept separate and rendered with ThinkingCard
        groups.push(segment as ThinkingSegment);
      } else if (segment.kind === 'tool') {
        groups.push(segment as ToolSegment);
      }
    }

    return (
      <>
        {groups.map((group, idx) => {
          if (group.kind === 'merged-text') {
            const merged = group.contents.join('');
            return <React.Fragment key={idx}>{renderContentWithCollapsibleJson(merged)}</React.Fragment>;
          }
          if (group.kind === 'thinking') {
            return <ThinkingCard key={idx} content={group.content} />;
          }
          // tool segment
          const seg = group as ToolSegment;
          return (
            <ToolTraceCard
              key={idx}
              trace={{
                tool: seg.tool,
                arguments: seg.arguments,
                result: seg.result,
                duration_ms: seg.duration_ms,
                description: seg.description,
              }}
              status={seg.status}
            />
          );
        })}
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
      {renderContentWithCollapsibleJson(message.content)}
    </>
  );
}

/** Thinking card with simple styling */
const ThinkingCard: React.FC<{ content: string }> = ({ content }) => {
  const prettyContent = useMemo(() => {
    let parsed: any = null;
    try {
      parsed = JSON.parse(content);
    } catch {
      // Not valid JSON
    }
    
    if (parsed && typeof parsed === 'object') {
      return JSON.stringify(parsed, null, 2);
    }
    return content;
  }, [content]);

  return (
    <div className="thinking-card">
      <Collapsible
        summary={
          <span className="collapsible-summary">
            Thinking
          </span>
        }
        defaultOpen={false}
      >
        <pre className="thinking-content">{prettyContent}</pre>
      </Collapsible>
    </div>
  );
};

function TimelineItem({ segment }: { segment: TimelineSegment }): React.ReactNode {
  if (segment.kind === 'text') {
    return renderContentWithCollapsibleJson(segment.content);
  }

  if (segment.kind === 'thinking') {
    // Use dedicated thinking card with proper styling
    return <ThinkingCard content={segment.content} />;
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
        <div className="tool-description">{String(trace.description)}</div>
      )}
      <div className={`tool-trace ${status === 'running' ? 'tool-running' : ''}`}>
        <div className="tool-trace-header">
          <span className="tool-trace-name">{String(trace.tool || 'unknown')}</span>
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
            <code>{cmdPreview}</code>
          </div>
        )}
        <Collapsible 
          summary={t('chat.paramsAndResult')} 
          defaultOpen={status === 'running' || (status === 'done' && trace.result?.error)}
        >
          <pre className="tool-args">{argsStr}</pre>
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
        <span style={{ color: 'var(--text-error)' }}>{translate('common.error')}: {result.error}</span>
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
      {text}
    </>
  );
}
