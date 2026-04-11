# -*- coding: utf-8 -*-
"""
PromptPiece - 提示词拼图块
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PromptPiece:
    """
    提示词拼图块

    Attributes:
        name: 唯一标识
        content: 静态内容
        template: Jinja 模板（动态渲染）
        priority: 拼接顺序（小在前）
        enabled: 开关
        is_base: 是否基础层（基础层始终存在）
    """

    name: str
    content: str = ""
    template: str = ""
    priority: int = 100
    enabled: bool = True
    is_base: bool = False

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
        return f"PromptPiece(name={self.name!r}, priority={self.priority}, enabled={self.enabled}, is_base={self.is_base})"
