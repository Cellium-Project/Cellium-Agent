# -*- coding: utf-8 -*-
"""
LoopState - 控制环状态管理

记录每一轮的完整状态，用于：
  - 控制决策
  - 反馈评估
  - 回放调试
  - 离线学习
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ControlDecision:
    """
    控制决策 - Action-based

    核心变化：
      - 不是 policy 字符串，而是 action_type
      - 包含参数化决策（为中期演进准备）
    """

    # ★ 核心维度：Action 类型
    action_type: str = "continue"
    # 可选值: "continue" | "redirect" | "compress" | "terminate"

    # 终止决策
    should_stop: bool = False
    stop_reason: Optional[str] = None

    # 引导决策
    enable_redirect_guidance: bool = False
    guidance_message: Optional[str] = None
    suggested_tools: List[str] = field(default_factory=list)

    # 上下文决策
    enable_long_memory: bool = True
    force_memory_compact: bool = False
    context_trim_level: str = "normal"  # normal / aggressive / minimal

    # ★ 参数化决策（替代 policy 字符串，为中期演进准备）
    params: Dict[str, Any] = field(default_factory=dict)
    # 示例:
    # {
    #     "stuck_threshold": 3,
    #     "repetition_threshold": 3,
    #     "compress_ratio": 0.8,
    # }

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "action_type": self.action_type,
            "should_stop": self.should_stop,
            "stop_reason": self.stop_reason,
            "enable_redirect_guidance": self.enable_redirect_guidance,
            "enable_long_memory": self.enable_long_memory,
            "force_memory_compact": self.force_memory_compact,
            "context_trim_level": self.context_trim_level,
            "params": self.params,
        }


@dataclass
class LoopState:
    """
    控制环状态 - 每一轮的完整快照

    设计原则：
      1. 完整记录：便于回放和调试
      2. 不可变优先：修改时创建新状态（可选）
      3. 可追溯：decision_trace 记录完整决策历史
    """

    # ===== 基础状态 =====
    iteration: int = 0
    max_iterations: int = 10
    session_id: str = "default"

    # ===== 执行状态 =====
    tool_traces: List[Dict] = field(default_factory=list)
    last_tool_result: Optional[Dict] = None
    last_error: Optional[str] = None

    # ===== 资源状态 =====
    tokens_used: int = 0
    token_budget: int = 200000
    elapsed_ms: int = 0

    # ===== 特征状态（来自 HeuristicEngine） =====
    # 运行时动态注入
    features: Optional[Any] = None  # DerivedFeatures

    # ===== 决策轨迹（用于回放和离线学习） =====
    decision_trace: List[ControlDecision] = field(default_factory=list)

    # ===== 反馈状态 =====
    cumulative_reward: float = 0.0
    round_reward: float = 0.0

    # ===== 用户输入（用于上下文） =====
    user_input: str = ""
    available_tools: List[str] = field(default_factory=list)

    # ===== LLM 输出追踪（用于检测重复循环） =====
    recent_llm_outputs: List[str] = field(default_factory=list)
    # 存储最近 10 轮的 LLM 输出内容，用于检测一字不差的重复

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于持久化）"""
        return {
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "session_id": self.session_id,
            "tool_traces_count": len(self.tool_traces),
            "tokens_used": self.tokens_used,
            "token_budget": self.token_budget,
            "elapsed_ms": self.elapsed_ms,
            "decision_count": len(self.decision_trace),
            "cumulative_reward": self.cumulative_reward,
            "round_reward": self.round_reward,
            "llm_outputs_count": len(self.recent_llm_outputs),
        }

    def get_last_decision(self) -> Optional[ControlDecision]:
        """获取最后一个决策"""
        if self.decision_trace:
            return self.decision_trace[-1]
        return None

    def get_decision_summary(self) -> Dict[str, int]:
        """获取决策统计摘要"""
        summary = {}
        for d in self.decision_trace:
            summary[d.action_type] = summary.get(d.action_type, 0) + 1
        return summary
