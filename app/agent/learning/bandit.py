# -*- coding: utf-8 -*-
"""
Bandit - Thompson Sampling 选择器
"""

import logging
import random
from typing import Dict, Optional

from app.agent.learning.memory_policy import PolicyBanditMemory, PolicyStats

logger = logging.getLogger(__name__)


class BayesianBandit:
    """
    Bayesian Bandit - Thompson Sampling 实现
    """

    def __init__(self, memory: PolicyBanditMemory, seed: Optional[int] = None):
        """
        初始化

        Args:
            memory: PolicyBanditMemory 实例
            seed: 随机种子（用于测试/复现）
        """
        self.memory = memory
        self._rng = random.Random(seed) if seed else random

    def select_policy(self) -> str:
        """
        Thompson Sampling 选择 Policy

        Returns:
            选中的 Policy 名称
        """
        samples = self._sample_all()

        if not samples:
            logger.warning("[BayesianBandit] 无可用 Policy，返回 default")
            return "default"

        selected = max(samples, key=samples.get)

        logger.info(
            "[BayesianBandit] 选择 | policy=%s | samples=%s",
            selected,
            {k: round(v, 3) for k, v in samples.items()},
        )

        return selected

    def _sample_all(self) -> Dict[str, float]:
        """从所有 Policy 采样"""
        samples = {}
        for name, stat in self.memory.get_all_stats().items():
            samples[name] = self._sample(stat)
        return samples

    def _sample(self, stat: PolicyStats) -> float:
        """从 Beta 分布采样"""
        # 使用 random.betavariate
        return self._rng.betavariate(stat.alpha, stat.beta)

    def get_samples(self) -> Dict[str, float]:
        """
        获取当前采样值（用于可观测性）

        Returns:
            {policy_name: sample_value}
        """
        return self._sample_all()

    def get_policy_probabilities(self) -> Dict[str, float]:
        n_simulations = 1000
        wins = {name: 0 for name in self.memory.get_all_stats().keys()}

        for _ in range(n_simulations):
            samples = self._sample_all()
            winner = max(samples, key=samples.get)
            wins[winner] += 1

        return {name: count / n_simulations for name, count in wins.items()}

    def get_best_policy(self) -> str:
        """
        获取当前最优 Policy（基于期望值，非采样）

        Returns:
            期望值最高的 Policy 名称
        """
        stats = self.memory.get_all_stats()
        if not stats:
            return "default"

        return max(stats, key=lambda name: stats[name].mean)
