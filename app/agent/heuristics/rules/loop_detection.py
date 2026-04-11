# -*- coding: utf-8 -*-
"""
循环检测规则

包含：
  - SameToolRepetitionRule: 相同工具重复检测
  - PatternLoopRule: 模式循环检测
  - ParameterSimilarityRule: 参数相似度检测
"""

import logging
from typing import Dict

from app.agent.heuristics.rules.base_rule import BaseRule
from app.agent.heuristics.types import (
    DecisionPoint,
    DecisionAction,
    RulePriority,
    RuleEvaluationResult,
    EvaluationContext,
    DerivedFeatures,
)
from app.agent.heuristics.features import get_call_signature

logger = logging.getLogger(__name__)


class SameToolRepetitionRule(BaseRule):
    """
    相同签名重复检测规则（★ 已改进：工具+命令级别检测）

    改进点：
    1. 使用签名（工具+命令）而非仅工具名检测重复
    2. 重复不等于失败，可能在逐步解决问题
    3. 重复 + 无进展 → STOP
    4. 重复 + 有进展 → 仅记录，不警告
    """
    id = "loop-001"
    name = "Same Signature Repetition"
    description = "检测相同签名（工具+命令）的重复调用"
    priority = RulePriority.HIGH
    decision_point = DecisionPoint.LOOP_DETECTION

    threshold: int = 3

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        # ★ 从全局配置获取动态阈值（由 Policy 注入）
        from app.agent.heuristics.engine import get_heuristic_engine
        engine = get_heuristic_engine()
        self.threshold = engine.config.get_threshold("repetition_threshold", self.threshold)

        recent = context.recent_tool_calls[-self.threshold:]

        if len(recent) < self.threshold:
            return RuleEvaluationResult.not_matched()

        # 检查是否全部是同一签名（工具+命令）
        signatures = [get_call_signature(c) for c in recent]
        if not all(sig == signatures[0] for sig in signatures):
            return RuleEvaluationResult.not_matched()

        signature = signatures[0]

        # ★ 关键改进：结合进展判断
        # 重复 + 无进展 → STOP
        if features.stuck_iterations >= 2 and features.progress_trend < 0:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.9,
                reason=f"签名 '{signature}' 重复 {self.threshold} 次且无进展",
                metadata={"signature": signature, "stuck": features.stuck_iterations},
            )

        # 重复 + 有进展 → WARN（轻量警告）
        if features.is_making_progress:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.WARN,
                score=0.4,  # 低置信度
                reason=f"签名 '{signature}' 重复但正在进步 (trend={features.progress_trend:.2f})",
                metadata={"signature": signature, "progress_trend": features.progress_trend},
            )

        # 重复 + 趋势不明 → WARN
        return RuleEvaluationResult(
            matched=True,
            action=DecisionAction.WARN,
            score=0.6,
            reason=f"签名 '{signature}' 已连续调用 {self.threshold} 次",
            metadata={"signature": signature},
        )


class PatternLoopRule(BaseRule):
    """
    模式循环检测规则（★ 必改-3：加结果质量门控）

    改进点：
    1. 检测到循环不直接 STOP
    2. ★ 必须检查结果质量（Gating）
    3. 循环 + 结果质量差 + 无进展 → STOP
    4. 循环 + 结果质量好 → 继续观察
    """
    id = "loop-002"
    name = "Pattern Loop Detection"
    description = "检测 ABAB 等循环调用模式"
    priority = RulePriority.CRITICAL
    decision_point = DecisionPoint.LOOP_DETECTION

    # 结果质量阈值
    QUALITY_THRESHOLD: float = 0.4

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        if features.pattern_detected != "cycle":
            return RuleEvaluationResult.not_matched()

        # ★ 结果质量门控
        # 循环 + 结果质量差 → 可能是真的问题
        if features.result_quality_score < self.QUALITY_THRESHOLD:
            # 质量差 + 无进展 → STOP
            if features.progress_trend < 0 or features.stuck_iterations >= 2:
                return RuleEvaluationResult(
                    matched=True,
                    action=DecisionAction.STOP,
                    score=0.95,
                    reason=f"循环且结果质量差 (quality={features.result_quality_score:.2f})，无进展",
                    metadata={
                        "pattern": features.pattern_detected,
                        "cycle_length": features.pattern_cycle_length,
                        "result_quality": features.result_quality_score,
                    },
                )

            # 质量差 + plateau → WARN（可能还在积累）
            if features.is_plateau:
                return RuleEvaluationResult(
                    matched=True,
                    action=DecisionAction.WARN,
                    score=0.6,
                    reason=f"循环且结果质量偏低，但在高原期，继续观察",
                    metadata={
                        "pattern": features.pattern_detected,
                        "is_plateau": True,
                    },
                )

        # 结果质量尚可 + 有进展 → WARN（轻量）
        if features.is_making_progress:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.WARN,
                score=0.4,
                reason=f"检测到循环但结果质量可接受 (quality={features.result_quality_score:.2f})",
                metadata={
                    "pattern": features.pattern_detected,
                    "cycle_length": features.pattern_cycle_length,
                    "result_quality": features.result_quality_score,
                },
            )

        # 结果质量尚可 + 无进展 → WARN（观察）
        return RuleEvaluationResult(
            matched=True,
            action=DecisionAction.WARN,
            score=0.5,
            reason=f"检测到循环模式 (周期={features.pattern_cycle_length})，继续观察",
            metadata={
                "pattern": features.pattern_detected,
                "cycle_length": features.pattern_cycle_length,
            },
        )


class ParameterSimilarityRule(BaseRule):
    """
    参数相似度检测规则

    检测连续调用参数高度相似的情况
    """
    id = "loop-003"
    name = "Parameter Similarity Detection"
    description = "检测连续调用参数高度相似"
    priority = RulePriority.MEDIUM
    decision_point = DecisionPoint.LOOP_DETECTION

    similarity_threshold: float = 0.8
    window_size: int = 3

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        calls = context.recent_tool_calls[-self.window_size * 2:]

        if len(calls) < self.window_size:
            return RuleEvaluationResult.not_matched()

        # 计算最近几次调用的参数相似度
        similarities = []
        for i in range(len(calls) - 1):
            args1 = calls[i].get("arguments", {})
            args2 = calls[i + 1].get("arguments", {})
            if args1 and args2:
                sim = self._calc_similarity(args1, args2)
                similarities.append(sim)

        if not similarities:
            return RuleEvaluationResult.not_matched()

        avg_similarity = sum(similarities) / len(similarities)

        if avg_similarity >= self.similarity_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.WARN,
                score=0.6,
                reason=f"连续调用参数相似度较高 ({avg_similarity:.0%})",
                metadata={"avg_similarity": avg_similarity},
            )

        return RuleEvaluationResult.not_matched()

    def _calc_similarity(self, args1: Dict, args2: Dict) -> float:
        """计算两个参数字典的相似度"""
        if not args1 or not args2:
            return 0.0

        # 收集所有键
        all_keys = set(args1.keys()) | set(args2.keys())
        if not all_keys:
            return 0.0

        # 计算匹配分数
        matches = 0
        for key in all_keys:
            v1 = args1.get(key)
            v2 = args2.get(key)

            if v1 == v2:
                matches += 1
            elif isinstance(v1, str) and isinstance(v2, str):
                # 字符串部分匹配
                if v1 in v2 or v2 in v1:
                    matches += 0.5
            elif isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                # 数值接近匹配
                if abs(v1 - v2) < max(abs(v1), abs(v2), 1) * 0.1:
                    matches += 0.7

        return matches / len(all_keys)
