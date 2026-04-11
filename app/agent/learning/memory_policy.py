# -*- coding: utf-8 -*-
"""
Memory - Policy 统计持久化
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PolicyStats:
    """单个 Policy 的 Beta 分布统计"""

    alpha: float = 2.0  # 成功计数（含先验）
    beta: float = 2.0   # 失败计数（含先验）

    @property
    def mean(self) -> float:
        """期望值 = alpha / (alpha + beta)"""
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    @property
    def variance(self) -> float:
        """方差 = alpha * beta / ((alpha + beta)^2 * (alpha + beta + 1))"""
        total = self.alpha + self.beta
        if total <= 1:
            return 0.0
        return (self.alpha * self.beta) / (total * total * (total + 1))

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "PolicyStats":
        return cls(
            alpha=data.get("alpha", 2.0),
            beta=data.get("beta", 2.0),
        )


class PolicyBanditMemory:
    
    DEFAULT_PATH = "data/learning/policy_bandit_stats.json"
    DECAY_INTERVAL = 50  # 每 50 个会话衰减一次
    DECAY_FACTOR = 0.99

    def __init__(self, path: str = None, policies: list = None):
        self.path = path or self.DEFAULT_PATH
        self.policies = policies or ["default", "efficient", "aggressive"]
        self._stats: Dict[str, PolicyStats] = {}
        self._session_count = 0
        self._dirty = False

        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._session_count = data.get("session_count", 0)
                stats_data = data.get("stats", {})
                for name in self.policies:
                    if name in stats_data:
                        self._stats[name] = PolicyStats.from_dict(stats_data[name])
                    else:
                        self._stats[name] = PolicyStats()
                logger.info("[PolicyBanditMemory] 加载成功 | path=%s | sessions=%d",
                           self.path, self._session_count)
            except Exception as e:
                logger.warning("[PolicyBanditMemory] 加载失败，使用默认值: %s", e)
                self._init_default()
        else:
            self._init_default()

    def _init_default(self):
        for name in self.policies:
            self._stats[name] = PolicyStats()
        self._dirty = True

    def _save(self):
        if not self._dirty:
            return

        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)

            data = {
                "session_count": self._session_count,
                "stats": {name: stat.to_dict() for name, stat in self._stats.items()},
            }

            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            self._dirty = False
            logger.debug("[PolicyBanditMemory] 保存成功 | path=%s", self.path)

        except Exception as e:
            logger.error("[PolicyBanditMemory] 保存失败: %s", e)

    def get_stats(self, policy_name: str) -> PolicyStats:
        if policy_name not in self._stats:
            self._stats[policy_name] = PolicyStats()
        return self._stats[policy_name]

    def get_all_stats(self) -> Dict[str, PolicyStats]:
        return self._stats.copy()

    def update(self, policy_name: str, reward: float, persist: bool = True):
        """
        更新 Policy 统计

        Args:
            policy_name: Policy 名称
            reward: 奖励值 ∈ [0, 1]
            persist: 是否立即持久化（False 用于轮次内轻量更新）
        """
        if policy_name not in self._stats:
            logger.warning("[PolicyBanditMemory] 未知 Policy: %s", policy_name)
            return

        reward = max(0.0, min(1.0, reward))

        stat = self._stats[policy_name]

        # Beta-Binomial 更新（软奖励）
        # alpha += reward, beta += (1 - reward)
        stat.alpha += reward
        stat.beta += (1.0 - reward)

        self._dirty = True
        if persist:
            self._save()
            logger.info(
                "[PolicyBanditMemory] 更新 | policy=%s | reward=%.2f | alpha=%.1f | beta=%.1f | mean=%.3f",
                policy_name, reward, stat.alpha, stat.beta, stat.mean
            )

    def decay(self, factor: float = None):
        factor = factor or self.DECAY_FACTOR

        for name, stat in self._stats.items():
            stat.alpha *= factor
            stat.beta *= factor

        self._dirty = True
        self._save()

        logger.info("[PolicyBanditMemory] 衰减完成 | factor=%.2f", factor)

    def should_decay(self) -> bool:
        self._session_count += 1
        self._dirty = True
        self._save()
        return self._session_count % self.DECAY_INTERVAL == 0

    def reset(self):
        self._init_default()
        self._session_count = 0
        self._save()
        logger.info("[PolicyBanditMemory] 已重置")

    def get_summary(self) -> Dict:
        return {
            "session_count": self._session_count,
            "policies": {
                name: {
                    "alpha": stat.alpha,
                    "beta": stat.beta,
                    "mean": round(stat.mean, 3),
                    "variance": round(stat.variance, 4),
                }
                for name, stat in self._stats.items()
            },
        }
