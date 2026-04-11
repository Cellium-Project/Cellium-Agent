# -*- coding: utf-8 -*-
"""
工具评分器
"""

import logging
from dataclasses import dataclass
from typing import List, Set

from app.agent.heuristics.types import EvaluationContext, DerivedFeatures

logger = logging.getLogger(__name__)


@dataclass
class ToolScore:
    """工具评分结果"""
    tool_name: str
    score: float       # 0-1
    reason: str
    confidence: float

    def __repr__(self):
        return f"ToolScore({self.tool_name}, {self.score:.2f}, {self.reason[:30]}...)"


class ToolScorer:
    """工具评分器"""

    def __init__(self, exploration_boost: float = 0.4):
        """
        Args:
            exploration_boost: exploration 加成基础值（必改-6：翻倍到 0.4）
        """
        self.exploration_boost = exploration_boost
        self._used_tools: Set[str] = set()

    def reset(self):
        """重置状态"""
        self._used_tools = set()

    def score_tools(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
        available_tools: List[str],
    ) -> List[ToolScore]:
        """
        为所有可用工具评分

        Args:
            context: 评估上下文
            features: 派生特征
            available_tools: 可用工具列表

        Returns:
            排序后的工具评分列表
        """
        scores = []

        for call in context.tool_call_history:
            tool_name = call.get("tool_name", call.get("tool", "unknown"))
            self._used_tools.add(tool_name)

        for tool in available_tools:
            intent_score = self._match_intent(context.user_input, tool)

            history_penalty = self._calc_history_penalty(tool, context)

            diversity_bonus = self._calc_diversity_bonus(tool, context)

            #停滞时鼓励尝试未使用工具
            exploration_bonus = self._calc_exploration_bonus(
                tool, context, features
            )

            final = (
                intent_score * 0.5
                - history_penalty * 0.2
                + diversity_bonus * 0.1
                + exploration_bonus * 0.3 
            )

            scores.append(ToolScore(
                tool_name=tool,
                score=max(0, min(1, final)),
                reason=self._explain(intent_score, history_penalty, diversity_bonus, exploration_bonus),
                confidence=0.7 if intent_score > 0.5 else 0.4,
            ))

        return sorted(scores, key=lambda x: x.score, reverse=True)

    def _match_intent(self, user_input: str, tool_name: str) -> float:
        """
        计算工具与用户意图的匹配度

        简单实现：关键词匹配
        """
        input_lower = user_input.lower()

        intent_keywords = {
            "shell": ["执行", "命令", "运行", "run", "cmd", "shell", "bash", "终端"],
            "memory": ["记忆", "记住", "回忆", "memory", "保存", "搜索历史"],
            "file": ["文件", "读取", "写入", "创建", "删除", "file", "read", "write", "目录"],
            "web_search": ["搜索", "查找", "search", "google", "bing"],
            "web_fetch": ["获取", "下载", "fetch", "download", "网页"],
        }

        keywords = intent_keywords.get(tool_name, [tool_name.lower()])

        for kw in keywords:
            if kw in input_lower:
                return 0.8

        if tool_name.lower() in input_lower:
            return 0.6

        return 0.3 

    def _calc_history_penalty(self, tool: str, context: EvaluationContext) -> float:
        calls = context.tool_call_history
        if not calls:
            return 0.0

        tool_count = sum(
            1 for c in calls
            if c.get("tool_name", c.get("tool", "unknown")) == tool
        )

        penalty = tool_count / len(calls)
        return penalty

    def _calc_diversity_bonus(self, tool: str, context: EvaluationContext) -> float:
        """
        计算多样性加成

        未使用过的工具获得加成
        """
        if tool not in self._used_tools:
            return 0.2
        return 0.0

    def _calc_exploration_bonus(
        self,
        tool: str,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> float:
        if features.stuck_iterations < 1:
            return 0.0

        if tool not in self._used_tools:
            boost = self.exploration_boost * min(features.stuck_iterations / 2, 2.0)
            return min(boost, 0.8)  

        return 0.0

    def _explain(
        self,
        intent: float,
        history: float,
        diversity: float,
        exploration: float,
    ) -> str:
        """生成评分解释"""
        reasons = []
        if intent > 0.5:
            reasons.append("意图匹配")
        if history > 0.3:
            reasons.append(f"重复惩罚({history:.0%})")
        if diversity > 0:
            reasons.append("多样性加成")
        if exploration > 0:
            reasons.append(f"探索加成({exploration:.0%})")
        return "; ".join(reasons) if reasons else "基础分数"
