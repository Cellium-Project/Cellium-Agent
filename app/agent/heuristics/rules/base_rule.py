# -*- coding: utf-8 -*-
"""
规则基类

所有启发式规则的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

from app.agent.heuristics.types import (
    DecisionPoint,
    RulePriority,
    RuleEvaluationResult,
    EvaluationContext,
    DerivedFeatures,
)


class BaseRule(ABC):
    """规则基类"""

    id: str                      # 规则唯一标识
    name: str                    # 规则名称
    description: str             # 规则描述
    priority: RulePriority       # 优先级
    decision_point: DecisionPoint # 决策点
    enabled: bool = True         # 是否启用

    def __init__(self, config: Dict[str, Any] = None):
        """初始化规则，可从配置覆盖默认参数"""
        if config:
            self._apply_config(config)

    def _apply_config(self, config: Dict[str, Any]):
        """应用配置"""
        for key, value in config.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @abstractmethod
    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        """
        评估规则

        Args:
            context: 评估上下文（原始数据）
            features: 派生特征

        Returns:
            RuleEvaluationResult: 评估结果
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id} priority={self.priority.name}>"
