# -*- coding: utf-8 -*-
import logging
from typing import Dict, List, Optional, Tuple

from app.agent.heuristics.types import (
    DecisionPoint,
    DecisionAction,
    FusedDecision,
    EvaluationContext,
    DerivedFeatures,
    RuleEvaluationResult,
)
from app.agent.heuristics.config import HeuristicConfig
from app.agent.heuristics.features import FeatureExtractor
from app.agent.heuristics.trace import TraceRecorder
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

logger = logging.getLogger(__name__)


class RuleRegistry:
    def __init__(self):
        self._rules: Dict[str, BaseRule] = {}
        self._by_point: Dict[DecisionPoint, List[BaseRule]] = {
            point: [] for point in DecisionPoint
        }

    def register(self, rule: BaseRule):
        if rule.id in self._rules:
            logger.warning("[RuleRegistry] 规则已存在，覆盖: %s", rule.id)
        self._rules[rule.id] = rule
        self._by_point[rule.decision_point].append(rule)
        logger.debug("[RuleRegistry] 注册规则: %s (%s)", rule.id, rule.name)

    def unregister(self, rule_id: str):
        if rule_id in self._rules:
            rule = self._rules.pop(rule_id)
            self._by_point[rule.decision_point].remove(rule)

    def get(self, rule_id: str) -> Optional[BaseRule]:
        return self._rules.get(rule_id)

    def get_rules_for_point(self, point: DecisionPoint) -> List[BaseRule]:
        rules = [r for r in self._by_point[point] if r.enabled]
        return sorted(rules, key=lambda r: r.priority.value, reverse=True)

    def all_rules(self) -> List[BaseRule]:
        return list(self._rules.values())


class HeuristicEngine:
    _instance: "HeuristicEngine" = None

    def __init__(self, config: HeuristicConfig = None):
        self.config = config or HeuristicConfig.load()
        self.feature_extractor = FeatureExtractor(
            ema_alpha=self.config.get_threshold("ema_alpha", 0.3)
        )
        self.registry = RuleRegistry()

        trace_enabled = self.config.get_threshold("trace_enabled", False)
        trace_dir = self.config.get_threshold("trace_dir", None)
        self.trace_recorder = TraceRecorder(
            trace_dir=trace_dir,
            enabled=trace_enabled,
        )

        self._register_builtin_rules()

        self._last_decision: Optional[FusedDecision] = None

    @classmethod
    def get_instance(cls) -> "HeuristicEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reload_config(self):
        """重新加载配置（支持热重载）"""
        from app.agent.heuristics.config import HeuristicConfig
        self.config = HeuristicConfig.load()
        self.feature_extractor = FeatureExtractor(
            ema_alpha=self.config.get_threshold("ema_alpha", 0.3)
        )
        logger.info("[HeuristicEngine] 配置已热重载 | enabled=%s", self.config.enabled)

    def _register_builtin_rules(self):
        builtin_rules = [
            MaxIterationRule(),
            TokenBudgetRule(),
            EmptyResultChainRule(),
            NoProgressRule(),
            SameToolRepetitionRule(),
            PatternLoopRule(),
            ParameterSimilarityRule(),
        ]

        for rule in builtin_rules:
            rule_config = self.config.get_rule_config(rule.id)

            # 特殊处理：MaxIterationRule 完全由 agent.enforce_iteration_limit 控制
            if rule.id == "term-001":
                try:
                    from app.core.util.agent_config import get_config
                    enforce_limit = get_config().get("agent.enforce_iteration_limit", False)
                    if not enforce_limit:
                        logger.info(
                            "[HeuristicEngine] MaxIterationRule 已禁用 (agent.enforce_iteration_limit=false)"
                        )
                        continue
                    # enforce_limit=True 时强制注册，忽略 heuristics.yaml 中的设置
                    logger.info(
                        "[HeuristicEngine] MaxIterationRule 已启用 (agent.enforce_iteration_limit=true)"
                    )
                    # 应用配置中的参数
                    if rule_config.threshold is not None and hasattr(rule, "warn_threshold"):
                        rule.warn_threshold = rule_config.threshold
                    for key, value in rule_config.params.items():
                        if hasattr(rule, key):
                            setattr(rule, key, value)
                    self.registry.register(rule)
                    continue
                except Exception as e:
                    logger.debug("[HeuristicEngine] 检查 enforce_iteration_limit 失败: %s", e)

            if not rule_config.enabled:
                logger.info("[HeuristicEngine] 规则已禁用: %s", rule.id)
                continue

            if rule_config.threshold is not None and hasattr(rule, "threshold"):
                rule.threshold = rule_config.threshold

            for key, value in rule_config.params.items():
                if hasattr(rule, key):
                    setattr(rule, key, value)

            self.registry.register(rule)

    def evaluate(
        self,
        point: DecisionPoint,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> Tuple[List[Tuple[BaseRule, RuleEvaluationResult]], DerivedFeatures]:
        if features is None:
            features = self.feature_extractor.extract(context)

            logger.info(
                "[HeuristicEngine] 特征提取 | stuck=%d | trend=%.3f | progress=%.3f | "
                "tools=%d | diversity=%.2f | quality=%.2f | calls=%d",
                features.stuck_iterations,
                features.progress_trend,
                features.progress_score,
                features.unique_tools_used,
                features.tool_diversity_score,
                features.result_quality_score,
                len(context.tool_call_history),
            )

        results = []
        for rule in self.registry.get_rules_for_point(point):
            try:
                result = rule.evaluate(context, features)
                if result.matched:
                    results.append((rule, result))
                    logger.debug(
                        "[HeuristicEngine] 规则匹配: %s -> %s (score=%.2f)",
                        rule.id, result.action.value, result.score
                    )
            except Exception as e:
                logger.error("[HeuristicEngine] 规则评估异常: %s | %s", rule.id, e)

        return results, features

    def evaluate_fused(
        self,
        point: DecisionPoint,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> Optional[FusedDecision]:

        matched, features = self.evaluate(point, context, features)
        veto_rules = []
        redirect_suggestions = []

        for rule, result in matched:
            if result.metadata.get("veto_stop"):
                veto_rules.append((rule, result))
            if result.action == DecisionAction.REDIRECT:
                suggestions = result.metadata.get("redirect_suggestions", [])
                redirect_suggestions.extend(suggestions)

        if not matched:
            return None

        matched.sort(key=lambda x: x[0].priority.value, reverse=True)

        final_action = DecisionAction.CONTINUE
        weighted_confidence = 0.0
        total_weight = 0.0
        reasons = []
        contributing_rules = []

        for rule, result in matched:
            weight = rule.priority.value / 100.0
            weighted_confidence += result.score * weight
            total_weight += weight

            # REDIRECT 优先级高于 WARN，低于 STOP
            if result.action == DecisionAction.REDIRECT:
                if final_action not in [DecisionAction.STOP]:
                    final_action = DecisionAction.REDIRECT

            # 确认机制：如果正在进步，降级为 REDIRECT 或 WARN
            elif result.action == DecisionAction.STOP:
                if any(vr.id == rule.id for vr, _ in veto_rules):
                    final_action = DecisionAction.STOP
                else:
                    result = self._confirm_stop(features, result)

                if result.action == DecisionAction.STOP:
                    final_action = DecisionAction.STOP
                elif result.action == DecisionAction.REDIRECT:
                    if final_action not in [DecisionAction.STOP]:
                        final_action = DecisionAction.REDIRECT

            elif result.action == DecisionAction.WARN and final_action == DecisionAction.CONTINUE:
                final_action = DecisionAction.WARN

            reasons.append(f"[{rule.name}] {result.reason}")
            contributing_rules.append(rule.id)

        confidence = weighted_confidence / total_weight if total_weight > 0 else 0.0

        decision = FusedDecision(
            action=final_action,
            confidence=min(confidence, 1.0),
            reasons=reasons,
            contributing_rules=contributing_rules,
            metadata={
                "stuck_iterations": features.stuck_iterations,
                "redirect_suggestions": list(set(redirect_suggestions)),  # 去重
            },
        )

        if self.trace_recorder.enabled:
            self.trace_recorder.record(
                point=str(point.value),
                context=context,
                results=matched,
                fused=decision,
                features=features,
            )

        self._last_decision = decision
        return decision
    def _confirm_stop(
        self,
        features: DerivedFeatures,
        result: RuleEvaluationResult,
    ) -> RuleEvaluationResult:
        if features.progress_trend < -0.3:
            return result  
        if features.stuck_iterations >= 10:
            return result 
        if result.score > 0.9:
            return result 

        suggestions = result.metadata.get("redirect_suggestions", [
            "当前方向可能遇到困难，尝试换个方法",
            "回顾之前的步骤，确认是否有遗漏",
        ])

        return RuleEvaluationResult(
            matched=True,
            score=result.score * 0.7,
            action=DecisionAction.REDIRECT,
            reason=f"{result.reason} (降级：趋势未明确恶化，建议换方向)",
            metadata={"redirect_suggestions": suggestions},
        )

    # ============================================================
    #  便捷方法
    # ============================================================

    def should_stop(
        self,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> Tuple[bool, Optional[str]]:
        if not self.config.enabled:
            return False, None

        termination_decision = self.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)
        if termination_decision and termination_decision.action == DecisionAction.STOP:
            return True, "; ".join(termination_decision.reasons)

        loop_decision = self.evaluate_fused(DecisionPoint.LOOP_DETECTION, context, features)
        if loop_decision and loop_decision.action == DecisionAction.STOP:
            return True, "; ".join(loop_decision.reasons)

        return False, None


    def get_redirect_guidance(
        self,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> Optional[Dict]:
        if not self.config.enabled:
            return None
        redirect_info = None

        loop_decision = self.evaluate_fused(DecisionPoint.LOOP_DETECTION, context, features)
        if loop_decision and loop_decision.action == DecisionAction.REDIRECT:
            suggestions = loop_decision.metadata.get("redirect_suggestions", [])
            redirect_info = {
                "reasons": loop_decision.reasons,
                "suggestions": suggestions,
                "confidence": loop_decision.confidence,
            }

        return redirect_info

    def get_warnings(
        self,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> List[str]:

        if not self.config.enabled:
            return []

        warnings = []

        termination_decision = self.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)
        if termination_decision and termination_decision.action == DecisionAction.WARN:
            warnings.extend(termination_decision.reasons)

        loop_decision = self.evaluate_fused(DecisionPoint.LOOP_DETECTION, context, features)
        if loop_decision and loop_decision.action == DecisionAction.WARN:
            warnings.extend(loop_decision.reasons)

        return warnings


    def reset(self):
        self.feature_extractor.reset()
        self.trace_recorder.reset()
        self._last_decision = None


def get_heuristic_engine() -> HeuristicEngine:
    return HeuristicEngine.get_instance()
