# -*- coding: utf-8 -*-
"""
Memory 接口协议 (Protocol)

定义记忆系统的标准接口，模块间依赖协议而非具体类。
支持静态类型检查和鸭子类型。
"""

from __future__ import annotations

from typing import List, Dict, Any, Protocol, Optional


class MemoryProtocol(Protocol):
    """MemoryManager 必须实现的接口协议"""

    def add_user_message(self, content: str) -> None:
        """添加用户消息到对话历史"""
        ...

    def add_assistant_message(self, content: str) -> None:
        """添加助手回复到对话历史"""
        ...

    def add_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        记录工具调用，返回 call_id

        Returns:
            唯一调用 ID
        """
        ...

    def add_tool_result(self, call_id: str, result: Dict[str, Any]) -> None:
        """记录工具调用结果"""
        ...

    def get_messages(self) -> List[Dict[str, Any]]:
        """
        获取完整消息列表（含 tool_call/tool_result）

        Returns:
            [{"role": "user|assistant|system|tool", ...}, ...]
        """
        ...
