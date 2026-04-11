# -*- coding: utf-8 -*-
"""
PromptBuilder - 提示词构建器
"""

import logging
from typing import Dict, List, Optional

from app.agent.prompt.piece import PromptPiece

logger = logging.getLogger(__name__)


class PromptBuilder:
    """
    提示词构建器

    功能：
      - register: 注册拼图块
      - enable: 启用/禁用拼图块
      - inject: 动态注入一次性内容
      - build: 构建完整提示词
    """

    def __init__(self, separator: str = "\n\n---\n\n"):
        self._pieces: Dict[str, PromptPiece] = {}
        self._separator = separator
        self._dynamic_counter = 0  # 动态注入计数器

    def register(self, piece: PromptPiece) -> None:
        """
        注册拼图块

        Args:
            piece: PromptPiece 实例
        """
        self._pieces[piece.name] = piece
        logger.debug("[PromptBuilder] 注册拼图块: %s", piece.name)

    def unregister(self, name: str) -> bool:
        """
        注销拼图块

        Args:
            name: 拼图块名称

        Returns:
            是否成功注销
        """
        if name in self._pieces:
            del self._pieces[name]
            logger.debug("[PromptBuilder] 注销拼图块: %s", name)
            return True
        return False

    def enable(self, name: str, enabled: bool = True) -> bool:
        """
        启用/禁用拼图块

        Args:
            name: 拼图块名称
            enabled: 是否启用

        Returns:
            是否成功设置
        """
        if name in self._pieces:
            self._pieces[name].enabled = enabled
            logger.debug("[PromptBuilder] %s 拼图块: %s", "启用" if enabled else "禁用", name)
            return True
        return False

    def inject(self, content: str, name: Optional[str] = None, priority: int = 200, is_base: bool = False) -> str:
        """
        动态注入一次性内容

        Args:
            content: 注入内容
            name: 拼图块名称（可选，自动生成）
            priority: 优先级
            is_base: 是否基础层

        Returns:
            注入的拼图块名称
        """
        self._dynamic_counter += 1
        piece_name = name or f"_dynamic_{self._dynamic_counter}"

        self._pieces[piece_name] = PromptPiece(
            name=piece_name,
            content=content,
            priority=priority,
            enabled=True,
            is_base=is_base,
        )

        logger.debug("[PromptBuilder] 动态注入: %s (priority=%d)", piece_name, priority)
        return piece_name

    def clear_dynamic(self) -> int:
        """
        清除所有动态注入的拼图块

        Returns:
            清除的数量
        """
        dynamic_names = [
            name for name in self._pieces
            if name.startswith("_dynamic_") or name.startswith("_control_")
        ]
        for name in dynamic_names:
            del self._pieces[name]
        if dynamic_names:
            logger.debug("[PromptBuilder] 清除动态拼图块: %d 个", len(dynamic_names))
        return len(dynamic_names)

    def get_piece(self, name: str) -> Optional[PromptPiece]:
        """获取拼图块"""
        return self._pieces.get(name)

    def list_pieces(self, enabled_only: bool = False) -> List[PromptPiece]:
        """
        列出所有拼图块

        Args:
            enabled_only: 是否只返回启用的

        Returns:
            拼图块列表
        """
        pieces = list(self._pieces.values())
        if enabled_only:
            pieces = [p for p in pieces if p.enabled]
        return sorted(pieces, key=lambda p: p.priority)

    def build(self, context: dict = None) -> str:
        """
        构建完整提示词

        Args:
            context: 模板渲染上下文

        Returns:
            完整提示词字符串
        """
        context = context or {}

        base_parts = []
        dynamic_parts = []

        for piece in self.list_pieces(enabled_only=True):
            try:
                content = piece.render(context)
                if content and content.strip():
                    if piece.is_base:
                        base_parts.append(content.strip())
                    else:
                        dynamic_parts.append(content.strip())
            except Exception as e:
                logger.warning("[PromptBuilder] 渲染拼图块失败 %s: %s", piece.name, e)

        # 拼接：基础层 + 动态层
        result = ""
        if base_parts:
            result = self._separator.join(base_parts)
        if dynamic_parts:
            if result:
                result += self._separator
            result += self._separator.join(dynamic_parts)

        return result

    def reset(self) -> None:
        self.clear_dynamic()

        for piece in self._pieces.values():
            piece.enabled = True

    def __repr__(self) -> str:
        base_count = sum(1 for p in self._pieces.values() if p.is_base)
        dynamic_count = len(self._pieces) - base_count
        return f"PromptBuilder(base={base_count}, dynamic={dynamic_count})"
