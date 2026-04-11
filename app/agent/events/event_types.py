# -*- coding: utf-8 -*-
"""
Agent 事件类型定义
"""

from enum import Enum


class AgentEventType(str, Enum):
    """Agent 生命周期事件类型"""

    # 消息相关
    MESSAGE_RECEIVED = "agent.message_received"          # 用户消息进入
    RESPONSE_START = "agent.response_start"              # 响应开始生成
    RESPONSE_CHUNK = "agent.response_chunk"              # 流式响应每个 chunk
    RESPONSE_COMPLETE = "agent.response_complete"        # 完整响应结束

    # 工具调用相关
    TOOL_CALL_START = "agent.tool_call_start"            # 工具调用开始
    TOOL_CALL_END = "agent.tool_call_end"                # 工具调用完成（含结果）
    TOOL_CALL_ERROR = "agent.tool_call_error"            # 工具调用错误

    # 记忆相关
    MEMORY_SEARCH = "agent.memory_search"                # 记忆检索触发
    MEMORY_SEARCH_RESULT = "agent.memory_search_result"  # 记忆检索结果
    MEMORY_SAVE = "agent.memory_save"                    # 对话保存到记忆

    # 生命周期相关
    LOOP_START = "agent.loop_start"                      # Agent 循环开始
    LOOP_ITERATION = "agent.loop_iteration"              # 每轮迭代
    LOOP_END = "agent.loop_end"                          # Agent 循环结束（正常/超限）

    # 错误
    ERROR = "agent.error"                                # Agent 错误

    def __str__(self):
        return self.value
