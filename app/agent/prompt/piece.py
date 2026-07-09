# -*- coding: utf-8 -*-
"""
PromptPiece - 提示词拼图块
"""

from dataclasses import dataclass, field
from typing import Optional, Literal, Callable


Stability = Literal["static", "daily", "session", "dynamic"]

# 稳定性说明:
#   static  — 永远不变（personality），对应 role: system
#   daily   — 每天变一次（日期, 环境信息），对应 role: user
#   session — 会话内稳定（长期记忆检索结果），对应 role: user
#   dynamic — 每次请求都变（runtime_status, 系统指令等），对应 role: user


@dataclass
class PromptPiece:
    """
    提示词拼图块

    Attributes:
        name: 唯一标识
        content: 静态内容（直接返回，不经过变量替换）
        template: 带 {{ var }} 占位符的模板文本
        condition: 可选条件函数，接收 context dict 返回 bool，
                   为 False 时该 piece 不渲染
        stability: 稳定性级别（static/daily/session/dynamic）
        priority: 拼接顺序（小在前）
        enabled: 开关
        role: 消息角色（默认 auto → static=system, 其余=user）
    """

    name: str
    content: str = ""
    template: str = ""
    condition: Optional[Callable[[dict], bool]] = None
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

        处理顺序:
          1. condition 条件过滤（返回空字符串）
          2. template 变量替换（{{ var }} → value）
          3. content 直接返回（无需替换）

        Args:
            context: 模板上下文

        Returns:
            渲染后的字符串
        """
        ctx = context or {}

        if self.condition is not None:
            try:
                if not self.condition(ctx):
                    return ""
            except Exception:
                pass

        if self.template:
            result = self.template
            for key, value in ctx.items():
                result = result.replace("{{ " + key + " }}", str(value))
                result = result.replace("{{" + key + "}}", str(value))
            return result

        return self.content

    def __repr__(self) -> str:
        return (
            f"PromptPiece(name={self.name!r}, stability={self.stability}, "
            f"priority={self.priority}, enabled={self.enabled})"
        )
