# -*- coding: utf-8 -*-
"""
ActionBandit - Action-based Bandit

核心升级（v3）：
  1. Heuristic → bias 模式（Bandit 主导学习）
  2. 新增 retry action
  3. n-step return reward（短期序列优化）
"""

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Action 类型：新增 retry
ACTION_TYPES = ["continue", "retry", "redirect", "compress", "terminate"]
ACTION_PRIORITY = ["continue", "retry", "compress", "redirect", "terminate"]


@dataclass
class ActionStats:
    """单个 Action 的统计数据"""
    alpha: float = 2.0
    beta: float = 2.0
    count: int = 0

    def to_dict(self) -> Dict:
        return {"alpha": self.alpha, "beta": self.beta, "count": self.count}

    @classmethod
    def from_dict(cls, data: Dict) -> "ActionStats":
        return cls(
            alpha=data.get("alpha", 2.0),
            beta=data.get("beta", 2.0),
            count=data.get("count", 0),
        )


class ActionBandit:
    """
    Action-based Bandit
    """

    def __init__(self, memory_path: Optional[str] = None, n_step: int = 3):
        """
        初始化

        Args:
            memory_path: 统计数据持久化路径
            n_step: n-step return 窗口大小
        """
        self.memory_path = memory_path
        self.n_step = n_step

        self._stats: Dict[str, ActionStats] = {
            action: ActionStats() for action in ACTION_TYPES
        }

        self._decay_factor = 0.99
        self._decay_interval = 50
        self._session_count = 0

        self._policy_thresholds: Dict[str, Any] = {}
        self._reward_buffer: deque = deque(maxlen=n_step)
        self._last_actions: deque = deque(maxlen=n_step)

        if memory_path:
            self._load()

    def select_action(
        self,
        features: Any,
        candidate_actions: Optional[List[str]] = None,
    ) -> str:
        """
        选择 Action。

        当前策略：
          1. 规则/控制环先给出候选 action
          2. Bandit 只在候选集合内部做 tie-break
          3. terminate 仍保留硬规则保护
        """
        is_terminate_hard = (
            hasattr(features, 'is_output_loop')
            and features.is_output_loop
            and hasattr(features, 'exact_repetition_count')
            and features.exact_repetition_count >= 5
        )

        if is_terminate_hard:
            logger.info(
                "[ActionBandit] 硬规则: terminate (output_loop=%d)",
                features.exact_repetition_count
            )
            return "terminate"

        candidates = self._normalize_candidates(candidate_actions)
        if not candidates:
            return "continue"

        if len(candidates) == 1:
            return candidates[0]

        action = self._thompson_with_bias(features, candidate_actions=candidates)
        logger.debug("[ActionBandit] tie-break: %s | candidates=%s", action, candidates)
        return action

    def _normalize_candidates(self, candidate_actions: Optional[List[str]]) -> List[str]:
        if not candidate_actions:
            return [action for action in ACTION_TYPES if action != "terminate"]

        candidates = []
        for action in candidate_actions:
            if action in ACTION_TYPES and action not in candidates:
                candidates.append(action)

        return candidates

    def _thompson_with_bias(self, features: Any, candidate_actions: Optional[List[str]] = None) -> str:
        """
        Thompson Sampling + Heuristic Bias

        原理：
          - 每个 action 从 Beta 分布采样
          - 加上 Heuristic 提供的 bias
          - 选择分数最高的 action
        """
        import random

        candidates = self._normalize_candidates(candidate_actions) or list(self._stats.keys())

        scores = {}
        for action in candidates:
            stats = self._stats[action]

            try:
                import numpy as np
                sample = np.random.beta(stats.alpha, stats.beta)
            except ImportError:
                mean = stats.alpha / (stats.alpha + stats.beta) if (stats.alpha + stats.beta) > 0 else 0.5
                noise = random.gauss(0, 0.1)
                sample = max(0, min(1, mean + noise))

            bias = self._heuristic_bias(action, features)

            scores[action] = sample + bias

        if not scores:
            return "continue"

        max_score = max(scores.values())
        best_actions = [a for a, s in scores.items() if s == max_score]

        # 优先级：continue > retry > compress > redirect > terminate
        for priority_action in ACTION_PRIORITY:
            if priority_action in best_actions:
                return priority_action

        return best_actions[0]

    def _heuristic_bias(self, action: str, features: Any) -> float:
        """
        Heuristic Bias 计算

        原理：
          - Heuristic 不决定 action，只调整分数
          - bias > 0 表示"推荐"，但 Bandit 仍可选择其他
          - 不同 action 有不同的触发条件
          - 使用 Policy 阈值动态调整判断条件

        Returns:
            bias 值 [0, 1]
        """
        bias = 0.0

        stuck_threshold = self._policy_thresholds.get("stuck_iterations", 3)
        repetition_threshold = self._policy_thresholds.get("repetition_threshold", 3)

        if action == "redirect":
            if hasattr(features, 'repetition_score') and features.repetition_score > 0.5:
                bias = max(bias, 0.2 * features.repetition_score)
            if hasattr(features, 'stuck_iterations') and features.stuck_iterations >= stuck_threshold:
                bias = max(bias, 0.25)

        if action == "retry":
            if hasattr(features, 'stuck_iterations') and 1 <= features.stuck_iterations < stuck_threshold:
                bias = max(bias, 0.15)
            if hasattr(features, 'progress_trend') and 0 < features.progress_trend < 0.3:
                bias = max(bias, 0.1)

        if action == "compress":
            if hasattr(features, 'context_saturation') and features.context_saturation > 0.6:
                bias = max(bias, 0.2)
            if hasattr(features, 'stuck_iterations') and features.stuck_iterations >= stuck_threshold // 2:
                bias = max(bias, 0.15)

        if action == "continue":
            if hasattr(features, 'progress_score') and features.progress_score > 0.5:
                bias = max(bias, 0.15)
            if hasattr(features, 'stuck_iterations') and features.stuck_iterations == 0:
                bias = max(bias, 0.2)

        return bias

    def set_policy_thresholds(self, thresholds: Dict[str, Any]):
        """设置 Policy 阈值约束"""
        self._policy_thresholds = thresholds
        logger.info("[ActionBandit] Policy 阈值已设置: %s", list(thresholds.keys()))

    def update(self, action: str, reward: float):
        if action not in self._stats:
            logger.warning("[ActionBandit] 未知 action: %s", action)
            return

        self._reward_buffer.append(reward)
        self._last_actions.append(action)

        n_step_reward = self._compute_n_step_return()

        stats = self._stats[action]
        stats.count += 1

        if n_step_reward > 0.5:
            stats.alpha += n_step_reward
        else:
            stats.beta += (1 - n_step_reward)

        logger.debug(
            "[ActionBandit] 更新 %s | reward=%.2f | n_step=%.2f | alpha=%.1f beta=%.1f",
            action, reward, n_step_reward, stats.alpha, stats.beta
        )

        if self.memory_path:
            self._save()

    def _compute_n_step_return(self) -> float:
        """
        计算 n-step return
        G_t = (r_t + r_{t+1} + ... + r_{t+n-1}) / n

        """
        if not self._reward_buffer:
            return 0.5  # 默认中性

        return sum(self._reward_buffer) / len(self._reward_buffer)

    def end_session(self):
        self._session_count += 1

        if self._session_count >= self._decay_interval:
            self._decay()
            self._session_count = 0

        self._reward_buffer.clear()
        self._last_actions.clear()

    def _decay(self):
        for action, stats in self._stats.items():
            stats.alpha = 1 + (stats.alpha - 1) * self._decay_factor
            stats.beta = 1 + (stats.beta - 1) * self._decay_factor

        logger.info("[ActionBandit] 统计已衰减 (factor=%.2f)", self._decay_factor)

    def get_stats(self) -> Dict[str, Dict]:
        return {
            action: stats.to_dict()
            for action, stats in self._stats.items()
        }

    def get_summary(self) -> Dict[str, Any]:
        summary = {
            "total_actions": sum(s.count for s in self._stats.values()),
            "actions": {},
        }

        for action, stats in self._stats.items():
            mean = stats.alpha / (stats.alpha + stats.beta) if (stats.alpha + stats.beta) > 0 else 0.5
            summary["actions"][action] = {
                "count": stats.count,
                "success_rate": mean,
            }

        return summary

    def _load(self):
        if not self.memory_path or not os.path.exists(self.memory_path):
            return

        try:
            with open(self.memory_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for action, stats_data in data.get("actions", {}).items():
                if action in self._stats:
                    self._stats[action] = ActionStats.from_dict(stats_data)

            logger.info("[ActionBandit] 加载统计数据: %s", self.memory_path)

        except Exception as e:
            logger.warning("[ActionBandit] 加载失败: %s", e)

    def _save(self):
        if not self.memory_path:
            return

        try:
            Path(self.memory_path).parent.mkdir(parents=True, exist_ok=True)

            data = {
                "actions": self.get_stats(),
                "session_count": self._session_count,
            }

            with open(self.memory_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.warning("[ActionBandit] 保存失败: %s", e)

    def reset(self):
        for action in self._stats:
            self._stats[action] = ActionStats()
        self._session_count = 0
        self._reward_buffer.clear()
        self._last_actions.clear()

        if self.memory_path:
            self._save()

        logger.info("[ActionBandit] 统计已重置")
