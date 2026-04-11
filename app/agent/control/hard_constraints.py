# -*- coding: utf-8 -*-
"""
HardConstraintRenderer - 强约束渲染器 (PromptBuilder v3)

核心：
  1. 三段结构：HARD CONSTRAINTS + CONTROL ACTION + CONTEXT HINT
  2. 自然语言强约束（不是 DSL 标签）
  3. FAILURE CONDITIONS 判定标准
  4. 具体行为规则（MUST DO / MUST NOT DO）
  5. token ≤ 80
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .loop_state import LoopState

from .loop_state import ControlDecision
from app.agent.heuristics.features import get_call_signature


@dataclass
class HardConstraint:
    """渲染后的强约束"""
    hard_constraints: str      # 三段结构文本 (≤80 tokens)
    failure_conditions: str    # 失败判定标准
    trigger_reason: str        # 触发原因
    forbidden: List[str]       # 禁止项
    preferred: List[str]       # 推荐项
    max_tokens: int            # 输出 token 限制
    force_stop: bool = False   # 是否强制终止


class HardConstraintTemplates:
    """
    强约束模板库

    设计原则：
      - HARD CONSTRAINTS：不可违反的判定标准
      - CONTROL ACTION：具体行为规则（MUST DO / MUST NOT DO）
      - CONTEXT HINT：可选的上下文提示
      - token ≤ 80
    """

    # ===== REDIRECT 约束 =====
    # 三段结构，总计 ≤ 80 tokens
    REDIRECT_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: REDIRECT
MUST: Change strategy, use different tool.
MUST NOT: Repeat same tool, continue same path.
[CONTEXT HINT]
Current approach may be stuck."""

    REDIRECT_FORBIDDEN_TOOLS = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: REDIRECT
MUST: Use {forbidden_tools}, switch approach.
MUST NOT: Repeat {last_tool}.
[CONTEXT HINT]
Try {suggested_tools}."""

    # ===== COMPRESS 约束 =====
    COMPRESS_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: COMPRESS
MUST: Summarize in ≤{max_tokens} tokens, bullet points.
MUST NOT: Repeat details, expand new ideas.
[CONTEXT HINT]
Context saturated. Prioritize key facts."""

    # ===== TERMINATE 约束 =====
    TERMINATE_HARD = """[HARD CONSTRAINTS]
You MUST follow termination signal. This is FINAL.
[CONTROL ACTION]
Action: TERMINATE
MUST: Stop immediately, provide summary + next steps.
MUST NOT: Continue exploration, request more input.
[FAILURE CONDITIONS]
- If you continue after this signal = FAILED
- If you ignore summary request = FAILED"""

    # ===== COMPRESS + REDIRECT 融合约束 =====
    COMPRESS_REDIRECT_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: COMPRESS + REDIRECT
MUST: Summarize first (≤{max_tokens} tokens), then change tool.
MUST NOT: Repeat details, use same tool.
[CONTEXT HINT]
Stuck + context saturated."""

    # ===== RETRY 约束 =====
    RETRY_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: RETRY
MUST: Keep direction, adjust prompt/params.
MUST NOT: Repeat exact same approach.
[CONTEXT HINT]
Minor stuck. Try refinement."""

    # ===== COMPRESS + RETRY 融合约束 =====
    COMPRESS_RETRY_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: COMPRESS + RETRY
MUST: Summarize first (≤{max_tokens} tokens), then refine approach.
MUST NOT: Repeat details, use exact same params.
[CONTEXT HINT]
Stuck + need refinement."""

    # ===== CONTINUE 约束（无约束）=====
    CONTINUE_HARD = ""

    @classmethod
    def get_template(cls, action: str) -> str:
        """获取指定 action 的模板"""
        return getattr(cls, action.upper() + "_HARD", cls.CONTINUE_HARD)


class FailureConditionBuilder:
    """Failure Conditions 构建器"""

    @staticmethod
    def build(features: Optional[Any] = None) -> str:
        """
        构建 failure condition 文本

        格式：
            [FAILURE CONDITIONS]
            Your response will be considered FAILED if:
            - condition 1
            - condition 2
        """
        conditions = []

        if features:
            # 工具重复
            if hasattr(features, 'repetition_score') and features.repetition_score > 0.5:
                conditions.append("You repeat the same tool call")

            # 停滞
            if hasattr(features, 'stuck_iterations') and features.stuck_iterations > 2:
                conditions.append(f"You make no progress after {features.stuck_iterations} attempts")

            # 输出循环
            if hasattr(features, 'is_output_loop') and features.is_output_loop:
                conditions.append("You produce identical repeated output")

            # 上下文压力
            if hasattr(features, 'context_saturation') and features.context_saturation > 0.75:
                conditions.append("You ignore context compression request")

        if not conditions:
            return ""

        lines = ["[FAILURE CONDITIONS]", "Your response will be considered FAILED if:"]
        for cond in conditions[:3]:  # 限制数量
            lines.append(f"- {cond}")

        return "\n".join(lines)


class ActionFusion:
    """多 Action 融合策略"""

    PRIORITY = {
        "terminate": 100,
        "compress": 50,
        "retry": 40,
        "redirect": 30,
        "continue": 0,
    }

    FUSABLE = {
        ("compress", "redirect"): "compress_redirect",
        ("compress", "retry"): "compress_retry",
    }

    @classmethod
    def fuse(cls, actions: List[str]) -> str:
        """融合多个 action"""
        if not actions:
            return "continue"

        if "terminate" in actions:
            return "terminate"

        action_set = tuple(sorted(set(actions)))
        if action_set in cls.FUSABLE:
            return cls.FUSABLE[action_set]

        return max(actions, key=lambda a: cls.PRIORITY.get(a, 0))


class HardConstraintRenderer:
    """
    强约束渲染器 - PromptBuilder v3

    三段结构：
      1. HARD CONSTRAINTS：不可违反的判定标准
      2. CONTROL ACTION：具体行为规则
      3. CONTEXT HINT：可选的上下文提示

    特点：
      - 自然语言强约束（非 DSL 标签）
      - MUST DO / MUST NOT DO 格式
      - FAILURE CONDITIONS 判定标准
      - token ≤ 80
    """

    def __init__(self, max_output_tokens: int = 100):
        self.max_output_tokens = max_output_tokens

    def render(
        self,
        decision: ControlDecision,
        features: Optional[Any] = None,
        state: Optional["LoopState"] = None,
    ) -> HardConstraint:
        """
        渲染强约束

        Args:
            decision: 控制决策
            features: 派生特征
            state: 状态

        Returns:
            HardConstraint（三段结构）
        """
        action = decision.action_type

        # 构建 failure condition
        failure_conditions = FailureConditionBuilder.build(features)

        # 构建禁止/推荐项
        forbidden, preferred = self._build_constraints(state, features)

        if action == "terminate":
            return self._render_terminate(failure_conditions)
        elif action == "compress":
            return self._render_compress(failure_conditions)
        elif action == "redirect":
            return self._render_redirect(failure_conditions, forbidden, preferred, state, decision)
        elif action == "compress_redirect":
            return self._render_compress_redirect(failure_conditions, forbidden, preferred)
        elif action == "retry":
            return self._render_retry(failure_conditions)
        elif action == "compress_retry":
            return self._render_compress_retry(failure_conditions)
        else:
            return HardConstraint(
                hard_constraints="",
                failure_conditions="",
                trigger_reason="",
                forbidden=[],
                preferred=[],
                max_tokens=self.max_output_tokens,
            )

    def _render_terminate(self, failure_conditions: str) -> HardConstraint:
        """渲染 terminate 约束"""
        return HardConstraint(
            hard_constraints=HardConstraintTemplates.TERMINATE_HARD,
            failure_conditions=failure_conditions or "Continuing after this signal = FAILED",
            trigger_reason="terminate",
            forbidden=["continue", "explore"],
            preferred=["summary", "next_steps"],
            max_tokens=200,
            force_stop=True,
        )

    def _render_compress(self, failure_conditions: str) -> HardConstraint:
        """渲染 compress 约束"""
        template = HardConstraintTemplates.COMPRESS_HARD.format(
            max_tokens=self.max_output_tokens
        )
        return HardConstraint(
            hard_constraints=template,
            failure_conditions=failure_conditions or "Ignoring compression request = FAILED",
            trigger_reason="compress",
            forbidden=["repeat", "verbose", "expand"],
            preferred=["bullet_points", "key_facts", "concise"],
            max_tokens=self.max_output_tokens,
        )

    def _render_redirect(
        self,
        failure_conditions: str,
        forbidden: List[str],
        preferred: List[str],
        state: Optional["LoopState"],
        decision: ControlDecision,
    ) -> HardConstraint:
        """渲染 redirect 约束"""
        # 如果有具体的禁止/推荐工具，使用详细模板
        if forbidden and state:
            last_tool = forbidden[-1] if forbidden else "last_tool"
            suggested = decision.suggested_tools[:3] if decision.suggested_tools else preferred
            suggested_str = ", ".join(suggested) if suggested else "different tool"

            forbidden_str = ", ".join(forbidden[:2]) if forbidden else last_tool

            hard_constraints = f"""[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: REDIRECT
MUST: Use {suggested_str}, change strategy.
MUST NOT: Use {forbidden_str}.
[CONTEXT HINT]
Current approach may be stuck."""
        else:
            hard_constraints = HardConstraintTemplates.REDIRECT_HARD

        return HardConstraint(
            hard_constraints=hard_constraints,
            failure_conditions=failure_conditions or "Repeating same action = FAILED",
            trigger_reason="redirect",
            forbidden=forbidden,
            preferred=preferred,
            max_tokens=self.max_output_tokens,
        )

    def _render_compress_redirect(
        self,
        failure_conditions: str,
        forbidden: List[str],
        preferred: List[str],
    ) -> HardConstraint:
        """渲染 compress+redirect 融合约束"""
        template = HardConstraintTemplates.COMPRESS_REDIRECT_HARD.format(
            max_tokens=self.max_output_tokens
        )
        return HardConstraint(
            hard_constraints=template,
            failure_conditions=failure_conditions or "Repeating + ignoring compress = FAILED",
            trigger_reason="compress_redirect",
            forbidden=forbidden + ["repeat", "verbose"],
            preferred=preferred + ["bullet_points", "different_tool"],
            max_tokens=self.max_output_tokens,
        )

    def _render_retry(self, failure_conditions: str) -> HardConstraint:
        """渲染 retry 约束"""
        return HardConstraint(
            hard_constraints=HardConstraintTemplates.RETRY_HARD,
            failure_conditions=failure_conditions or "Repeating exact same approach = FAILED",
            trigger_reason="retry",
            forbidden=["repeat_exact", "same_prompt"],
            preferred=["adjust_params", "refine_approach"],
            max_tokens=self.max_output_tokens,
        )

    def _render_compress_retry(
        self,
        failure_conditions: str,
    ) -> HardConstraint:
        """渲染 compress+retry 融合约束"""
        template = HardConstraintTemplates.COMPRESS_RETRY_HARD.format(
            max_tokens=self.max_output_tokens
        )
        return HardConstraint(
            hard_constraints=template,
            failure_conditions=failure_conditions or "Repeating + ignoring compress = FAILED",
            trigger_reason="compress_retry",
            forbidden=["repeat", "verbose", "same_params"],
            preferred=["bullet_points", "adjust_params"],
            max_tokens=self.max_output_tokens,
        )

    def _build_constraints(
        self,
        state: Optional["LoopState"],
        features: Optional[Any],
    ) -> tuple:
        """构建禁止/推荐项（使用签名而非工具名）"""
        forbidden = []
        preferred = []

        if state and state.tool_traces:
            # 禁止特定签名（工具+命令），而非整个工具
            recent_signatures = [
                get_call_signature(t)
                for t in state.tool_traces[-3:]
            ]
            forbidden = [s for s in recent_signatures if s]

            # 推荐未使用的工具（保持原有逻辑）
            if state.available_tools:
                used = set(
                    t.get("tool_name") or t.get("tool")
                    for t in state.tool_traces
                )
                preferred = [t for t in state.available_tools if t not in used][:3]

        return forbidden, preferred

    def render_multi_action(
        self,
        decisions: List[ControlDecision],
        features: Optional[Any] = None,
        state: Optional["LoopState"] = None,
    ) -> HardConstraint:
        """多 Action 融合渲染"""
        if not decisions:
            return HardConstraint(
                hard_constraints="",
                failure_conditions="",
                trigger_reason="none",
                forbidden=[],
                preferred=[],
                max_tokens=self.max_output_tokens,
            )

        actions = [d.action_type for d in decisions]
        fused_action = ActionFusion.fuse(actions)

        fused_decision = ControlDecision(action_type=fused_action)
        if decisions:
            fused_decision.suggested_tools = decisions[0].suggested_tools
            fused_decision.guidance_message = decisions[0].guidance_message

        return self.render(fused_decision, features, state)

    def render_combined(self, constraint: HardConstraint) -> str:
        """
        渲染组合的三段结构文本

        用于注入到 system prompt

        Args:
            constraint: HardConstraint

        Returns:
            组合后的文本
        """
        parts = []

        if constraint.hard_constraints:
            parts.append(constraint.hard_constraints)

        if constraint.failure_conditions:
            parts.append(constraint.failure_conditions)

        return "\n\n".join(parts) if parts else ""
