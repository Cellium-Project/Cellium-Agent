# -*- coding: utf-8 -*-
"""
启发式模块 - 数据类型定义
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Any, Optional


class DecisionPoint(Enum):
    """决策点类型"""
    ITERATION_TERMINATION = "iteration_termination"   # 迭代终止判断
    TOOL_SELECTION = "tool_selection"                
    LOOP_DETECTION = "loop_detection"                


class DecisionAction(Enum):
    """决策动作"""
    CONTINUE = "continue"       # 继续执行
    STOP = "stop"               # 终止迭代
    REDIRECT = "redirect"       
    RECOMMEND = "recommend"     # 推荐（工具选择）
    WARN = "warn"               # 警告（但不阻止）


class RulePriority(Enum):
    """规则优先级"""
    CRITICAL = 100
    HIGH = 80
    MEDIUM = 50
    LOW = 30


@dataclass
class Decision:
    """单个决策结果"""
    point: DecisionPoint
    action: DecisionAction
    confidence: float         
    reason: str
    rule_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class FusedDecision:
    """融合后的决策（多规则合并）"""
    action: DecisionAction
    confidence: float
    reasons: List[str] = field(default_factory=list)              
    contributing_rules: List[str] = field(default_factory=list)  
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleEvaluationResult:
    """规则评估结果"""
    matched: bool
    action: DecisionAction
    score: float = 0.0          
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def not_matched(cls) -> "RuleEvaluationResult":
        """返回未匹配的结果"""
        return cls(matched=False, action=DecisionAction.CONTINUE)


@dataclass
class EvaluationContext:
    """评估上下文"""
    session_id: str
    iteration: int
    max_iterations: int
    recent_tool_calls: List[Dict] = field(default_factory=list)
    tool_call_history: List[Dict] = field(default_factory=list)
    available_tools: List[str] = field(default_factory=list)
    total_tokens_used: int = 0
    token_budget: int = 200000
    elapsed_ms: int = 0
    user_input: str = ""
    last_tool_result: Optional[Dict] = None
    # ★ 新增：最近 LLM 输出（用于检测重复循环）
    recent_llm_outputs: List[str] = field(default_factory=list)


@dataclass
class DerivedFeatures:
    """派生特征（从 EvaluationContext 计算）"""

    # ===== 进度特征 =====
    progress_score: float = 0.0          # 任务完成进度估计 (0-1)
    stuck_iterations: int = 0            # 连续无进展的迭代次数
    convergence_rate: float = 0.0        # 收敛速度

    # ===== 趋势特征=====
    progress_trend: float = 0.0          # EMA 平滑后的趋势 (-1 到 1)
    progress_trend_raw: float = 0.0      # 原始趋势（未平滑，用于调试）
    trend_confidence: float = 0.0        # 趋势置信度（基于回归 R²）
    is_making_progress: bool = True      # 综合判断：是否在取得进展
    is_plateau: bool = False            

    # ===== 工具调用特征 =====
    unique_tools_used: int = 0            # 使用过的不同工具数
    tool_diversity_score: float = 0.0     # 工具多样性分数
    dominant_tool_ratio: float = 0.0      # 最多使用的工具占比
    tool_call_velocity: float = 0.0       # 单位时间调用数

    # ===== 循环检测特征 =====
    repetition_score: float = 0.0         # 重复调用分数
    pattern_detected: str = ""            # 检测到的模式: "cycle" / "repetition" / ""
    pattern_cycle_length: int = 0         # 循环周期长度

    # ===== 结果质量特征 =====
    error_rate: float = 0.0               # 错误率
    empty_result_rate: float = 0.0        # 空结果率
    avg_result_size: float = 0.0          # 平均结果大小
    result_growth_rate: float = 0.0       # 结果增长率
    result_quality_score: float = 0.0     # ★ 新增：结果质量综合分数

    # ===== 上下文特征 =====
    context_saturation: float = 0.0       # 上下文饱和度 (已用/budget)
    message_turn_ratio: float = 0.0       # 消息数/迭代数

    # ===== 时序特征 =====
    time_per_iteration: float = 0.0       # 每次迭代平均耗时(ms)

    # ===== LLM 输出重复检测 =====
    exact_repetition_count: int = 0       # 连续完全相同的输出次数
    is_output_loop: bool = False          # 是否陷入输出循环（5+次重复）
