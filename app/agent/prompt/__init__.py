# -*- coding: utf-8 -*-
"""
Prompt 模块 - 提示词模块化拼接

设计：基础层 + 动态层
  - 基础层：始终存在（identity, constraints）
  - 动态层：按需拼接（tools_guide, session_context, long_memory, etc.）
"""

from app.agent.prompt.piece import PromptPiece
from app.agent.prompt.builder import PromptBuilder

__all__ = [
    "PromptPiece",
    "PromptBuilder",
]
