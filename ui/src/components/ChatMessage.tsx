import React, { memo, useState, useEffect, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import type { Message, TimelineSegment } from '../types';
import { Icons } from './Icons';
import { Collapsible } from './Collapsible';
import { EditDiffCard, ShellOutputCard, parseUnifiedDiff } from './ToolOutput';

marked.setOptions({ gfm: true });

const markdownCache = new Map<string, string>();
const MAX_CACHE_SIZE = 100;

const FENCE_NEWLINE = /^([ \t]*)(\S[^\n`]*?)[ \t]*```[^\n`]*$/gm;

function normalizeMarkdown(content: string): string {
  return content.replace(FENCE_NEWLINE, '$1$2\n\n```');
}

function safeRenderMarkdown(content: string): string {
  if (!content) return '';
  
  const cached = markdownCache.get(content);
  if (cached) return cached;
  
  const rawHtml = marked.parse(normalizeMarkdown(content)) as string;
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

function splitJsonBlocks(content: string): Array<{ type: 'text' | 'json'; content: string }> {
  const segments: Array<{ type: 'text' | 'json'; content: string }> = [];
  const jsonBlockRegex = /```json\s*([\s\S]*?)\s*```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = jsonBlockRegex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: content.slice(lastIndex, match.index) });
    }
    segments.push({ type: 'json', content: match[1].trim() });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < content.length) {
    segments.push({ type: 'text', content: content.slice(lastIndex) });
  }
  if (segments.length === 0) {
    segments.push({ type: 'text', content });
  }
  return segments;
}

function renderContentWithCollapsibleJson(content: string): React.ReactNode {
  const segments = splitJsonBlocks(content);

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
        return <JsonBlockCard key={idx} jsonStr={seg.content} />;
      })}
    </>
  );
}

/** Collapsible card for a JSON reasoning/plan block */
const jsonParseCache = new Map<string, { label: string; content: string }>();
const MAX_PARSE_CACHE_SIZE = 30;

const JsonBlockCard: React.FC<{ jsonStr: string; isThinking?: boolean }> = memo(({ jsonStr, isThinking }) => {
  const { label, content } = useMemo(() => {
    const cached = jsonParseCache.get(jsonStr);
    if (cached) return cached;

    let parsed: any = null;
    try {
      parsed = JSON.parse(jsonStr);
    } catch {
      const result = { label: isThinking ? 'Thinking' : 'JSON', content: jsonStr };
      jsonParseCache.set(jsonStr, result);
      return result;
    }

    // thinking 段：提取 reasoning 字段显示
    if (isThinking && typeof parsed?.reasoning === 'string') {
      const result = { label: 'Thinking', content: parsed.reasoning };
      jsonParseCache.set(jsonStr, result);
      return result;
    }

    const result = {
      label: isThinking ? 'Thinking' : 'JSON',
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
  isStreaming?: boolean;
}

export const ChatMessage = memo<ChatMessageProps>(({ message, isStreaming }) => {
  const { t } = useTranslation();
  const isUser = message.role === 'user';
  const [showSchedulerDetail, setShowSchedulerDetail] = useState(false);
  
  const parsedMessage = useMemo(() => {
    if (isUser) {
      const parsed = parseAttachmentsFromMessage(message.content);
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
            <span className="assistant-content-wrap">
              {renderTimeline(message)}
              {isStreaming && <span className="streaming-cursor"><span /><span /><span /></span>}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

function renderTimeline(message: Message): React.ReactNode {
  if (message.timeline && message.timeline.length > 0) {
    type ToolSegment = Extract<TimelineSegment, { kind: 'tool' }>;
    type ThinkingSegment = Extract<TimelineSegment, { kind: 'thinking' }>;
    type GroupItem =
      | { kind: 'merged-text'; contents: string[] }
      | { kind: 'tool-batch'; items: ToolSegment[] }
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
        const last = groups[groups.length - 1];
        if (last && last.kind === 'tool-batch') {
          last.items.push(segment as ToolSegment);
        } else {
          groups.push({ kind: 'tool-batch', items: [segment as ToolSegment] });
        }
      }
      // 'reasoning' segments are intentionally not displayed
    }

    return (
      <>
        {groups.map((group, idx) => {
          if (group.kind === 'merged-text') {
            const merged = group.contents.join('');
            return <React.Fragment key={idx}>{renderContentWithCollapsibleJson(merged)}</React.Fragment>;
          }
          if (group.kind === 'thinking') {
            return <JsonBlockCard key={idx} jsonStr={group.content} isThinking />;
          }
          // tool batch
          return <ToolBatchCard key={idx} items={group.items} />;
        })}
      </>
    );
  }

  return (
    <>
      {message.toolTraces && message.toolTraces.length > 0 && (
        <ToolBatchCard
          items={message.toolTraces.map(tr => ({
            kind: 'tool' as const,
            tool: tr.tool,
            arguments: (tr.arguments || {}) as Record<string, any>,
            result: tr.result,
            duration_ms: tr.duration_ms || 0,
            description: tr.description,
            status: tr.result?.error ? ('error' as const) : ('done' as const),
          }))}
        />
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
    return <JsonBlockCard jsonStr={segment.content} isThinking />;
  }

  const seg = segment as Extract<TimelineSegment, { kind: 'tool' }>;
  return (
    <ToolTraceCard
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
  compact?: boolean;
}

const KEY_ARG_KEYS = ['file_path', 'command', 'url', 'path', 'pattern', 'query', 'keyword'];

function pickKeyArg(args: Record<string, any> | undefined): string {
  if (!args) return '';
  for (const key of KEY_ARG_KEYS) {
    const v = args[key];
    if (typeof v === 'string' && v) return v;
  }
  return '';
}

function useElapsed(running: boolean): number {
  const [elapsedMs, setElapsedMs] = useState(0);
  const startRef = useRef(Date.now());

  useEffect(() => {
    if (!running) return;
    startRef.current = Date.now();
    const timer = setInterval(() => setElapsedMs(Date.now() - startRef.current), 100);
    return () => clearInterval(timer);
  }, [running]);

  return running ? elapsedMs : 0;
}

function formatDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function ToolCallContent({ trace, status, compact }: ToolTraceCardProps & { compact?: boolean }): React.ReactNode {
  const { t } = useTranslation();
  const argsStr = JSON.stringify(trace.arguments || {}, null, 2);
  const resultPreview = makeResultPreview(trace.result, t);

  return (
    <>
      {!compact && trace.description && (
        <div className="tool-description">{String(trace.description)}</div>
      )}
      {trace.tool === 'edit' && status !== 'running' && trace.result?.diff && (
        <EditDiffCard
          diff={String(trace.result.diff)}
          filePath={String(trace.arguments?.file_path || trace.result?.path || '')}
        />
      )}
      {trace.tool === 'shell' && status !== 'running' && (trace.result?.output || trace.result?.stderr) && (
        <ShellOutputCard result={trace.result} />
      )}
      <pre className="tool-args">{argsStr}</pre>
      {status === 'running' && (
        <div className="tool-result"><span className="status-dot dot-running"></span>{t('chat.waitingForResult')}</div>
      )}
      {status !== 'running' && (!compact || trace.result?.error) && (
        <div className="tool-result">{resultPreview}</div>
      )}
    </>
  );
}

const ToolTraceCard: React.FC<ToolTraceCardProps> = ({ trace, status, compact }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const elapsed = useElapsed(status === 'running');
  const durStr = formatDuration(status === 'running' ? elapsed : (trace.duration_ms || 0));
  const keyArg = pickKeyArg(trace.arguments);
  const err = trace.result?.error ? String(trace.result.error) : '';

  const tail = (() => {
    if (status === 'running') {
      return <span className="tool-tail tool-tail-run">{t('chat.executing')}</span>;
    }
    if (err) {
      return <span className="tool-tail tool-tail-error">{err.length > 48 ? err.slice(0, 48) + '…' : err}</span>;
    }
    if (trace.tool === 'edit' && trace.result?.diff) {
      const { adds, dels } = parseUnifiedDiff(String(trace.result.diff));
      return (
        <span className="tool-tail">
          <span className="tail-add">+{adds}</span>
          <span className="tail-del">−{dels}</span>
        </span>
      );
    }
    if (trace.tool === 'shell') {
      const out = String(trace.result?.output || '');
      const lines = out ? out.split('\n').filter(Boolean).length : 0;
      const code = trace.result?.exit_code;
      return (
        <span className="tool-tail">
          {code !== undefined && (
            <span className={code === 0 ? 'tail-exit-ok' : 'tail-exit-fail'}>exit {code}</span>
          )}
          {lines > 0 && <span className="tail-lines">{lines} 行</span>}
        </span>
      );
    }
    return <span className="tool-tail tail-done">{t('chat.done')}</span>;
  })();

  return (
    <div className={`tool-trace ${status === 'running' ? 'tool-running' : ''} ${expanded ? 'tool-expanded' : ''}`}>
      <div
        className="tool-trace-header"
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(v => !v)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(v => !v); } }}
      >
        <span className={`tool-state-dot ${status || 'done'}`} />
        <span className="tool-trace-name">{String(trace.tool || 'unknown')}</span>
        {keyArg && (
          <span className="tool-trace-arg">{keyArg.length > 48 ? keyArg.slice(0, 48) + '…' : keyArg}</span>
        )}
        <span className="tool-trace-spacer" />
        {tail}
        <span className="tool-trace-time">{durStr}</span>
        <Icons.ChevronDown size={13} className={`tool-trace-chevron ${expanded ? 'rotated' : ''}`} />
      </div>
      <div className="tool-trace-body">
        <div className="tool-trace-body-inner">
          <ToolCallContent trace={trace} status={status} compact={compact} />
        </div>
      </div>
    </div>
  );
};

const ToolGroupItem: React.FC<ToolTraceCardProps> = ({ trace, status }) => {
  const [expanded, setExpanded] = useState(false);
  const elapsed = useElapsed(status === 'running');
  const durStr = formatDuration(status === 'running' ? elapsed : (trace.duration_ms || 0));
  const keyArg = pickKeyArg(trace.arguments);
  const err = trace.result?.error ? String(trace.result.error) : '';

  return (
    <div className={`tool-group-item ${status === 'running' ? 'item-running' : ''} ${expanded ? 'tool-expanded' : ''}`}>
      <div
        className="tool-group-item-head"
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(v => !v)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(v => !v); } }}
      >
        <span className={`tool-state-dot ${status || 'done'}`} />
        <span className="tool-group-item-arg">{keyArg || String(trace.tool || 'unknown')}</span>
        {err && <span className="tool-group-item-err">{err.length > 40 ? err.slice(0, 40) + '…' : err}</span>}
        <span className="tool-trace-spacer" />
        <span className="tool-group-item-time">{durStr}</span>
        <Icons.ChevronDown size={12} className={`tool-trace-chevron ${expanded ? 'rotated' : ''}`} />
      </div>
      <div className="tool-trace-body">
        <div className="tool-trace-body-inner">
          <ToolCallContent trace={trace} status={status} compact />
        </div>
      </div>
    </div>
  );
};

const ToolGroupCard: React.FC<{ items: Extract<TimelineSegment, { kind: 'tool' }>[] }> = ({ items }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const running = items.some(i => i.status === 'running');
  const errCount = items.filter(i => i.result?.error).length;
  const totalMs = items.reduce((s, i) => s + (i.duration_ms || 0), 0);
  const durStr = totalMs >= 1000 ? `${(totalMs / 1000).toFixed(1)}s` : `${totalMs}ms`;

  return (
    <div className={`tool-group ${running ? 'tool-running' : ''} ${expanded ? 'tool-expanded' : ''}`}>
      <div
        className="tool-trace-header"
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(v => !v)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(v => !v); } }}
      >
        <span className={`tool-state-dot ${running ? 'running' : errCount > 0 ? 'error' : 'done'}`} />
        <span className="tool-trace-name">{String(items[0].tool || 'unknown')}</span>
        <span className="tool-group-count">×{items.length}</span>
        <span className="tool-trace-spacer" />
        {running
          ? <span className="tool-tail tool-tail-run">{t('chat.executing')}</span>
          : errCount > 0
            ? <span className="tool-tail tool-tail-error">{t('chat.errorsCount', { count: errCount })}</span>
            : <span className="tool-tail tail-done">{t('chat.done')}</span>}
        {!running && <span className="tool-trace-time">{durStr}</span>}
        <Icons.ChevronDown size={13} className={`tool-trace-chevron ${expanded ? 'rotated' : ''}`} />
      </div>
      <div className="tool-trace-body">
        <div className="tool-trace-body-inner tool-group-items">
          {items.map((seg, i) => (
            <ToolGroupItem
              key={i}
              trace={{
                tool: seg.tool,
                arguments: seg.arguments,
                result: seg.result,
                duration_ms: seg.duration_ms,
                description: seg.description,
              }}
              status={seg.status}
            />
          ))}
        </div>
      </div>
    </div>
  );
};

function batchSubGroups(items: Extract<TimelineSegment, { kind: 'tool' }>[]): Array<{ tool: string; items: Extract<TimelineSegment, { kind: 'tool' }>[] }> {
  const subGroups: Array<{ tool: string; items: Extract<TimelineSegment, { kind: 'tool' }>[] }> = [];
  for (const seg of items) {
    const last = subGroups[subGroups.length - 1];
    if (last && last.tool === seg.tool) {
      last.items.push(seg);
    } else {
      subGroups.push({ tool: seg.tool, items: [seg] });
    }
  }
  return subGroups;
}

const ToolBatchCard: React.FC<{ items: Extract<TimelineSegment, { kind: 'tool' }>[] }> = ({ items }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const running = items.some(i => i.status === 'running');
  const errCount = items.filter(i => i.result?.error).length;
  const totalMs = items.reduce((s, i) => s + (i.duration_ms || 0), 0);

  const wasRunning = useRef(running);
  useEffect(() => {
    if (running && !wasRunning.current) setExpanded(true);
    if (wasRunning.current && !running) setExpanded(false);
    wasRunning.current = running;
  }, [running]);

  if (items.length === 1) {
    const seg = items[0];
    return (
      <ToolTraceCard
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
  }

  const subGroups = batchSubGroups(items);
  const summary = subGroups
    .map(g => (g.items.length > 1 ? `${g.tool} ×${g.items.length}` : g.tool))
    .join('  ');

  return (
    <div className={`tool-batch ${running ? 'tool-running' : ''} ${expanded ? 'tool-expanded' : ''}`}>
      <div
        className="tool-trace-header"
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(v => !v)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(v => !v); } }}
      >
        <span className={`tool-state-dot ${running ? 'running' : errCount > 0 ? 'error' : 'done'}`} />
        <span className="tool-trace-name">{t('chat.toolActivity')}</span>
        <span className="tool-batch-summary">{summary}</span>
        <span className="tool-trace-spacer" />
        {running
          ? <span className="tool-tail tool-tail-run">{t('chat.executing')}</span>
          : errCount > 0
            ? <span className="tool-tail tool-tail-error">{t('chat.errorsCount', { count: errCount })}</span>
            : <span className="tool-tail tail-done">{t('chat.done')}</span>}
        {!running && <span className="tool-trace-time">{formatDuration(totalMs)}</span>}
        <Icons.ChevronDown size={13} className={`tool-trace-chevron ${expanded ? 'rotated' : ''}`} />
      </div>
      <div className="tool-trace-body">
        <div className="tool-trace-body-inner tool-batch-items">
          {subGroups.map((g, i) => (
            g.items.length === 1
              ? (
                <ToolTraceCard
                  key={i}
                  compact
                  trace={{
                    tool: g.items[0].tool,
                    arguments: g.items[0].arguments,
                    result: g.items[0].result,
                    duration_ms: g.items[0].duration_ms,
                    description: g.items[0].description,
                  }}
                  status={g.items[0].status}
                />
              )
              : <ToolGroupCard key={i} items={g.items} />
          ))}
        </div>
      </div>
    </div>
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
