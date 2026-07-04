# -*- coding: utf-8 -*-
"""
Prompt 模块 — 提示词模块化构建

设计：stability 分层（static → daily → session → dynamic）
"""

from app.agent.prompt.piece import PromptPiece, Stability
from app.agent.prompt.builder import PromptBuilder
from app.agent.prompt.diff import PromptDiffTracker, CacheStats, DiffReport
from app.agent.prompt.pieces import create_default_builder

__all__ = [
    "PromptPiece",
    "Stability",
    "PromptBuilder",
    "PromptDiffTracker",
    "CacheStats",
    "DiffReport",
    "create_default_builder",
]
