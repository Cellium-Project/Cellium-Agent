# -*- coding: utf-8 -*-
"""
ControlLoop - 控制环核心

关键职责：
  1. 每轮开始时，综合所有信息做出决策
  2. 每轮结束时，评估反馈并更新 Bandit
  3. 统一决策入口

核心变化：
  - Bandit 选 action，不是 policy
  - 每轮更新 Bandit（不是只 end_session）
"""

import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .loop_state import LoopState, ControlDecision
from .feedback_evaluator import FeedbackEvaluator
from .action_bandit import ActionBandit

if TYPE_CHECKING:
    from app.agent.heuristics.engine import HeuristicEngine
    from app.agent.heuristics.types import EvaluationContext, DerivedFeatures

logger = logging.getLogger(__name__)


class ControlLoop:
    """
    控制环 - Harness 核心

    使用方式：
        # 初始化
        control_loop = ControlLoop(heuristic_engine, action_bandit, feedback_evaluator)

        # 会话开始
        state = LoopState(session_id="xxx", ...)
        control_loop.start_session(state)

        # 每轮循环
        while True:
            decision = control_loop.step(state)
            if decision.should_stop:
                break
            # ... 执行 LLM 调用 ...
            reward = control_loop.end_round(state)

        # 会话结束
        control_loop.end_session(state)
    """

    def __init__(
        self,
        heuristic_engine: "HeuristicEngine",
        action_bandit: ActionBandit,
        feedback_evaluator: FeedbackEvaluator,
    ):
        """
        初始化控制环

        Args:
            heuristic_engine: 启发式引擎（特征提取）
            action_bandit: Action 选择器
            feedback_evaluator: 反馈评估器
        """
        self.heuristics = heuristic_engine
        self.bandit = action_bandit
        self.evaluator = feedback_evaluator

        self._state: Optional[LoopState] = None

    def start_session(self, state: LoopState) -> ControlDecision:
        """
        会话开始 - 初始化状态

        Args:
            state: 初始状态

        Returns:
            初始决策
        """
        self._state = state
        self.heuristics.reset()

        logger.info(
            "[ControlLoop] 会话开始 | session=%s | max_iter=%s",
            state.session_id,
            "∞" if state.max_iterations == float('inf') else str(state.max_iterations)
        )

        # 返回默认决策
        return ControlDecision(action_type="continue")

    def set_policy_thresholds(self, thresholds: Dict[str, Any]):
        """
        接收 Learning 的 Policy 阈值，传递给 ActionBandit

        Args:
            thresholds: Policy 参数，如 {"stuck_iterations": 3, "repetition_threshold": 3}
        """
        self.bandit.set_policy_thresholds(thresholds)
        logger.info("[ControlLoop] Policy 阈值已传递: %s", list(thresholds.keys()))

    def step(self, state: LoopState) -> ControlDecision:
        """
        每轮决策 - 核心控制逻辑

        流程：
          1. 特征提取（Heuristic 作为特征提取器）
          2. 规则先给出 action 候选
          3. Bandit 仅在候选集合内部做 tie-break
          4. 根据 Action 构建决策
          5. 记录决策轨迹

        Args:
            state: 当前状态

        Returns:
            本轮决策
        """
        context = self._build_context(state)
        features = self.heuristics.feature_extractor.extract(context)
        state.features = features

        logger.debug(
            "[ControlLoop] 特征 | stuck=%d | trend=%.2f | raw=%.2f | progress=%.2f",
            features.stuck_iterations,
            features.progress_trend,
            features.progress_trend_raw,
            features.progress_score,
        )

        rule_decisions = self._evaluate_rule_decisions(context, features)
        candidate_actions, rule_reasons = self._derive_action_candidates(rule_decisions, features, state)
        bandit_used = len(candidate_actions) > 1

        if bandit_used:
            action = self.bandit.select_action(features, candidate_actions=candidate_actions)
        else:
            action = candidate_actions[0]

        decision = self._build_decision(
            action,
            features,
            state,
            candidate_actions=candidate_actions,
            rule_reasons=rule_reasons,
            bandit_used=bandit_used,
            rule_decisions=rule_decisions,
        )

        state.decision_trace.append(decision)

        logger.info(
            "[ControlLoop] 决策 | iter=%d | action=%s | candidates=%s | bandit=%s | stop=%s",
            state.iteration,
            action,
            candidate_actions,
            bandit_used,
            decision.should_stop,
        )

        return decision

    def end_round(self, state: LoopState) -> float:
        """
        每轮结束 - 评估反馈并更新 Bandit

        Args:
            state: 当前状态

        Returns:
            本轮 reward
        """
        # 1. 评估反馈
        reward = self.evaluator.evaluate(state)
        state.round_reward = reward
        state.cumulative_reward += reward

        # 2. 仅当 Bandit 真的参与 tie-break 时更新统计
        last_decision = state.get_last_decision()
        if last_decision and last_decision.params.get("bandit_tiebreak"):
            self.bandit.update(last_decision.action_type, reward)

        logger.debug(
            "[ControlLoop] 本轮结束 | reward=%.2f | cumulative=%.2f",
            reward, state.cumulative_reward
        )

        return reward

    def end_session(self, state: LoopState):
        """
        会话结束 - 清理和持久化
        """
        self.bandit.end_session()

        summary = state.get_decision_summary()
        logger.info(
            "[ControlLoop] 会话结束 | iter=%d | cumulative_reward=%.2f | decisions=%s",
            state.iteration, state.cumulative_reward, summary
        )

    # ============================================================
    # 内部方法
    # ============================================================

    def _build_context(self, state: LoopState) -> "EvaluationContext":
        """
        构建评估上下文
        """
        from app.agent.heuristics.types import EvaluationContext

        context = EvaluationContext(
            session_id=state.session_id,
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            recent_tool_calls=state.tool_traces[-10:],
            tool_call_history=state.tool_traces,
            available_tools=state.available_tools,
            total_tokens_used=state.tokens_used,
            token_budget=state.token_budget,
            elapsed_ms=state.elapsed_ms,
            user_input=state.user_input,
            last_tool_result=state.last_tool_result,
            recent_llm_outputs=state.recent_llm_outputs,  # ★ 新增
        )

        return context

    def _evaluate_rule_decisions(
        self,
        context: "EvaluationContext",
        features: "DerivedFeatures",
    ) -> Dict[str, Any]:
        from app.agent.heuristics.types import DecisionPoint

        return {
            "termination": self.heuristics.evaluate_fused(
                DecisionPoint.ITERATION_TERMINATION,
                context,
                features,
            ),
            "loop": self.heuristics.evaluate_fused(
                DecisionPoint.LOOP_DETECTION,
                context,
                features,
            ),
        }

    def _derive_action_candidates(
        self,
        rule_decisions: Dict[str, Any],
        features: "DerivedFeatures",
        state: LoopState,
    ) -> Tuple[List[str], List[str]]:
        from app.agent.heuristics.types import DecisionAction

        reasons: List[str] = []
        termination_decision = rule_decisions.get("termination")
        loop_decision = rule_decisions.get("loop")

        for decision in (termination_decision, loop_decision):
            if not decision:
                continue
            reasons.extend(decision.reasons)
            if decision.action == DecisionAction.STOP:
                return ["terminate"], reasons

        if features.is_output_loop and features.exact_repetition_count >= 5:
            return ["terminate"], reasons + ["检测到连续完全相同输出"]

        candidates = set()
        stuck_threshold = self.heuristics.config.get_threshold("stuck_iterations", 3)
        term_rules = set(getattr(termination_decision, "contributing_rules", []) or [])
        loop_rules = set(getattr(loop_decision, "contributing_rules", []) or [])

        if termination_decision and termination_decision.action == DecisionAction.REDIRECT:
            candidates.add("redirect")
        if loop_decision and loop_decision.action == DecisionAction.REDIRECT:
            candidates.add("redirect")

        if "term-002" in term_rules or features.context_saturation >= 0.75:
            candidates.add("compress")

        if "term-003" in term_rules:
            candidates.update({"retry", "redirect"})

        if "term-004" in term_rules:
            if features.stuck_iterations >= stuck_threshold:
                candidates.add("redirect")
            else:
                candidates.add("retry")

        if "loop-001" in loop_rules:
            candidates.update({"retry", "redirect"})
        if "loop-002" in loop_rules:
            candidates.add("redirect")
        if "loop-003" in loop_rules:
            candidates.add("retry")

        if features.stuck_iterations > 0 and not features.is_making_progress:
            candidates.add("retry")
        if features.repetition_score > 0.55 or features.pattern_detected == "cycle":
            candidates.add("redirect")
        if features.context_saturation >= 0.85:
            candidates.add("compress")

        if not candidates:
            candidates.add("continue")
        elif features.is_making_progress:
            candidates.add("continue")

        ordered_candidates = [
            action for action in ["continue", "retry", "redirect", "compress", "terminate"]
            if action in candidates
        ]
        return ordered_candidates, reasons

    def _collect_redirect_suggestions(self, rule_decisions: Dict[str, Any]) -> List[str]:
        suggestions: List[str] = []
        for key in ("termination", "loop"):
            decision = rule_decisions.get(key)
            if not decision:
                continue
            for suggestion in decision.metadata.get("redirect_suggestions", []) or []:
                if suggestion not in suggestions:
                    suggestions.append(suggestion)
        return suggestions[:3]

    def _build_decision(
        self,
        action: str,
        features: "DerivedFeatures",
        state: LoopState,
        candidate_actions: Optional[List[str]] = None,
        rule_reasons: Optional[List[str]] = None,
        bandit_used: bool = False,
        rule_decisions: Optional[Dict[str, Any]] = None,
    ) -> ControlDecision:
        """
        根据 Action 构建具体决策
        """
        decision = ControlDecision(action_type=action)
        decision.params.update({
            "candidate_actions": candidate_actions or [action],
            "bandit_tiebreak": bandit_used,
            "rule_reasons": rule_reasons or [],
        })

        if action == "terminate":
            decision.should_stop = True
            decision.stop_reason = "规则判定终止"

        elif action == "redirect":
            redirect_suggestions = self._collect_redirect_suggestions(rule_decisions or {})
            decision.enable_redirect_guidance = True
            decision.guidance_message = self._build_redirect_message(features, redirect_suggestions)
            decision.suggested_tools = self._get_alternative_tools(state)
            decision.params["redirect_suggestions"] = redirect_suggestions

        elif action == "compress":
            decision.force_memory_compact = True
            decision.context_trim_level = "aggressive"
            decision.enable_long_memory = False

        elif action == "continue":
            token_ratio = state.tokens_used / max(state.token_budget, 1)
            if token_ratio > 0.8:
                decision.context_trim_level = "aggressive"
            elif token_ratio > 0.6:
                decision.context_trim_level = "normal"

        return decision

    def _build_redirect_message(
        self,
        features: "DerivedFeatures",
        suggestions: Optional[List[str]] = None,
    ) -> str:
        """
        构建重定向引导消息
        """
        reasons = []

        if features.repetition_score > 0.5:
            reasons.append(f"工具重复调用较多 (score={features.repetition_score:.0%})")

        if features.stuck_iterations > 0:
            reasons.append(f"已停滞 {features.stuck_iterations} 轮")

        if not reasons:
            reasons.append("当前方向可能遇到困难")

        message = "## ⚠️ 方向调整建议\n\n"
        message += "检测到当前执行可能陷入困境：\n\n"
        message += "**问题原因：**\n"
        for r in reasons:
            message += f"- {r}\n"
        message += "\n**建议：**\n"
        if suggestions:
            for suggestion in suggestions[:3]:
                message += f"- {suggestion}\n"
        else:
            message += "- 尝试换一个工具或方法\n"
            message += "- 回顾之前的步骤，确认是否有遗漏\n"

        return message

    def _get_alternative_tools(self, state: LoopState) -> List[str]:
        """
        获取推荐的工具列表
        """
        if not state.available_tools:
            return []

        # 找出还没用过的工具
        used_tools = set()
        for trace in state.tool_traces:
            tool = trace.get("tool_name") or trace.get("tool")
            if tool:
                used_tools.add(tool)

        unused = [t for t in state.available_tools if t not in used_tools]

        # 优先推荐没用过的
        if unused:
            return unused[:3]

        # 否则返回所有可用工具
        return state.available_tools[:3]


# ============================================================
# 工厂函数
# ============================================================

def create_control_loop(
    memory_path: Optional[str] = None,
) -> ControlLoop:
    """
    创建控制环实例

    Args:
        memory_path: Bandit 统计数据持久化路径
    """
    from app.agent.heuristics.engine import get_heuristic_engine

    engine = get_heuristic_engine()
    bandit = ActionBandit(memory_path=memory_path)
    evaluator = FeedbackEvaluator()

    return ControlLoop(
        heuristic_engine=engine,
        action_bandit=bandit,
        feedback_evaluator=evaluator,
    )
