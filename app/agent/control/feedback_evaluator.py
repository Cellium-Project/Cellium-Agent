# -*- coding: utf-8 -*-
"""
FeedbackEvaluator - 分段式反馈评估器

核心原则：
  1. 先区分成功/失败（大差异）
  2. 再优化好 vs 更好（小差异）

分段式设计：
  - 成功：基础分 1.0，然后扣效率/成本
  - 失败：基础分 0.0，然后扣停滞时间
"""

import logging
from typing import Dict, Any, Optional

from .loop_state import LoopState

logger = logging.getLogger(__name__)


class FeedbackEvaluator:
    """
    反馈评估器

    输出范围: [0, 1]
    - 成功: 0.7 ~ 1.0（取决于效率和成本）
    - 失败: 0.0 ~ 0.3（取决于停滞程度）
    """

    def __init__(
        self,
        success_base: float = 1.0,
        failure_base: float = 0.0,
        iteration_penalty_coef: float = 0.1,
        cost_penalty_coef: float = 0.2,
        stuck_penalty_coef: float = 0.3,
        token_threshold: float = 0.8,
    ):
        """
        初始化评估器

        Args:
            success_base: 成功基础分
            failure_base: 失败基础分
            iteration_penalty_coef: 迭代惩罚系数
            cost_penalty_coef: 成本惩罚系数
            stuck_penalty_coef: 停滞惩罚系数
            token_threshold: Token 紧张阈值
        """
        self.success_base = success_base
        self.failure_base = failure_base
        self.iteration_penalty_coef = iteration_penalty_coef
        self.cost_penalty_coef = cost_penalty_coef
        self.stuck_penalty_coef = stuck_penalty_coef
        self.token_threshold = token_threshold

    def evaluate(self, state: LoopState) -> float:
        """
        评估当前状态，返回 reward

        分段式逻辑：
          1. 判断是否成功
          2. 成功分支：扣效率和成本
          3. 失败分支：扣停滞时间
        """
        # 1. 判断本轮是否成功
        success = self._is_round_success(state)

        if success:
            reward = self._evaluate_success(state)
        else:
            reward = self._evaluate_failure(state)

        # 限制在 [0, 1] 范围内
        reward = max(0.0, min(1.0, reward))

        logger.debug(
            "[FeedbackEvaluator] reward=%.2f | success=%s | iter=%d",
            reward, success, state.iteration
        )

        return reward

    def _is_round_success(self, state: LoopState) -> bool:
        """
        判断本轮是否成功

        成功标准：
          - 最后工具调用成功
          - 没有错误
        """
        if not state.last_tool_result:
            # 如果没有工具调用结果，视为中性
            return True

        result = state.last_tool_result

        if isinstance(result, dict):
            # 检查错误标记
            if result.get("error") or result.get("status") == "error":
                return False
            if result.get("success") is False:
                return False
            return True

        # 非字典类型，视为成功
        return True

    def _evaluate_success(self, state: LoopState) -> float:
        """
        成功分支评估

        reward = success_base - iteration_penalty - cost_penalty
        """
        reward = self.success_base

        # 2. 效率惩罚（迭代越多扣越多）
        iteration_ratio = state.iteration / max(state.max_iterations, 1)
        iteration_penalty = self.iteration_penalty_coef * iteration_ratio
        reward -= iteration_penalty

        # 3. 成本惩罚（Token 超预算扣更多）
        token_ratio = state.tokens_used / max(state.token_budget, 1)
        if token_ratio > self.token_threshold:
            # 超阈值部分线性惩罚
            excess = (token_ratio - self.token_threshold) / (1 - self.token_threshold)
            cost_penalty = self.cost_penalty_coef * excess
            reward -= cost_penalty

        logger.debug(
            "[FeedbackEvaluator] success | base=%.2f iter_pen=%.2f cost_pen=%.2f",
            self.success_base, iteration_penalty,
            max(0, cost_penalty) if token_ratio > self.token_threshold else 0
        )

        return reward

    def _evaluate_failure(self, state: LoopState) -> float:
        """
        失败分支评估

        reward = failure_base - stuck_penalty
        """
        reward = self.failure_base

        # 4. 失败惩罚（卡得越久扣越多）
        if state.features:
            stuck = state.features.stuck_iterations
            # 最多扣 stuck_penalty_coef
            stuck_penalty = self.stuck_penalty_coef * min(stuck / 5, 1.0)
            reward -= stuck_penalty

            logger.debug(
                "[FeedbackEvaluator] failure | base=%.2f stuck=%d stuck_pen=%.2f",
                self.failure_base, stuck, stuck_penalty
            )
        else:
            # 没有特征，扣一半
            reward -= self.stuck_penalty_coef / 2

        return reward

    def explain(self, state: LoopState) -> Dict[str, Any]:
        """
        解释 reward 计算过程（用于调试）
        """
        success = self._is_round_success(state)

        explanation = {
            "success": success,
            "base_reward": self.success_base if success else self.failure_base,
            "penalties": {},
            "final_reward": self.evaluate(state),
        }

        if success:
            # 成功分支的惩罚
            iteration_ratio = state.iteration / max(state.max_iterations, 1)
            explanation["penalties"]["iteration"] = {
                "ratio": iteration_ratio,
                "amount": self.iteration_penalty_coef * iteration_ratio,
            }

            token_ratio = state.tokens_used / max(state.token_budget, 1)
            if token_ratio > self.token_threshold:
                excess = (token_ratio - self.token_threshold) / (1 - self.token_threshold)
                explanation["penalties"]["cost"] = {
                    "token_ratio": token_ratio,
                    "excess": excess,
                    "amount": self.cost_penalty_coef * excess,
                }
        else:
            # 失败分支的惩罚
            if state.features:
                stuck = state.features.stuck_iterations
                explanation["penalties"]["stuck"] = {
                    "iterations": stuck,
                    "amount": self.stuck_penalty_coef * min(stuck / 5, 1.0),
                }

        return explanation
