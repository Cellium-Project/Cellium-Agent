# -*- coding: utf-8 -*-
"""
规则模块入口
"""

from app.agent.heuristics.rules.base_rule import BaseRule
from app.agent.heuristics.rules.termination import (
    MaxIterationRule,
    TokenBudgetRule,
    EmptyResultChainRule,
    NoProgressRule,
)
from app.agent.heuristics.rules.loop_detection import (
    SameToolRepetitionRule,
    PatternLoopRule,
    ParameterSimilarityRule,
)

__all__ = [
    "BaseRule",
    # 迭代终止规则
    "MaxIterationRule",
    "TokenBudgetRule",
    "EmptyResultChainRule",
    "NoProgressRule",
    # 循环检测规则
    "SameToolRepetitionRule",
    "PatternLoopRule",
    "ParameterSimilarityRule",
]
