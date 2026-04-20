import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../stores/appStore';
import { API, postJSON } from '../utils/api';
import type { SSEEvent, Message, ToolTrace, TimelineSegment } from '../types';
import type { HybridPhase } from '../stores/appStore';

export function useChat() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const connectionIdRef = useRef(0);
  const lastEventIdBySessionRef = useRef<Record<string, number>>({});
  const isManualCloseRef = useRef(false);
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
    setHybridPhase,
  } = useAppStore();

  function buildStreamingContext() {
    return {
      timeline: [] as TimelineSegment[],
      traces: [] as ToolTrace[],
      lastEventId: 0,
      finalized: false,
      stopRequested: false,
      stoppedByServer: false,
      sawDone: false,
    };
  }

  const finalizeMessage = useCallback((ctx: ReturnType<typeof buildStreamingContext>, connectionId?: number) => {
    if (ctx.finalized) {
      updateStreamingMessage(null);
      return;
    }
    if (typeof connectionId === 'number' && connectionId !== connectionIdRef.current && !ctx.sawDone) {
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

  const handleChatEvent = useCallback((event: SSEEvent, ctx: ReturnType<typeof buildStreamingContext>, sessionId: string, connectionId: number) => {
    if (connectionId !== connectionIdRef.current) return;
    if (event.session_id && event.session_id !== sessionId) return;
    if (event.event_id && event.event_id > ctx.lastEventId) {
      ctx.lastEventId = event.event_id;
      lastEventIdBySessionRef.current[sessionId] = event.event_id;
    }
    switch (event.type) {
      case 'thinking':
        // 只显示来自 LLM reasoning 的 thinking，过滤系统默认消息
        const content = event.content || '';
        const isSystemDefault = ['正在思考...', '正在压缩会话记忆...', '正在根据控制决策压缩上下文...', 
                                  '分析结果中...', '输出被截断，正在补充...', '正在分析工具定义...']
                                  .includes(content);
        if (!isSystemDefault && content.trim()) {
          const thinkingSeg: TimelineSegment = {
            kind: 'thinking',
            content: content,
          };
          ctx.timeline.push(thinkingSeg);
          updateStreamingMessage({
            role: 'assistant',
            content: '', // thinking 不直接显示在消息内容中
            toolTraces: ctx.traces,
            timeline: [...ctx.timeline],
          });
        }
        break;

      case 'tool_start': {
        if (event.tool && event.arguments) {
          const toolSeg: TimelineSegment = {
            kind: 'tool',
            tool: event.tool,
            arguments: event.arguments,
            duration_ms: 0,
            description: event.description,
            status: 'running',
            call_id: event.call_id,
          };
          ctx.timeline.push(toolSeg);

          ctx.traces.push({
            tool: event.tool,
            arguments: event.arguments,
            duration_ms: 0,
            description: event.description,
            call_id: event.call_id,
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
        const targetCallId = event.call_id;
        if (targetCallId && ctx.traces.length > 0) {
          for (let i = ctx.timeline.length - 1; i >= 0; i--) {
            const seg = ctx.timeline[i];
            if (seg.kind === 'tool' && seg.call_id === targetCallId && seg.status === 'running') {
              seg.status = 'done';
              seg.duration_ms = event.duration_ms || 0;
              seg.result = event.result;
              break;
            }
          }
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
        setIsStreaming(false);
        setHasRunningTask(false);
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
        setIsStreaming(false);
        setHasRunningTask(false);
        break;
      }

      case 'message_received': {
        if (event.message && !streamingMessage) {
          updateStreamingMessage({
            role: 'assistant',
            content: '',
            toolTraces: [],
            timeline: [],
          });
        }
        break;
      }

      case 'supplement_injected': {
        ctx.timeline.push({
          kind: 'text',
          content: `[补充信息已注入: ${(event.content || '').slice(0, 30)}...]`,
        });
        updateStreamingMessage({
          role: 'assistant',
          content: '[正在根据补充信息继续...]',
          toolTraces: ctx.traces,
          timeline: [...ctx.timeline],
        });
        break;
      }

      case 'hybrid_phase': {
        setHybridPhase(
          (event.phase || 'observe') as HybridPhase,
          event.message || '',
          event.description || ''
        );
        break;
      }
    }
  }, [updateStreamingMessage, finalizeMessage, streamingMessage, setIsStreaming, setHasRunningTask, setHybridPhase]);

  // 创建新 WebSocket 连接的核心函数
  const createNewConnection = useCallback((
    sessionId: string,
    ctx: ReturnType<typeof buildStreamingContext>,
    connectionId: number,
    connectingKey: string
  ) => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/session-events/ws?session_id=${encodeURIComponent(sessionId)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    isManualCloseRef.current = false;

    ws.onopen = () => {
      console.log('[WS] Connected for chat');
      (window as any)[connectingKey] = false;

      heartbeatTimerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);

      ws.send(JSON.stringify({
        type: 'subscribe',
        session_id: sessionId,
      }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'pong') return;
        if (msg.type === 'subscribed') return;
        if (msg.type === 'connected') return;

        console.log('[WS] 收到消息:', msg.type, msg.data?.type);

        if (msg.type === 'chat_event' && msg.data) {
          handleChatEvent(msg.data as SSEEvent, ctx, sessionId, connectionId);
        }
      } catch (e) {
        console.error('[WS] parse error:', e);
      }
    };

    ws.onerror = () => {
      console.warn('[WS] Error');
      (window as any)[connectingKey] = false;
    };

    ws.onclose = () => {
      console.log('[WS] Disconnected');
      (window as any)[connectingKey] = false;

      if (heartbeatTimerRef.current) {
        clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }

      if (connectionId !== connectionIdRef.current) {
        console.log('[WS] Stale connection closed, ignoring');
        return;
      }

      if (!(ws as any).isManualClose && !ctx.finalized) {
        reconnectTimerRef.current = setTimeout(() => {
          const newConnectingKey = `ws_connecting_${sessionId}`;
          (window as any)[newConnectingKey] = false;
          const newConnectionId = ++connectionIdRef.current;
          createNewConnection(sessionId, ctx, newConnectionId, newConnectingKey);
        }, 3000);
      } else {
        setIsStreaming(false);
      }
    };
  }, [handleChatEvent, setIsStreaming]);

  // 连接 WebSocket（处理旧连接关闭）
  const connectWebSocket = useCallback((sessionId: string, ctx: ReturnType<typeof buildStreamingContext>, connectionId: number, isReconnect: boolean) => {
    const connectingKey = `ws_connecting_${sessionId}`;
    if ((window as any)[connectingKey]) {
      console.log('[WS] Already connecting, skipping');
      return;
    }
    (window as any)[connectingKey] = true;

    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    // 先关闭旧连接并等待其完成
    const oldWs = wsRef.current;
    if (oldWs) {
      (oldWs as any).isManualClose = true;
      if (oldWs.readyState === WebSocket.OPEN || oldWs.readyState === WebSocket.CONNECTING) {
        oldWs.close();
        // 等待旧连接关闭后再创建新连接
        const checkAndConnect = () => {
          if (oldWs.readyState === WebSocket.CLOSED) {
            createNewConnection(sessionId, ctx, connectionId, connectingKey);
          } else {
            setTimeout(checkAndConnect, 50);
          }
        };
        checkAndConnect();
        return;
      }
    }

    // 无旧连接，直接创建
    createNewConnection(sessionId, ctx, connectionId, connectingKey);
  }, [createNewConnection]);

  const disconnectWebSocket = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
    if (wsRef.current) {
      (wsRef.current as any).isManualClose = true;
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const reconnectToTask = useCallback(async (sessionId: string) => {
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
      const hasTask = await checkTaskStatus(sessionId);
      if (hasTask) {
        const connectionId = ++connectionIdRef.current;
        connectWebSocket(sessionId, ctx, connectionId, true);
      } else {
        updateStreamingMessage(null);
        setIsStreaming(false);
        setHasRunningTask(false);
      }
    } catch (error: any) {
      if (import.meta.env.DEV) {
        console.error('Reconnect error:', error);
      }
      updateStreamingMessage(null);
      setIsStreaming(false);
    } finally {
      abortControllerRef.current = null;
    }
  }, [buildStreamingContext, updateStreamingMessage, setIsStreaming, setHasRunningTask, fetchSessions, checkTaskStatus, streamingMessage, connectWebSocket]);

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

    addMessage({ role: 'user', content: content.trim() });
    setIsStreaming(true);
    setHasRunningTask(true);

    const ctx = buildStreamingContext();
    lastEventIdBySessionRef.current[sessionId] = 0;
    updateStreamingMessage({
      role: 'assistant',
      content: '正在思考...',
      toolTraces: [],
      timeline: [],
    });

    abortControllerRef.current = new AbortController();

    try {
      const connectionId = ++connectionIdRef.current;
      connectWebSocket(sessionId, ctx, connectionId, false);

      const response = await fetch(API.stream, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: content.trim(), session_id: sessionId, last_event_id: 0 }),
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const result = await response.json();

      if (result.status === 'error') {
        throw new Error(result.error);
      }

    } catch (error: any) {
      if (error.name !== 'AbortError') {
        addMessage({ role: 'assistant', content: `错误: ${error.message}` });
        updateStreamingMessage(null);
        setIsStreaming(false);
      }
    } finally {
      abortControllerRef.current = null;
    }
  }, [currentSessionId, isStreaming, addMessage, setIsStreaming, setHasRunningTask, updateStreamingMessage, buildStreamingContext, fetchSessions, finalizeMessage, checkTaskStatus, handleChatEvent, connectWebSocket]);

  const stopStreaming = useCallback(async () => {
    const sessionId = currentSessionId || 'default';

    if (streamingMessage) {
      updateStreamingMessage({
        ...streamingMessage,
        content: '[正在停止...]',
      });
    }

    disconnectWebSocket();
    await stopTask(sessionId);
  }, [currentSessionId, stopTask, streamingMessage, updateStreamingMessage, disconnectWebSocket]);

  // 使用 ref 跟踪 session，避免 useEffect 依赖不稳定
  const currentSessionIdRef = useRef(currentSessionId);
  const hasReconnectedRef = useRef(false);
  const prevSessionIdRef = useRef(currentSessionId);

  // session 切换时断开旧连接并重置状态
  useEffect(() => {
    if (prevSessionIdRef.current && prevSessionIdRef.current !== currentSessionId) {
      console.log('[WS] Session changed, disconnecting old connection');
      disconnectWebSocket();
      hasReconnectedRef.current = false;
    }
    prevSessionIdRef.current = currentSessionId;
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId, disconnectWebSocket]);

  useEffect(() => {
    let mounted = true;

    const checkAndReconnect = async () => {
      const sessionId = currentSessionIdRef.current;
      if (!sessionId || hasReconnectedRef.current) return;

      const hasTask = await checkTaskStatus(sessionId);
      if (!mounted || !hasTask) return;

      // 标记已尝试重连，避免重复
      hasReconnectedRef.current = true;

      const ctx = buildStreamingContext();
      ctx.lastEventId = lastEventIdBySessionRef.current[sessionId] || 0;

      updateStreamingMessage({
        role: 'assistant',
        content: '',
        toolTraces: [],
        timeline: [],
      });
      setIsStreaming(true);

      const connectionId = ++connectionIdRef.current;
      connectWebSocket(sessionId, ctx, connectionId, true);
    };

    checkAndReconnect();

    return () => {
      mounted = false;
    };
    // 只依赖 currentSessionId，其他函数通过 ref 或直接调用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId]);

  // 只在组件卸载时清理
  useEffect(() => {
    return () => {
      disconnectWebSocket();
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
    // 空依赖，只在卸载时执行
    // eslint-disable-next-line react-hooks/exhaustive-deps
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