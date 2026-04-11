# -*- coding: utf-8 -*-
"""
循环事件发布器

职责：
  - 封装事件发布逻辑
  - 提供类型安全的事件发布接口
  - 处理事件发布异常
"""

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.bus.event_bus import EventBus

from app.agent.events.event_types import AgentEventType
from app.agent.events.event_models import (
    MessageReceivedEvent,
    ResponseStartEvent,
    ResponseChunkEvent,
    ResponseCompleteEvent,
    ToolCallStartEvent,
    ToolCallEndEvent,
    ToolCallErrorEvent,
    LoopIterationEvent,
    LoopEndEvent,
    AgentErrorEvent,
)

logger = logging.getLogger(__name__)


class LoopEventPublisher:
    """
    循环事件发布器

    封装 AgentLoop 中的事件发布逻辑，提供更清晰的接口。
    """

    def __init__(self, event_bus: "EventBus"):
        """
        初始化事件发布器

        Args:
            event_bus: 事件总线实例
        """
        self._bus = event_bus

    def publish_message_received(
        self,
        session_id: str,
        message: str,
    ):
        """发布消息接收事件"""
        try:
            self._bus.publish(
                AgentEventType.MESSAGE_RECEIVED,
                MessageReceivedEvent(
                    event_type=AgentEventType.MESSAGE_RECEIVED,
                    data={"message": message},
                    session_id=session_id,
                    message=message,
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 MESSAGE_RECEIVED 失败: %s", e)

    def publish_loop_iteration(
        self,
        session_id: str,
        iteration: int,
        max_iterations: int,
        has_tool_calls: bool,
    ):
        """发布循环迭代事件"""
        try:
            self._bus.publish(
                AgentEventType.LOOP_ITERATION,
                LoopIterationEvent(
                    event_type=AgentEventType.LOOP_ITERATION,
                    data={
                        "current_iteration": iteration,
                        "max_iterations": max_iterations,
                        "has_tool_calls": has_tool_calls,
                    },
                    session_id=session_id,
                    iteration=iteration,
                    current_iteration=iteration,
                    max_iterations=max_iterations,
                    has_tool_calls=has_tool_calls,
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 LOOP_ITERATION 失败: %s", e)

    def publish_tool_call_start(
        self,
        session_id: str,
        iteration: int,
        tool_name: str,
        arguments: Dict[str, Any],
        call_id: Optional[str] = None,
    ):
        """发布工具调用开始事件"""
        try:
            self._bus.publish(
                AgentEventType.TOOL_CALL_START,
                ToolCallStartEvent(
                    event_type=AgentEventType.TOOL_CALL_START,
                    data={
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "call_id": call_id,
                    },
                    session_id=session_id,
                    iteration=iteration,
                    tool_name=tool_name,
                    arguments=arguments,
                    call_id=call_id or "",
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 TOOL_CALL_START 失败: %s", e)

    def publish_tool_call_end(
        self,
        session_id: str,
        iteration: int,
        tool_name: str,
        call_id: Optional[str],
        result: Any,
        duration_ms: float,
    ):
        """发布工具调用结束事件"""
        try:
            self._bus.publish(
                AgentEventType.TOOL_CALL_END,
                ToolCallEndEvent(
                    event_type=AgentEventType.TOOL_CALL_END,
                    data={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "result": result,
                        "duration_ms": duration_ms,
                    },
                    session_id=session_id,
                    iteration=iteration,
                    tool_name=tool_name,
                    call_id=call_id or "",
                    result=result,
                    duration_ms=duration_ms,
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 TOOL_CALL_END 失败: %s", e)

    def publish_tool_call_error(
        self,
        session_id: str,
        iteration: int,
        tool_name: str,
        call_id: Optional[str],
        error: str,
    ):
        """发布工具调用错误事件"""
        try:
            self._bus.publish(
                AgentEventType.TOOL_CALL_ERROR,
                ToolCallErrorEvent(
                    event_type=AgentEventType.TOOL_CALL_ERROR,
                    data={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "error": error,
                    },
                    session_id=session_id,
                    iteration=iteration,
                    tool_name=tool_name,
                    call_id=call_id or "",
                    error=error,
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 TOOL_CALL_ERROR 失败: %s", e)

    def publish_response_complete(
        self,
        session_id: str,
        iteration: int,
        content: str,
        total_time_ms: float,
    ):
        """发布响应完成事件"""
        try:
            self._bus.publish(
                AgentEventType.RESPONSE_COMPLETE,
                ResponseCompleteEvent(
                    event_type=AgentEventType.RESPONSE_COMPLETE,
                    data={
                        "content": content,
                        "iterations": iteration,
                        "response_type": "response",
                        "total_time_ms": total_time_ms,
                    },
                    session_id=session_id,
                    iteration=iteration,
                    content=content,
                    iterations=iteration,
                    response_type="response",
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 RESPONSE_COMPLETE 失败: %s", e)

    def publish_loop_end(
        self,
        session_id: str,
        total_iterations: int,
        reason: str,
        result: Optional[Dict] = None,
    ):
        """发布循环结束事件"""
        try:
            self._bus.publish(
                AgentEventType.LOOP_END,
                LoopEndEvent(
                    event_type=AgentEventType.LOOP_END,
                    data={
                        "total_iterations": total_iterations,
                        "reason": reason,
                        "result": result or {},
                    },
                    session_id=session_id,
                    total_iterations=total_iterations,
                    reason=reason,
                    result=result or {},
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 LOOP_END 失败: %s", e)

    def publish_error(
        self,
        session_id: str,
        error_type: str,
        error_message: str,
        traceback: str = "",
    ):
        """发布错误事件"""
        try:
            self._bus.publish(
                AgentEventType.ERROR,
                AgentErrorEvent(
                    event_type=AgentEventType.ERROR,
                    data={
                        "error_type": error_type,
                        "error_message": error_message,
                        "traceback": traceback,
                    },
                    session_id=session_id,
                    error_type=error_type,
                    error_message=error_message,
                    traceback=traceback,
                ),
            )
        except Exception as e:
            logger.warning("[LoopEventPublisher] 发布 ERROR 失败: %s", e)
