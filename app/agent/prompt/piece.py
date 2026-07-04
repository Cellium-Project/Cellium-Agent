# -*- coding: utf-8 -*-
"""
PromptPiece - 提示词拼图块
"""

from dataclasses import dataclass
from typing import Optional, Literal


Stability = Literal["static", "daily", "session", "dynamic"]

# 稳定性说明:
#   static  — 永远不变（personality, thought_schema），对应 role: system
#   daily   — 每天变一次（日期, 环境信息），对应 role: user
#   session — 会话内稳定（长期记忆检索结果），对应 role: user
#   dynamic — 每次请求都变（runtime_status, 系统指令等），对应 role: user


@dataclass
class PromptPiece:
    """
    提示词拼图块

    Attributes:
        name: 唯一标识
        content: 静态内容
        template: Jinja 模板（动态渲染）
        stability: 稳定性级别（static/daily/session/dynamic）
        priority: 拼接顺序（小在前）
        enabled: 开关
        role: 消息角色（默认 auto → static=system, 其余=user）
    """

    name: str
    content: str = ""
    template: str = ""
    stability: Stability = "dynamic"
    priority: int = 100
    enabled: bool = True
    role: str = ""  # 空 = auto 推断

    @property
    def effective_role(self) -> str:
        """推断消息角色：static→system，其余→user"""
        return self.role or ("system" if self.stability == "static" else "user")

    def render(self, context: dict = None) -> str:
        """
        渲染内容

        Args:
            context: 模板上下文

        Returns:
            渲染后的字符串
        """
        if self.template:
            try:
                from jinja2 import Template
                return Template(self.template).render(**(context or {}))
            except ImportError:
                result = self.template
                if context:
                    for key, value in context.items():
                        result = result.replace("{{ " + key + " }}", str(value))
                return result

        return self.content

    def __repr__(self) -> str:
        return (
            f"PromptPiece(name={self.name!r}, stability={self.stability}, "
            f"priority={self.priority}, enabled={self.enabled})"
        )
