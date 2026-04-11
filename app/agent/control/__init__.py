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
]
