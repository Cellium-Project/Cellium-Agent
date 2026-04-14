# -*- coding: utf-8 -*-
"""
迭代终止规则

包含：
  - MaxIterationRule: 最大迭代限制
  - TokenBudgetRule: Token 预算限制
  - EmptyResultChainRule: 空结果链检测
  - NoProgressRule: 无进展检测
"""

import logging
from typing import Any, Dict, List

from app.agent.heuristics.rules.base_rule import BaseRule
from app.agent.heuristics.types import (
    DecisionPoint,
    DecisionAction,
    RulePriority,
    RuleEvaluationResult,
    EvaluationContext,
    DerivedFeatures,
)

logger = logging.getLogger(__name__)


class MaxIterationRule(BaseRule):
    """
    最大迭代限制规则

    当迭代次数接近或达到最大值时触发
    """
    id = "term-001"
    name = "Max Iteration Protection"
    description = "迭代次数接近最大值时警告或终止"
    priority = RulePriority.CRITICAL
    decision_point = DecisionPoint.ITERATION_TERMINATION

    warn_threshold: float = 0.8
    stop_threshold: float = 0.95

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        from app.agent.heuristics.engine import get_heuristic_engine
        engine = get_heuristic_engine()
        stop_threshold = engine.config.get_threshold("max_iterations_ratio", self.stop_threshold)
        warn_threshold = stop_threshold * 0.85

        ratio = context.iteration / max(context.max_iterations, 1)

        if ratio >= stop_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.95,
                reason=f"达到最大迭代次数限制 ({context.iteration}/{context.max_iterations})",
                metadata={"ratio": ratio, "iteration": context.iteration},
            )

        # 达到警告阈值
        if ratio >= warn_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.WARN,
                score=0.7,
                reason=f"接近最大迭代次数 ({context.iteration}/{context.max_iterations}，{ratio:.0%})",
                metadata={"ratio": ratio, "iteration": context.iteration},
            )

        return RuleEvaluationResult.not_matched()


class TokenBudgetRule(BaseRule):
    """
    Token 预算限制规则 - 多段判断

    分段阈值（基于 ratio = tokens_used / token_budget）：
      - >= stop_ratio: STOP（强制终止）
      - >= redirect_ratio: REDIRECT（引导换方向）
      - >= warn_ratio: WARN（警告）
      - >= compress_ratio: COMPRESS（建议压缩上下文）

    默认阈值（可配置）：
      - compress_ratio: 0.50
      - warn_ratio: 0.70
      - redirect_ratio: 0.85
      - stop_ratio: 0.95
    """
    id = "term-002"
    name = "Token Budget Protection"
    description = "Token 使用量分段判断：压缩 → 警告 → 换方向 → 终止"
    priority = RulePriority.CRITICAL
    decision_point = DecisionPoint.ITERATION_TERMINATION

    compress_threshold: float = 0.50
    warn_threshold: float = 0.70
    redirect_threshold: float = 0.85
    stop_threshold: float = 0.95

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        from app.agent.heuristics.engine import get_heuristic_engine
        engine = get_heuristic_engine()
        stop_threshold = engine.config.get_threshold("token_budget_stop_ratio", self.stop_threshold)
        redirect_threshold = engine.config.get_threshold("token_budget_redirect_ratio", self.redirect_threshold)
        warn_threshold = engine.config.get_threshold("token_budget_warn_ratio", self.warn_threshold)
        compress_threshold = engine.config.get_threshold("token_budget_compress_ratio", self.compress_threshold)

        if context.token_budget <= 0:
            return RuleEvaluationResult.not_matched()

        ratio = context.total_tokens_used / context.token_budget

        if ratio >= stop_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.95,
                reason=f"Token 预算即将耗尽 ({context.total_tokens_used}/{context.token_budget})",
                metadata={"ratio": ratio, "tokens_used": context.total_tokens_used, "stage": "stop"},
            )

        elif ratio >= redirect_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.REDIRECT,
                score=0.80,
                reason=f"Token 使用量过高 ({ratio:.0%})，建议换方向",
                metadata={"ratio": ratio, "tokens_used": context.total_tokens_used, "stage": "redirect"},
            )

        elif ratio >= warn_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.WARN,
                score=0.60,
                reason=f"Token 使用量较高 ({ratio:.0%})",
                metadata={"ratio": ratio, "tokens_used": context.total_tokens_used, "stage": "warn"},
            )

        elif ratio >= compress_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.RECOMMEND,
                score=0.50,
                reason=f"Token 使用量过半 ({ratio:.0%})，建议压缩上下文",
                metadata={"ratio": ratio, "tokens_used": context.total_tokens_used, "stage": "compress"},
            )

        return RuleEvaluationResult.not_matched()


class EmptyResultChainRule(BaseRule):
    """
    空结果链检测规则
    """
    id = "term-003"
    name = "Empty Result Chain Detection"
    description = "连续多次工具返回空结果或错误，引导换方向"
    priority = RulePriority.HIGH
    decision_point = DecisionPoint.ITERATION_TERMINATION

    threshold: int = 3
    stop_threshold: int = 6

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        from app.agent.heuristics.engine import get_heuristic_engine
        engine = get_heuristic_engine()
        threshold = engine.config.get_threshold("repetition_threshold", self.threshold)
        stop_threshold = threshold * 2

        calls = context.recent_tool_calls[-stop_threshold * 2:]

        if len(calls) < threshold:
            return RuleEvaluationResult.not_matched()

        consecutive_empty = 0
        last_error_info = None

        for call in reversed(calls):
            result = call.get("result", {})
            if self._is_empty_or_error(result):
                consecutive_empty += 1
                last_error_info = self._extract_error_info(result, call)
            else:
                break

        if consecutive_empty >= stop_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.85,
                reason=f"连续 {consecutive_empty} 次工具调用失败，已尝试多方向但无进展",
                metadata={
                    "consecutive_empty": consecutive_empty,
                    "last_error": last_error_info,
                    "redirect_suggestions": self._generate_suggestions(context, last_error_info),
                },
            )

        if consecutive_empty >= threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.REDIRECT,
                score=0.7,
                reason=f"连续 {consecutive_empty} 次工具调用返回空结果或错误",
                metadata={
                    "consecutive_empty": consecutive_empty,
                    "last_error": last_error_info,
                    "redirect_suggestions": self._generate_suggestions(context, last_error_info),
                },
            )

        return RuleEvaluationResult.not_matched()

    def _is_empty_or_error(self, result: Any) -> bool:
        """判断结果是否为空或错误"""
        if result is None:
            return True
        if isinstance(result, dict):
            if result.get("error"):
                return True
            if result.get("success") is True:
                return False
            output = result.get("output", "")
            if isinstance(output, str) and output.strip():
                return False
            content = result.get("content") or result.get("data") or result.get("text")
            if content and str(content).strip():
                return False
            return True
        return False

    def _extract_error_info(self, result: Any, call: Dict) -> Dict:
        """提取错误信息用于生成建议"""
        tool_name = call.get("name", "unknown")
        args = call.get("args", {})

        error_info = {"tool": tool_name, "args": args}

        if isinstance(result, dict):
            error_info["error"] = result.get("error", "")
            error_info["output"] = str(result.get("output", ""))[:200]

        return error_info

    def _generate_suggestions(self, context: EvaluationContext, error_info: Dict) -> List[str]:
        """生成换方向建议"""
        suggestions = []
        tool_name = error_info.get("tool", "") if error_info else ""
        available_tools = context.available_tools or []

        if tool_name == "shell":
            suggestions.append("考虑使用 file 工具直接读取文件，而非 shell 命令")
            suggestions.append("检查命令语法是否正确，路径是否存在")
            if "sqlite" in str(error_info.get("args", {})).lower():
                suggestions.append("尝试使用 Python 脚本操作数据库，而非 sqlite3 命令行")

        elif tool_name == "file":
            suggestions.append("检查文件路径是否正确")
            suggestions.append("尝试使用 shell 工具列出目录确认文件存在")

        elif tool_name == "memory":
            suggestions.append("尝试用不同的关键词搜索")
            suggestions.append("使用 memory list 查看当前记忆概况")

        if "memory" in available_tools and tool_name != "memory":
            suggestions.append("尝试搜索相关记忆，可能有历史解决方案")

        if len(suggestions) == 0:
            suggestions.append("尝试换一个工具或方法")
            suggestions.append("回顾之前的步骤，确认当前方向是否正确")

        return suggestions[:3]


class NoProgressRule(BaseRule):
    """
    无进展检测规则
    """
    id = "term-004"
    name = "No Progress Detection"
    description = "检测长时间无进展，引导换方向"
    priority = RulePriority.HIGH
    decision_point = DecisionPoint.ITERATION_TERMINATION

    stuck_threshold: int = 3
    trend_threshold: float = -0.1
    redirect_threshold: int = 5   

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        from app.agent.heuristics.engine import get_heuristic_engine
        engine = get_heuristic_engine()
        self.stuck_threshold = engine.config.get_threshold("stuck_iterations", self.stuck_threshold)

        if features.is_plateau:
            if features.stuck_iterations >= self.stuck_threshold * 3:
                return RuleEvaluationResult(
                    matched=True,
                    action=DecisionAction.REDIRECT,
                    score=0.6,
                    reason=f"高原期较长 ({features.stuck_iterations} 次)，建议尝试不同方法",
                    metadata={
                        "stuck_iterations": features.stuck_iterations,
                        "is_plateau": True,
                        "redirect_suggestions": [
                            "当前方法可能陷入瓶颈，尝试换个角度思考",
                            "回顾任务目标，确认当前方向是否正确",
                            "尝试使用不同工具组合",
                        ],
                    },
                )
            # 高原期初期 → 不干预
            return RuleEvaluationResult.not_matched()

        if (features.stuck_iterations >= self.redirect_threshold
            and features.stuck_iterations < self.stuck_threshold * 3):
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.REDIRECT,
                score=0.65,
                reason=f"停滞中 ({features.stuck_iterations} 次)，建议换方向尝试",
                metadata={
                    "stuck_iterations": features.stuck_iterations,
                    "progress_trend": features.progress_trend,
                    "redirect_suggestions": self._generate_redirect_suggestions(context, features),
                },
            )

        # 真停滞检测
        # 条件1: 停滞 + 趋势向下 + 置信度高 → STOP
        if (features.stuck_iterations >= self.stuck_threshold
            and features.progress_trend < self.trend_threshold
            and features.trend_confidence > 0.5):
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.9,
                reason=f"真停滞：{features.stuck_iterations} 次无进展，趋势={features.progress_trend:.2f}，置信度={features.trend_confidence:.2f}",
                metadata={
                    "stuck_iterations": features.stuck_iterations,
                    "progress_trend": features.progress_trend,
                    "trend_confidence": features.trend_confidence,
                },
            )

        # 条件2: 停滞但趋势不确定 → WARN
        if features.stuck_iterations >= self.stuck_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.WARN,
                score=0.5,
                reason=f"停滞中 ({features.stuck_iterations} 次)，但趋势不确定",
                metadata={"stuck_iterations": features.stuck_iterations},
            )

        # 条件3: 长时间停滞 → 强制 STOP
        if features.stuck_iterations >= self.stuck_threshold * 4:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.95,
                reason=f"长时间停滞 ({features.stuck_iterations} 次)，强制终止",
                metadata={"stuck_iterations": features.stuck_iterations},
            )

        return RuleEvaluationResult.not_matched()

    def _generate_redirect_suggestions(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> List[str]:
        """生成换方向建议"""
        suggestions = []

        if features.dominant_tool_ratio > 0.6:
            suggestions.append(f"过度依赖单一工具，尝试使用其他可用工具")

        if features.tool_diversity_score < 0.3:
            suggestions.append("尝试探索更多工具组合")

        if features.error_rate > 0.3:
            suggestions.append("错误率较高，建议先检查之前的错误原因")

        suggestions.append("回顾任务目标，确认当前方向是否正确")
        suggestions.append("尝试将任务分解为更小的步骤")

        return suggestions[:3]
