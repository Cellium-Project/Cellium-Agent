# -*- coding: utf-8 -*-
"""
启发式优化模块 - 入口

导出公共 API：
  - get_heuristic_engine(): 获取引擎单例
  - HeuristicEngine: 启发式引擎
  - EvaluationContext: 评估上下文
  - DerivedFeatures: 派生特征
  - DecisionPoint: 决策点枚举
  - DecisionAction: 决策动作枚举
"""

from app.agent.heuristics.types import (
    DecisionPoint,
    DecisionAction,
    RulePriority,
    Decision,
    FusedDecision,
    EvaluationContext,
    DerivedFeatures,
    RuleEvaluationResult,
)
from app.agent.heuristics.engine import HeuristicEngine, get_heuristic_engine
from app.agent.heuristics.integration import AgentLoopIntegration

__all__ = [
    # 类型
    "DecisionPoint",
    "DecisionAction",
    "RulePriority",
    "Decision",
    "FusedDecision",
    "EvaluationContext",
    "DerivedFeatures",
    "RuleEvaluationResult",
    # 引擎
    "HeuristicEngine",
    "get_heuristic_engine",
    # 集成
    "AgentLoopIntegration",
]
