import type { TimelineSegment } from '../types';

type ToolSegment = Extract<TimelineSegment, { kind: 'tool' }>;

export interface SubagentInfo {
  name?: string;
  status?: string;
  tool_calls?: number;
  current_tool?: string | null;
  last_tool?: string | null;
  tools_used?: string[] | null;
  last_result_preview?: string | null;
  elapsed_s?: number | null;
  duration_s?: number | null;
  error?: string | null;
}

/** 卡片标题描述：名称 · 状态 · 工具 · 次数 · 耗时 · 预览/错误 */
export function describeSubagent(info: SubagentInfo): string {
  const name = info.name || 'subagent';
  const status = info.status || 'running';
  const parts: string[] = [name, status];
  const tool = info.current_tool || info.last_tool;
  const used = (info.tools_used || []).filter(Boolean) as string[];
  if (tool) parts.push(`工具 ${tool}`);
  else if (used.length) parts.push(`工具 ${used.join(', ')}`);
  if (info.tool_calls) parts.push(`第 ${info.tool_calls} 次`);
  const elapsed = info.elapsed_s ?? info.duration_s;
  if (elapsed) parts.push(`${Math.round(Number(elapsed))}s`);
  if (status === 'running' && info.last_result_preview) {
    parts.push(String(info.last_result_preview).slice(0, 80));
  }
  if (info.error) parts.push(String(info.error).slice(0, 60));
  return parts.join(' · ');
}

export function mergeSubagentSegment(
  timeline: TimelineSegment[],
  seg: ToolSegment
): TimelineSegment[] {
  const idx = timeline.findIndex(
    (s) => s.kind === 'tool' && s.call_id === seg.call_id
  );
  if (idx < 0) return [...timeline, seg];
  const next = [...timeline];
  next[idx] = { ...next[idx], ...seg };
  return next;
}
