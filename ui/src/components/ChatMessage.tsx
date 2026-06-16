import React, { memo, useState, useEffect, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import type { Message, TimelineSegment } from '../types';
import { Icons } from './Icons';
import { Collapsible } from './Collapsible';

marked.setOptions({ gfm: true });

const markdownCache = new Map<string, string>();
const MAX_CACHE_SIZE = 100;

function safeRenderMarkdown(content: string): string {
  if (!content) return '';
  
  const cached = markdownCache.get(content);
  if (cached) return cached;
  
  const rawHtml = marked.parse(content) as string;
  const sanitized = DOMPurify.sanitize(rawHtml);
  
  if (markdownCache.size >= MAX_CACHE_SIZE) {
    const firstKey = markdownCache.keys().next().value;
    if (firstKey) markdownCache.delete(firstKey);
  }
  markdownCache.set(content, sanitized);
  
  return sanitized;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function parseAttachmentsFromMessage(content: string): { content: string; attachments: Array<{ filename: string; file_type: string; file_size: number; local_path: string }> } {
  const attachments: Array<{ filename: string; file_type: string; file_size: number; local_path: string }> = [];
  
  const attachmentBlockRegex = /\[附件信息\]([\s\S]*?)(?=\n\n|$)/g;
  let blockMatch;
  
  while ((blockMatch = attachmentBlockRegex.exec(content)) !== null) {
    const blockContent = blockMatch[1];
    
    const itemRegex = /- 文件: (.+?) \(类型: (.+?), 大小: (\d+) bytes\)\n\s+本地路径: (.+?)(?=\n|$)/g;
    let itemMatch;
    
    while ((itemMatch = itemRegex.exec(blockContent)) !== null) {
      attachments.push({
        filename: itemMatch[1],
        file_type: itemMatch[2],
        file_size: parseInt(itemMatch[3]),
        local_path: itemMatch[4],
      });
    }
  }
  
  const cleanedContent = content.replace(attachmentBlockRegex, '').trim();
  
  return { content: cleanedContent, attachments };
}

function parseSchedulerTrigger(content: string): { isSchedulerTrigger: boolean; taskName?: string; fullContent?: string } {
  // 检查是否包含定时任务触发关键字
  if (!content.includes('[定时任务触发]')) {
    return { isSchedulerTrigger: false };
  }
  
  // 尝试多种方式提取任务名称
  let taskName = '未知任务';
  
  // 方式1: 任务名称: xxx 触发时间
  const taskNameMatch1 = content.match(/任务名称:\s*(.+?)\s*触发时间/);
  if (taskNameMatch1) {
    taskName = taskNameMatch1[1].trim();
  } else {
    // 方式2: 任务名称: xxx 后面跟着其他字段
    const taskNameMatch2 = content.match(/任务名称:\s*(.+?)(?:\s+\S+?:|$)/);
    if (taskNameMatch2) {
      taskName = taskNameMatch2[1].trim();
    } else {
      // 方式3: 更宽松的匹配
      const taskNameMatch3 = content.match(/任务名称:\s*(.+)/);
      if (taskNameMatch3) {
        taskName = taskNameMatch3[1].trim();
      }
    }
  }
  
  return {
    isSchedulerTrigger: true,
    taskName: taskName,
    fullContent: content,
  };
}

/** Check if a parsed JSON object is a thought JSON (has reasoning field) */
function isThoughtJson(obj: any): boolean {
  return typeof obj === 'object' && obj !== null && typeof obj.reasoning === 'string';
}

/**
 * Split content into segments: plain text and JSON thought blocks.
 * Detects both ```json ... ``` fenced blocks AND raw JSON objects
 * that look like thought blocks ({reasoning, plan, action, ...}).
 */
const jsonBlockCache = new Map<string, Array<{ type: 'text' | 'json'; content: string }>>();
const MAX_JSON_CACHE_SIZE = 50;

function splitJsonBlocks(content: string): Array<{ type: 'text' | 'json'; content: string }> {
  const cached = jsonBlockCache.get(content);
  if (cached) return cached;
  
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
  for (const seg of fencedSegments) {
    if (seg.type === 'json') {
      segments.push(seg);
      continue;
    }

    const text = seg.content;
    const rawJsonResult = extractRawThoughtJson(text);
    if (rawJsonResult) {
      if (rawJsonResult.before.trim()) {
        segments.push({ type: 'text', content: rawJsonResult.before });
      }
      segments.push({ type: 'json', content: rawJsonResult.json });
      if (rawJsonResult.after.trim()) {
        segments.push({ type: 'text', content: rawJsonResult.after });
      }
    } else {
      segments.push(seg);
    }
  }

  if (jsonBlockCache.size >= MAX_JSON_CACHE_SIZE) {
    const firstKey = jsonBlockCache.keys().next().value;
    if (firstKey) jsonBlockCache.delete(firstKey);
  }
  jsonBlockCache.set(content, segments);
  
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
const jsonParseCache = new Map<string, { label: string; content: string }>();
const MAX_PARSE_CACHE_SIZE = 30;

const JsonBlockCard: React.FC<{ jsonStr: string }> = memo(({ jsonStr }) => {
  const { t } = useTranslation();

  const { label, content } = useMemo(() => {
    const cached = jsonParseCache.get(jsonStr);
    if (cached) return cached;
    
    let parsed: any = null;
    try {
      parsed = JSON.parse(jsonStr);
    } catch {
      const result = {
        label: 'Thinking',
        content: jsonStr,
      };
      jsonParseCache.set(jsonStr, result);
      return result;
    }

    if (isThoughtJson(parsed)) {
      const result = {
        label: 'Thinking',
        content: parsed.reasoning || jsonStr,
      };
      jsonParseCache.set(jsonStr, result);
      return result;
    }

    const result = {
      label: 'JSON',
      content: JSON.stringify(parsed, null, 2),
    };
    
    if (jsonParseCache.size >= MAX_PARSE_CACHE_SIZE) {
      const firstKey = jsonParseCache.keys().next().value;
      if (firstKey) jsonParseCache.delete(firstKey);
    }
    jsonParseCache.set(jsonStr, result);
    return result;
  }, [jsonStr]);

  return (
    <div className="json-block-card">
      <Collapsible
        summary={
          <span className="json-block-summary">
            <span className="json-block-label">{label}</span>
            <span className="json-block-preview">{String(content).slice(0, 80)}</span>
          </span>
        }
        defaultOpen={false}
      >
        <pre className="json-block-content">{content}</pre>
      </Collapsible>
    </div>
  );
});

interface ChatMessageProps {
  message: Message;
}

export const ChatMessage = memo<ChatMessageProps>(({ message }) => {
  const { t } = useTranslation();
  const isUser = message.role === 'user';
  const [showSchedulerDetail, setShowSchedulerDetail] = useState(false);
  
  // 解析用户消息中的附件信息
  const parsedMessage = useMemo(() => {
    if (isUser) {
      const parsed = parseAttachmentsFromMessage(message.content);
      // 合并已有的attachments和解析出来的attachments
      const allAttachments = [
        ...(message.attachments || []),
        ...parsed.attachments
      ];
      return {
        content: parsed.content,
        attachments: allAttachments
      };
    }
    return { content: message.content, attachments: message.attachments || [] };
  }, [message.content, message.attachments, isUser]);
  
  // 检测定时任务触发格式的消息
  const schedulerTrigger = useMemo(() => {
    if (isUser && parsedMessage.content) {
      return parseSchedulerTrigger(parsedMessage.content);
    }
    return { isSchedulerTrigger: false };
  }, [isUser, parsedMessage.content]);
  
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
  
  // 检测到定时任务触发格式的消息
  if (schedulerTrigger.isSchedulerTrigger) {
    return (
      <div className="message-row scheduler-trigger user">
        <div 
          className={`scheduler-trigger-container ${showSchedulerDetail ? 'expanded' : ''}`}
        >
          <div 
            className="scheduler-trigger-bubble clickable"
            onClick={() => setShowSchedulerDetail(!showSchedulerDetail)}
          >
            <Icons.Clock size={16} />
            <span>{t('common.schedulerTrigger')}：</span>
            <span className="task-name">{schedulerTrigger.taskName}</span>
            <Icons.ChevronDown size={14} className={`chevron-icon ${showSchedulerDetail ? 'rotated' : ''}`} />
          </div>
          <div className="scheduler-trigger-detail">
            <pre>{schedulerTrigger.fullContent}</pre>
          </div>
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
            <>
              <div className="user-text">{parsedMessage.content}</div>
              {parsedMessage.attachments && parsedMessage.attachments.length > 0 && (
                <div className="message-attachments">
                  {parsedMessage.attachments.map((att, idx) => (
                    <div key={idx} className="message-attachment-item">
                      <div className="message-attachment-icon">
                        {att.file_type === 'image' ? (
                          <Icons.Image size={14} />
                        ) : (
                          <Icons.File size={14} />
                        )}
                      </div>
                      <span className="message-attachment-name">{att.filename}</span>
                      <span className="message-attachment-size">{formatFileSize(att.file_size)}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
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
            return <JsonBlockCard key={idx} jsonStr={group.content} />;
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

function TimelineItem({ segment }: { segment: TimelineSegment }): React.ReactNode {
  if (segment.kind === 'text') {
    return renderContentWithCollapsibleJson(segment.content);
  }

  if (segment.kind === 'thinking') {
    return <JsonBlockCard jsonStr={segment.content} />;
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
