# -*- coding: utf-8 -*-
"""
Agent 事件模型
"""

from dataclasses import dataclass, field
from typing import Any, Dict
from app.core.bus.event_models import BaseEvent


@dataclass
class AgentEvent(BaseEvent):
    """Agent 事件基类"""

    session_id: str = ""
    iteration: int = 0


@dataclass
class MessageReceivedEvent(AgentEvent):
    """用户消息接收事件"""
    message: str = ""


@dataclass
class ResponseStartEvent(AgentEvent):
    """响应开始事件"""
    pass


@dataclass
class ResponseChunkEvent(AgentEvent):
    """流式响应 chunk 事件"""
    chunk: str = ""
    is_final: bool = False


@dataclass
class ResponseCompleteEvent(AgentEvent):
    """响应完成事件"""
    content: str = ""
    iterations: int = 0
    response_type: str = "response"  # response | error


@dataclass
class ToolCallStartEvent(AgentEvent):
    """工具调用开始事件"""
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ToolCallEndEvent(AgentEvent):
    """工具调用完成事件"""
    tool_name: str = ""
    call_id: str = ""
    result: Any = None
    duration_ms: float = 0.0


@dataclass
class ToolCallErrorEvent(AgentEvent):
    """工具调用错误事件"""
    tool_name: str = ""
    call_id: str = ""
    error: str = ""


@dataclass
class MemorySearchEvent(AgentEvent):
    """记忆检索事件"""
    query: str = ""
    memory_type: str = "all"  # all | short_term | long_term | personality


@dataclass
class MemorySearchResultEvent(AgentEvent):
    """记忆检索结果事件"""
    query: str = ""
    results_count: int = 0
    results: list = field(default_factory=list)


@dataclass
class MemorySaveEvent(AgentEvent):
    """对话保存到记忆事件"""
    user_input: str = ""
    response: str = ""
    source_id: str = ""


@dataclass
class LoopIterationEvent(AgentEvent):
    """循环迭代事件"""
    current_iteration: int = 0
    max_iterations: int = 10
    has_tool_calls: bool = False


@dataclass
class LoopEndEvent(AgentEvent):
    """循环结束事件"""
    total_iterations: int = 0
    reason: str = ""  # complete | max_iterations_exceeded | error
    result: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentErrorEvent(AgentEvent):
    """Agent 错误事件"""
    error_type: str = ""
    error_message: str = ""
    traceback: str = ""
