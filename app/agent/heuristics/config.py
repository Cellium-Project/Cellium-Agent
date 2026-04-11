# -*- coding: utf-8 -*-
"""
启发式配置管理

配置来源：config/agent/heuristics.yaml
加载方式：通过 AgentConfig 单例统一加载，支持热重载
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class RuleConfig:
    enabled: bool = True
    threshold: Optional[float] = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HeuristicConfig:
    enabled: bool = True
    log_level: str = "info"
    trace_enabled: bool = False
    thresholds: Dict[str, float] = field(default_factory=dict)
    rules: Dict[str, RuleConfig] = field(default_factory=dict)

    # 默认阈值
    DEFAULT_THRESHOLDS = {
        "max_iterations_ratio": 0.8,      # 迭代上限警告比例
        "token_budget_ratio": 0.9,        # Token 预算警告比例
        "stuck_iterations": 3,            # 无进展迭代阈值
        "repetition_threshold": 3,         # 重复调用阈值
        "ema_alpha": 0.3,                  # EMA 平滑因子
        "plateau_stuck_limit": 5,          # 高原期最大停滞次数
        "trace_enabled": False,            # 决策追踪开关
        "trace_dir": None,                 # 追踪文件目录
    }

    @classmethod
    def load(cls) -> "HeuristicConfig":
        """
        从 AgentConfig 单例加载配置

        配置文件：config/agent/heuristics.yaml
        热重载：支持（通过 AgentConfig.reload() 触发）
        """
        try:
            from app.core.util.agent_config import get_config
            config = get_config()
            data = config.get_section("heuristics")

            if not data:
                logger.info("[HeuristicConfig] 配置段为空，使用默认配置")
                return cls._create_default()

            heuristic_config = cls._parse_config(data)
            logger.info("[HeuristicConfig] 从 AgentConfig 加载成功")
            return heuristic_config

        except Exception as e:
            logger.warning("[HeuristicConfig] 加载失败，使用默认配置: %s", e)
            return cls._create_default()

    # 别名，保持兼容
    from_agent_config = load

    @classmethod
    def _create_default(cls) -> "HeuristicConfig":
        return cls(
            enabled=True,
            log_level="info",
            trace_enabled=False,
            thresholds=cls.DEFAULT_THRESHOLDS.copy(),
            rules={},
        )

    @classmethod
    def _parse_config(cls, data: Dict[str, Any]) -> "HeuristicConfig":
        thresholds = cls.DEFAULT_THRESHOLDS.copy()
        if "thresholds" in data:
            thresholds.update(data["thresholds"])

        rules = {}
        if "rules" in data:
            for rule_id, rule_data in data["rules"].items():
                if isinstance(rule_data, dict):
                    rules[rule_id] = RuleConfig(
                        enabled=rule_data.get("enabled", True),
                        threshold=rule_data.get("threshold"),
                        params=rule_data.get("params", {}),
                    )

        return cls(
            enabled=data.get("enabled", True),
            log_level=data.get("log_level", "info"),
            trace_enabled=data.get("trace_enabled", False),
            thresholds=thresholds,
            rules=rules,
        )

    def get_threshold(self, key: str, default: float = None) -> float:
        return self.thresholds.get(key, default if default is not None else self.DEFAULT_THRESHOLDS.get(key, 0))

    def get_rule_config(self, rule_id: str) -> RuleConfig:
        return self.rules.get(rule_id, RuleConfig())
