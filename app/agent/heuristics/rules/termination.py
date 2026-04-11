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

    # 阈值配置
    warn_threshold: float = 0.8    # 警告阈值（比例）
    stop_threshold: float = 0.95   # 停止阈值（比例）

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        ratio = context.iteration / max(context.max_iterations, 1)

        # 达到停止阈值
        if ratio >= self.stop_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.95,
                reason=f"达到最大迭代次数限制 ({context.iteration}/{context.max_iterations})",
                metadata={"ratio": ratio, "iteration": context.iteration},
            )

        # 达到警告阈值
        if ratio >= self.warn_threshold:
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
    Token 预算限制规则

    当 Token 使用量接近预算时触发
    """
    id = "term-002"
    name = "Token Budget Protection"
    description = "Token 使用量接近预算时警告或终止"
    priority = RulePriority.CRITICAL
    decision_point = DecisionPoint.ITERATION_TERMINATION

    warn_threshold: float = 0.8
    stop_threshold: float = 0.95

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        if context.token_budget <= 0:
            return RuleEvaluationResult.not_matched()

        ratio = context.total_tokens_used / context.token_budget

        # 达到停止阈值
        if ratio >= self.stop_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.STOP,
                score=0.95,
                reason=f"Token 预算即将耗尽 ({context.total_tokens_used}/{context.token_budget})",
                metadata={"ratio": ratio, "tokens_used": context.total_tokens_used},
            )

        # 达到警告阈值
        if ratio >= self.warn_threshold:
            return RuleEvaluationResult(
                matched=True,
                action=DecisionAction.WARN,
                score=0.7,
                reason=f"Token 使用量较高 ({ratio:.0%})",
                metadata={"ratio": ratio, "tokens_used": context.total_tokens_used},
            )

        return RuleEvaluationResult.not_matched()


class EmptyResultChainRule(BaseRule):
    """
    空结果链检测规则（★ 改进：引导换方向而非直接终止）

    改进点：
    1. 连续空结果 → 先 REDIRECT（引导换方向）
    2. 多次 REDIRECT 无效后才考虑 STOP
    3. 提供具体的换方向建议
    """
    id = "term-003"
    name = "Empty Result Chain Detection"
    description = "连续多次工具返回空结果或错误，引导换方向"
    priority = RulePriority.HIGH
    decision_point = DecisionPoint.ITERATION_TERMINATION

    threshold: int = 3           # 连续空结果阈值（触发 REDIRECT）
    stop_threshold: int = 6      # 累积空结果阈值（触发 STOP）

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        calls = context.recent_tool_calls[-self.stop_threshold * 2:]  # 多看一些

        if len(calls) < self.threshold:
            return RuleEvaluationResult.not_matched()

        # 检查最近的调用
        consecutive_empty = 0
        last_error_info = None

        for call in reversed(calls):
            result = call.get("result", {})
            if self._is_empty_or_error(result):
                consecutive_empty += 1
                last_error_info = self._extract_error_info(result, call)
            else:
                break

        if consecutive_empty >= self.stop_threshold:
            # ★ 多次尝试无效 → STOP（但带建议）
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

        if consecutive_empty >= self.threshold:
            # ★ 初次检测到问题 → REDIRECT（引导换方向）
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
            # 优先检查 error 字段
            if result.get("error"):
                return True
            # 检查 success 字段（file 工具返回）
            if result.get("success") is True:
                return False
            # 检查是否有实质性内容
            output = result.get("output", "")
            if isinstance(output, str) and output.strip():
                return False
            # 检查 data/content/text 字段
            content = result.get("content") or result.get("data") or result.get("text")
            if content and str(content).strip():
                return False
            # 其他情况视为空
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

        # 根据失败的工具体现建议
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

        # 通用建议
        if "memory" in available_tools and tool_name != "memory":
            suggestions.append("尝试搜索相关记忆，可能有历史解决方案")

        if len(suggestions) == 0:
            suggestions.append("尝试换一个工具或方法")
            suggestions.append("回顾之前的步骤，确认当前方向是否正确")

        return suggestions[:3]  # 最多 3 条建议


class NoProgressRule(BaseRule):
    """
    无进展检测规则（★ 改进：区分 plateau vs 真停滞 + 引导换方向）

    改进点：
    1. 不把 plateau（高原期）当作失败
    2. 高原期：趋势平 + 停滞短 + 质量可 → 继续观察
    3. 真停滞：趋势负 + 停滞长 + 质量差 → REDIRECT 或 STOP
    4. ★ 提供换方向建议
    """
    id = "term-004"
    name = "No Progress Detection"
    description = "检测长时间无进展，引导换方向"
    priority = RulePriority.HIGH
    decision_point = DecisionPoint.ITERATION_TERMINATION

    stuck_threshold: int = 3
    trend_threshold: float = -0.1
    redirect_threshold: int = 5    # ★ 触发 REDIRECT 的停滞次数

    def evaluate(
        self,
        context: EvaluationContext,
        features: DerivedFeatures,
    ) -> RuleEvaluationResult:
        # ★ 从全局配置获取动态阈值（由 Policy 注入）
        from app.agent.heuristics.engine import get_heuristic_engine
        engine = get_heuristic_engine()
        self.stuck_threshold = engine.config.get_threshold("stuck_iterations", self.stuck_threshold)

        # ★ 首先检查是否是 plateau
        if features.is_plateau:
            # 高原期：给更多时间
            if features.stuck_iterations >= self.stuck_threshold * 3:
                # 高原期很久 → REDIRECT（建议换方法）
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

        # ★ 停滞中期 → REDIRECT（引导换方向）
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

        # 根据工具使用情况建议
        if features.dominant_tool_ratio > 0.6:
            suggestions.append(f"过度依赖单一工具，尝试使用其他可用工具")

        if features.tool_diversity_score < 0.3:
            suggestions.append("尝试探索更多工具组合")

        # 根据错误率建议
        if features.error_rate > 0.3:
            suggestions.append("错误率较高，建议先检查之前的错误原因")

        # 通用建议
        suggestions.append("回顾任务目标，确认当前方向是否正确")
        suggestions.append("尝试将任务分解为更小的步骤")

        return suggestions[:3]
