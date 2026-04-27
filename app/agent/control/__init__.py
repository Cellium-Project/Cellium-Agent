# -*- coding: utf-8 -*-
"""
Control Loop Harness - LLM 控制环

核心组件：
  - LoopState: 控制环状态快照
  - ControlDecision: Action-based 决策
  - FeedbackEvaluator: 分段式反馈评估
  - ActionBandit: Action 选择器
  - ControlLoop: 统一控制入口
  - DecisionRenderer: 软约束渲染器（默认）
  - HardConstraintRenderer: 强约束渲染器（PromptBuilder v3）
  - ThoughtParser: 思考解析器
  - HybridController: Plan-Execute-Observe-RePlan 混合控制器

Gene 相关（constraint_gene 子模块）：
  - TaskSignalMatcher: 任务信号匹配器
  - GeneEvolution: Gene 进化系统
  - GeneComposer: Gene 组合器
"""

from .loop_state import LoopState, ControlDecision
from .feedback_evaluator import FeedbackEvaluator
from .action_bandit import ActionBandit
from .control_loop import ControlLoop, create_control_loop
from .decision_renderer import DecisionRenderer, RenderedPrompt
from .hard_constraints import (
    HardConstraint,
    HardConstraintRenderer,
    HardConstraintTemplates,
    ActionFusion,
)
from .thought_parser import (
    ThoughtParser,
    ParsedThought,
    ThoughtStep,
    ActionType,
    THOUGHT_SCHEMA,
)
from .hybrid_controller import (
    HybridController,
    HybridPhase,
    HybridState,
    Observation,
    create_hybrid_controller,
)

from .constraint_gene import TaskSignalMatcher, GeneEvolution, GeneComposer

__all__ = [
    "LoopState",
    "ControlDecision",
    "FeedbackEvaluator",
    "ActionBandit",
    "ControlLoop",
    "create_control_loop",
    "DecisionRenderer",
    "RenderedPrompt",
    # 强约束版
    "HardConstraint",
    "HardConstraintRenderer",
    "HardConstraintTemplates",
    "ActionFusion",
    # 思考系统
    "ThoughtParser",
    "ParsedThought",
    "ThoughtStep",
    "ActionType",
    "THOUGHT_SCHEMA",
    # Hybrid 控制器
    "HybridController",
    "HybridPhase",
    "HybridState",
    "Observation",
    "create_hybrid_controller",
    # Gene 相关
    "TaskSignalMatcher",
    "GeneEvolution",
    "GeneComposer",
]
