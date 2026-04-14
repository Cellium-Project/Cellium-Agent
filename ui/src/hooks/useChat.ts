import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../stores/appStore';
import { API, postJSON } from '../utils/api';
import type { SSEEvent, Message, ToolTrace, TimelineSegment } from '../types';

export function useChat() {
  const abortControllerRef = useRef<AbortController | null>(null);
  const connectionIdRef = useRef(0);
  const lastEventIdBySessionRef = useRef<Record<string, number>>({});
  const {
    currentSessionId,
    messages,
    streamingMessage,
    isStreaming,
    hasRunningTask,
    setIsStreaming,
    setHasRunningTask,
    updateStreamingMessage,
    addMessage,
    fetchSessions,
    checkTaskStatus,
    stopTask,
  } = useAppStore();

  // Build streaming message context — 使用有序时间线
  const buildStreamingContext = useCallback(() => ({
    timeline: [] as TimelineSegment[],
    traces: [] as ToolTrace[],
    lastEventId: 0,
    finalized: false,
    stopRequested: false,
    stoppedByServer: false,
    sawDone: false,
  }), []);

  // ★ 连接到正在运行的任务（重新连接）
  const reconnectToTask = useCallback(async (sessionId: string) => {
    // 防止重复连接
    if (abortControllerRef.current) {
      if (import.meta.env.DEV) {
        console.log('[reconnectToTask] 已经在连接中，跳过');
      }
      return;
    }

    const ctx = buildStreamingContext();
    ctx.lastEventId = lastEventIdBySessionRef.current[sessionId] || 0;
    if (streamingMessage?.timeline?.length) {
      ctx.timeline = [...streamingMessage.timeline];
    }
    if (streamingMessage?.toolTraces?.length) {
      ctx.traces = [...streamingMessage.toolTraces];
    }
    const connectionId = ++connectionIdRef.current;
    if (!streamingMessage) {
      updateStreamingMessage({
        role: 'assistant',
        content: '',
        toolTraces: [],
        timeline: [],
      });
    }
    setIsStreaming(true);

    abortControllerRef.current = new AbortController();

    try {
      const response = await fetch(API.stream, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: '', session_id: sessionId, last_event_id: ctx.lastEventId }),  // 空消息表示重新连接
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader available');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6);
          if (!raw || raw === '[DONE]') continue;

          try {
            const event: SSEEvent = JSON.parse(raw);
            handleSSEEvent(event, ctx, sessionId, connectionId);
          } catch (e) {
            if (import.meta.env.DEV) {
              console.warn('Failed to parse SSE event:', raw);
            }
          }
        }
      }

      // Finalize message
      finalizeMessage(ctx, connectionId);
      fetchSessions();
    } catch (error: any) {
      if (error.name !== 'AbortError') {
        if (import.meta.env.DEV) {
          console.error('Reconnect error:', error);
        }
        updateStreamingMessage(null);
      }
    } finally {
      setIsStreaming(false);
      setHasRunningTask(await checkTaskStatus(sessionId));
      abortControllerRef.current = null;
    }
  }, [buildStreamingContext, updateStreamingMessage, setIsStreaming, setHasRunningTask, fetchSessions, checkTaskStatus, streamingMessage]);

  // Finalize message helper
  const finalizeMessage = useCallback((ctx: ReturnType<typeof buildStreamingContext>, connectionId?: number) => {
    if (ctx.finalized) {
      updateStreamingMessage(null);
      return;
    }
    if (typeof connectionId === 'number' && connectionId !== connectionIdRef.current) {
      return;
    }

    const finalText = ctx.timeline
      .filter(s => s.kind === 'text')
      .map(s => s.content)
      .join('\n\n');

    const hasUsefulContent = Boolean(finalText.trim()) || ctx.traces.length > 0 || ctx.timeline.length > 0;
    ctx.finalized = true;

    if (!hasUsefulContent) {
      updateStreamingMessage(null);
      return;
    }

    const finalMessage: Message = {
      role: 'assistant',
      content: finalText,
      toolTraces: ctx.traces,
      timeline: ctx.timeline,
    };
    addMessage(finalMessage);
    updateStreamingMessage(null);
  }, [addMessage, updateStreamingMessage]);

  // Send message — 发送消息并处理流式响应
  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;

    const sessionId = currentSessionId || 'default';

    if (isStreaming) {
      addMessage({ role: 'user', content: content.trim() });
      try {
        await postJSON(API.supplement, { message: content.trim(), session_id: sessionId });
      } catch (error: any) {
        addMessage({ role: 'assistant', content: `补充消息发送失败: ${error.message}` });
      }
      return;
    }

    // Add user message
    addMessage({ role: 'user', content: content.trim() });
    setIsStreaming(true);
    setHasRunningTask(true);

    // Create streaming message placeholder
    const ctx = buildStreamingContext();
    lastEventIdBySessionRef.current[sessionId] = 0;
    const connectionId = ++connectionIdRef.current;
    updateStreamingMessage({
      role: 'assistant',
      content: '',
      toolTraces: [],
      timeline: [],
    });

    abortControllerRef.current = new AbortController();

    try {
      const response = await fetch(API.stream, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: content.trim(), session_id: sessionId, last_event_id: 0 }),
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader available');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6);
          if (!raw || raw === '[DONE]') continue;

          try {
            const event: SSEEvent = JSON.parse(raw);
            handleSSEEvent(event, ctx, sessionId, connectionId);
          } catch (e) {
            if (import.meta.env.DEV) {
              console.warn('Failed to parse SSE event:', raw);
            }
          }
        }
      }

      // Finalize message
      finalizeMessage(ctx, connectionId);

      // Refresh session list
      fetchSessions();
    } catch (error: any) {
      if (error.name !== 'AbortError') {
        addMessage({ role: 'assistant', content: `错误: ${error.message}` });
        updateStreamingMessage(null);
      }
    } finally {
      setIsStreaming(false);
      setHasRunningTask(await checkTaskStatus(sessionId));
      abortControllerRef.current = null;
    }
  }, [currentSessionId, isStreaming, addMessage, setIsStreaming, setHasRunningTask, updateStreamingMessage, buildStreamingContext, fetchSessions, finalizeMessage, checkTaskStatus]);

  // Handle SSE event — 按时间顺序构建时间线
  const handleSSEEvent = useCallback((event: SSEEvent, ctx: ReturnType<typeof buildStreamingContext>, sessionId: string, connectionId: number) => {
    if (connectionId !== connectionIdRef.current) return;
    if (event.session_id && event.session_id !== sessionId) return;
    if (event.event_id && event.event_id > ctx.lastEventId) {
      ctx.lastEventId = event.event_id;
      lastEventIdBySessionRef.current[sessionId] = event.event_id;
    }
    switch (event.type) {
      case 'thinking':
        updateStreamingMessage({
          role: 'assistant',
          content: event.content || '正在思考...',
          toolTraces: ctx.traces,
          timeline: [...ctx.timeline],
        });
        break;

      case 'tool_start': {
        if (event.tool && event.arguments) {
          // 追加工具片段到时间线（保持顺序）
          const toolSeg: TimelineSegment = {
            kind: 'tool',
            tool: event.tool,
            arguments: event.arguments,
            duration_ms: 0,
            description: event.description,
            status: 'running',
            call_id: event.call_id,  // 使用 call_id 唯一标识
          };
          ctx.timeline.push(toolSeg);

          // 同时维护 traces 数组用于快速查找
          ctx.traces.push({
            tool: event.tool,
            arguments: event.arguments,
            duration_ms: 0,
            description: event.description,
            call_id: event.call_id,  // ★ 使用 call_id 唯一标识
          });

          updateStreamingMessage({
            role: 'assistant',
            content: '',
            toolTraces: [...ctx.traces],
            timeline: [...ctx.timeline],
          });
        }
        break;
      }

      case 'tool_result': {
        // ★ 使用 call_id 精确匹配 tool_start 和 tool_result
        const targetCallId = event.call_id;
        if (targetCallId && ctx.traces.length > 0) {
          // 找到匹配的 tool segment（通过 call_id）
          for (let i = ctx.timeline.length - 1; i >= 0; i--) {
            const seg = ctx.timeline[i];
            if (seg.kind === 'tool' && seg.call_id === targetCallId && seg.status === 'running') {
              seg.status = 'done';
              seg.duration_ms = event.duration_ms || 0;
              seg.result = event.result;
              break;
            }
          }

          // 同步更新 traces
          for (let i = ctx.traces.length - 1; i >= 0; i--) {
            if (ctx.traces[i].call_id === targetCallId) {
              ctx.traces[i] = {
                ...ctx.traces[i],
                result: event.result,
                duration_ms: event.duration_ms || 0,
              };
              break;
            }
          }

          updateStreamingMessage({
            role: 'assistant',
            content: '',
            toolTraces: [...ctx.traces],
            timeline: [...ctx.timeline],
          });
        } else if (event.tool && ctx.traces.length > 0) {
          // 降级：如果没有 call_id，使用工具名匹配（兼容旧版本）
          for (let i = ctx.timeline.length - 1; i >= 0; i--) {
            const seg = ctx.timeline[i];
            if (seg.kind === 'tool' && seg.tool === event.tool && seg.status === 'running') {
              seg.status = 'done';
              seg.duration_ms = event.duration_ms || 0;
              seg.result = event.result;
              break;
            }
          }
          for (let i = ctx.traces.length - 1; i >= 0; i--) {
            if (ctx.traces[i].tool === event.tool) {
              ctx.traces[i] = {
                ...ctx.traces[i],
                result: event.result,
                duration_ms: event.duration_ms || 0,
              };
              break;
            }
          }
          updateStreamingMessage({
            role: 'assistant',
            content: '',
            toolTraces: [...ctx.traces],
            timeline: [...ctx.timeline],
          });
        }
        break;
      }

      case 'content_chunk': {
        const rawChunk = event.content || '';
        // ★ 不要 trim()，保留原始格式（包括换行和缩进）
        // 只过滤掉完全空的 chunk
        if (import.meta.env.DEV) {
          console.log('[content_chunk] received:', rawChunk.slice(0, 100), 'timeline length before:', ctx.timeline.length);
        }
        if (rawChunk.length > 0) {
          ctx.timeline.push({ kind: 'text', content: rawChunk });
        }

        updateStreamingMessage({
          role: 'assistant',
          content: '',
          toolTraces: ctx.traces,
          timeline: [...ctx.timeline],
        });
        if (import.meta.env.DEV) {
          console.log('[content_chunk] updated timeline length:', ctx.timeline.length);
        }
        break;
      }

      case 'done':
        ctx.sawDone = true;
        if (event.tool_traces && event.tool_traces.length > 0) {
          if (ctx.traces.length === 0) {
            ctx.traces = event.tool_traces;
          }
        }
        finalizeMessage(ctx, connectionId);
        break;

      case 'error': {
        const errChunk = `错误: ${event.error || '未知错误'}`;
        ctx.timeline.push({ kind: 'text', content: errChunk });
        updateStreamingMessage({
          role: 'assistant',
          content: '',
          toolTraces: ctx.traces,
          timeline: [...ctx.timeline],
        });
        break;
      }

      case 'stopped': {
        if (ctx.sawDone) {
          break;
        }
        ctx.stoppedByServer = true;
        const stopMsg = (() => {
          switch (event.reason) {
            case 'user_cancelled':
              return '已停止生成';
            case 'max_iterations_exceeded':
              return '已停止：达到最大迭代次数';
            case 'loop_detected':
              return '已停止：检测到重复输出';
            default:
              return event.reason ? `已停止：${event.reason}` : '已停止生成';
          }
        })();
        ctx.timeline.push({ kind: 'text', content: stopMsg });
        updateStreamingMessage({
          role: 'assistant',
          content: '[正在停止...]',
          toolTraces: ctx.traces,
          timeline: [...ctx.timeline],
        });
        finalizeMessage(ctx, connectionId);
        break;
      }
    }
  }, [updateStreamingMessage, finalizeMessage]);

  // Stop streaming — ★ 改为调用后端停止接口
  const stopStreaming = useCallback(async () => {
    const sessionId = currentSessionId || 'default';
    
    if (streamingMessage) {
      updateStreamingMessage({
        ...streamingMessage,
        content: '[正在停止...]',
      });
    }

    // 调用后端停止任务
    await stopTask(sessionId);
  }, [currentSessionId, stopTask, streamingMessage, updateStreamingMessage]);

  // ★ 页面加载时检查是否有运行中的任务
  useEffect(() => {
    const checkAndReconnect = async () => {
      if (!currentSessionId) return;
      
      const hasTask = await checkTaskStatus(currentSessionId);
      if (hasTask) {
        // 有运行中的任务，自动重新连接
        reconnectToTask(currentSessionId);
      }
    };

    checkAndReconnect();
  }, [currentSessionId, checkTaskStatus, reconnectToTask]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  return {
    sendMessage,
    stopStreaming,
    messages,
    streamingMessage,
    isStreaming,
    hasRunningTask,
    reconnectToTask,
  };
}
