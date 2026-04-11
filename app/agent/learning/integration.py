# -*- coding: utf-8 -*-
"""
Integration - 与 AgentLoop 集成
"""

import logging
from typing import Optional, TYPE_CHECKING

from app.agent.learning.policy import POLICY_TEMPLATES, get_policy_params
from app.agent.learning.memory_policy import PolicyBanditMemory
from app.agent.learning.bandit import BayesianBandit

if TYPE_CHECKING:
    from app.agent.heuristics.engine import HeuristicEngine

logger = logging.getLogger(__name__)


def compute_reward(
    error: bool,
    iteration: int,
    max_iterations: int,
    user_feedback: Optional[float] = None,
    tool_call_count: int = 0,
    stuck_iterations: int = 0,
) -> float:
    """
    计算评分（改进版 - 更有区分度）

    Args:
        error: 是否有错误
        iteration: 实际迭代次数
        max_iterations: 最大迭代次数
        user_feedback: 用户显式反馈（可选）
        tool_call_count: 工具调用次数
        stuck_iterations: 停滞迭代次数

    Returns:
        reward ∈ [0, 1]

    评分维度：
      - 错误：直接 0 分
      - 效率：迭代少 = 高分
      - 顺畅：无停滞 = 高分
      - 用户反馈：覆盖其他维度
    """
    # 有错误 → 0.0
    if error:
        return 0.0

    # 用户反馈优先
    if user_feedback is not None:
        return max(0.0, min(1.0, user_feedback))

    # 基础分：完成了任务
    base = 0.6

    # 效率分：迭代越少分越高 (0-0.2)
    efficiency_ratio = 1.0 - (iteration / max_iterations)
    efficiency_score = efficiency_ratio * 0.2

    # 顺畅分：无停滞 = 高分 (0-0.15)
    # 停滞多 = 扣分
    smooth_score = 0.15 * (1.0 - min(stuck_iterations / max(iteration, 1), 1.0))

    # 工具效率：工具调用少但完成了任务 = 高分 (0-0.05)
    if iteration > 0:
        tool_efficiency = 0.05 * max(0, 1.0 - tool_call_count / (iteration * 3))
    else:
        tool_efficiency = 0.05

    reward = base + efficiency_score + smooth_score + tool_efficiency

    return max(0.0, min(1.0, reward))


class LearningIntegration:
    """
    Learning 模块集成适配器

    与 AgentLoop 的集成点：
      1. __init__: 初始化 memory, bandit
      2. start_session(): 选择 Policy，注入 HeuristicEngine
      3. end_session(): 更新统计，检查衰减
    """

    def __init__(
        self,
        heuristic_engine: "HeuristicEngine",
        memory_path: Optional[str] = None,
        enabled: bool = True,
    ):
        """
        初始化

        Args:
            heuristic_engine: HeuristicEngine 实例
            memory_path: 统计文件路径（可选）
            enabled: 是否启用学习
        """
        self.heuristic_engine = heuristic_engine
        self.enabled = enabled

        self.memory = PolicyBanditMemory(path=memory_path)
        self.bandit = BayesianBandit(self.memory)

        self._current_policy: str = "default"
        self._session_active = False

        # ★ ControlLoop 引用（用于传递 Policy 阈值）
        self._control_loop = None

    def start_session(self) -> str:
        """
        会话开始：选择 Policy 并注入

        Returns:
            选中的 Policy 名称
        """
        if not self.enabled:
            return "default"

        self._current_policy = self.bandit.select_policy()
        self._apply_policy(self._current_policy)
        self._session_active = True

        logger.info(
            "[LearningIntegration] 会话开始 | policy=%s",
            self._current_policy,
        )

        return self._current_policy

    def _apply_policy(self, policy_name: str):
        """
        将 Policy 参数注入 HeuristicEngine 和 ControlLoop

        Args:
            policy_name: Policy 名称
        """
        params = get_policy_params(policy_name)

        # 1. 注入到 HeuristicEngine.config.thresholds
        if hasattr(self.heuristic_engine, "config"):
            if hasattr(self.heuristic_engine.config, "thresholds"):
                for key, value in params.items():
                    self.heuristic_engine.config.thresholds[key] = value
                    logger.debug(
                        "[LearningIntegration] 注入参数 | %s=%s", key, value
                    )

        # 2. 传递到 ControlLoop → ActionBandit（分层协作）
        if self._control_loop:
            self._control_loop.set_policy_thresholds(params)

        logger.info(
            "[LearningIntegration] Policy 注入完成 | policy=%s | params=%s",
            policy_name,
            list(params.keys()),
        )

    def set_control_loop(self, control_loop):
        """
        设置 ControlLoop 引用（用于分层协作）

        Args:
            control_loop: ControlLoop 实例
        """
        self._control_loop = control_loop
        logger.info("[LearningIntegration] ControlLoop 已设置")

    def end_session(
        self,
        error: bool = False,
        iteration: int = 1,
        max_iterations: int = 10,
        user_feedback: Optional[float] = None,
        tool_call_count: int = 0,
        stuck_iterations: int = 0,
    ):
        """
        会话结束：更新统计

        Args:
            error: 是否有错误
            iteration: 实际迭代次数
            max_iterations: 最大迭代次数
            user_feedback: 用户显式反馈（可选）
            tool_call_count: 工具调用次数
            stuck_iterations: 停滞迭代次数
        """
        if not self.enabled or not self._session_active:
            return

        # 计算 reward
        reward = compute_reward(
            error=error,
            iteration=iteration,
            max_iterations=max_iterations,
            user_feedback=user_feedback,
            tool_call_count=tool_call_count,
            stuck_iterations=stuck_iterations,
        )

        # 更新统计
        self.memory.update(self._current_policy, reward)

        # 检查是否需要衰减
        if self.memory.should_decay():
            self.memory.decay()

        logger.info(
            "[LearningIntegration] 会话结束 | policy=%s | reward=%.2f | error=%s | iter=%d",
            self._current_policy,
            reward,
            error,
            iteration,
        )

        self._session_active = False

    def get_current_policy(self) -> str:
        """获取当前 Policy"""
        return self._current_policy

    def get_stats_summary(self) -> dict:
        """获取统计摘要"""
        return self.memory.get_summary()

    def reset(self):
        """重置所有学习状态"""
        self.memory.reset()
        self._current_policy = "default"
        self._session_active = False
        logger.info("[LearningIntegration] 已重置")

    def force_policy(self, policy_name: str):
        """
        强制指定 Policy（用于测试/调试）

        Args:
            policy_name: Policy 名称
        """
        if policy_name not in POLICY_TEMPLATES:
            logger.warning("[LearningIntegration] 未知 Policy: %s", policy_name)
            return

        self._current_policy = policy_name
        self._apply_policy(policy_name)
        logger.info("[LearningIntegration] 强制 Policy: %s", policy_name)
