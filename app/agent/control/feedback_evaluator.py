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
from datetime import datetime
from typing import Dict, Any, List, Optional

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
        success = self._is_round_success(state)

        if success:
            reward = self._evaluate_success(state)
        else:
            reward = self._evaluate_failure(state)

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
            return True

        result = state.last_tool_result

        if isinstance(result, dict):
            if result.get("error") or result.get("status") == "error":
                return False
            if result.get("success") is False:
                return False
            return True

        return True

    def _evaluate_success(self, state: LoopState) -> float:
        """
        成功分支评估

        reward = success_base - iteration_penalty - cost_penalty
        """
        reward = self.success_base

        iteration_ratio = state.iteration / max(state.max_iterations, 1)
        iteration_penalty = self.iteration_penalty_coef * iteration_ratio
        reward -= iteration_penalty

        token_ratio = state.tokens_used / max(state.token_budget, 1)
        if token_ratio > self.token_threshold:
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

        if state.features:
            stuck = state.features.stuck_iterations
            stuck_penalty = self.stuck_penalty_coef * min(stuck / 5, 1.0)
            reward -= stuck_penalty

            logger.debug(
                "[FeedbackEvaluator] failure | base=%.2f stuck=%d stuck_pen=%.2f",
                self.failure_base, stuck, stuck_penalty
            )
        else:
            reward -= self.stuck_penalty_coef / 2

        return reward

    def evaluate_with_gene_evolution(self, state: LoopState, task_type: str = "", user_input: str = "") -> float:
        reward = self.evaluate(state)

        from .constraint_gene import GeneEvolution

        logger.info(f"[GeneEvolution] evaluate_with_gene_evolution called | reward={reward:.2f} | task_type={task_type} | user_input={user_input[:50] if user_input else 'empty'}")

        try:
            if reward < 0.5:
                if not task_type and state.tool_traces:
                    last_tool = state.tool_traces[-1].get('tool', '')
                    if last_tool:
                        task_type = last_tool
                        logger.debug("[GeneEvolution] 从工具调用推断 task_type: %s", task_type)
                
                avoid_cue = GeneEvolution.extract_avoid_cue(state, reward)
                logger.info(f"[GeneEvolution] extract_avoid_cue result | avoid_cue={avoid_cue[:50] if avoid_cue else 'None'} | task_type={task_type}")
                if avoid_cue and task_type:
                    state.gene_failure_count += 1
                    state.gene_failure_history.append({
                        "iteration": state.iteration,
                        "avoid_cue": avoid_cue,
                        "error": state.last_error,
                        "task_type": task_type,  # 记录工具名
                        "timestamp": datetime.now().isoformat(),
                    })
                    # 只保留最近 3 次
                    if len(state.gene_failure_history) > 3:
                        state.gene_failure_history = state.gene_failure_history[-3:]

                    logger.info(f"[GeneEvolution] 记录失败 #{state.gene_failure_count}: tool={task_type}, cue={avoid_cue[:50]}")

                    if state.gene_failure_count >= 2:
                        failed_tools = set(h.get("task_type", "") for h in state.gene_failure_history if h.get("task_type"))
                        
                        if GeneEvolution.should_prompt_agent_for_gene(state, task_type, avoid_cue):
                            state.needs_agent_gene_creation = True
                            state.gene_creation_prompt = self._build_gene_view_prompt(state, user_input, state.gene_failure_history)
                            logger.info(f"[GeneEvolution] 累积{state.gene_failure_count}次失败，涉及工具: {failed_tools}，将后台创建 Gene")

                            for tool in failed_tools:
                                tool_cues = [h.get("avoid_cue", "") for h in state.gene_failure_history if h.get("task_type") == tool]
                                combined_cue = "; ".join(filter(None, tool_cues)) if tool_cues else avoid_cue
                                GeneEvolution.update_gene_from_failure(tool, combined_cue, state, reward)
                                logger.info(f"[GeneEvolution] 更新 Gene: {tool}")
                        
                        state.gene_failure_count = 0
                        state.gene_failure_history = []
                        logger.info("[GeneEvolution] Gene 触发完成，重置失败计数")
            elif reward >= 0.5:
                if task_type:
                    GeneEvolution.record_success(task_type, reward, state.elapsed_ms)
        except Exception as e:
            logger.warning("[GeneEvolution] Gene evolution failed: %s", e)

        return reward

    def _build_gene_view_prompt(self, state: LoopState, user_input: str, failure_history: List[Dict]) -> str:
        """构建提示 Agent 查看 Gene 的提示（包含失败历史）"""
        from .constraint_gene import GeneEvolution

        # 构建失败历史描述
        failure_summary = "\n".join([
            f"  {i+1}. 第{h['iteration']}轮: {h['avoid_cue']}"
            for i, h in enumerate(failure_history[-2:])  # 只显示最近2次
        ])

        # 获取现有 Gene 信息
        existing_gene = GeneEvolution._get_existing_gene(user_input)

        if existing_gene:
            return f"""[系统提示]
检测到任务连续 2 次执行失败：
{failure_summary}

已有相关 Gene 记录，请先查看：

使用命令：memory get_gene task_type={existing_gene['task_type']}

查看后根据 Gene 中的指导继续执行任务。"""
        else:
            return f"""[系统提示]
检测到任务连续 2 次执行失败：
{failure_summary}

系统将自动分析失败原因并创建指导规则。

请继续尝试完成任务，系统会根据执行情况优化策略。"""

    def explain(self, state: LoopState) -> Dict[str, Any]:
        success = self._is_round_success(state)

        explanation = {
            "success": success,
            "base_reward": self.success_base if success else self.failure_base,
            "penalties": {},
            "final_reward": self.evaluate(state),
        }

        if success:
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
            if state.features:
                stuck = state.features.stuck_iterations
                explanation["penalties"]["stuck"] = {
                    "iterations": stuck,
                    "amount": self.stuck_penalty_coef * min(stuck / 5, 1.0),
                }

        return explanation
