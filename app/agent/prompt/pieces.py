# -*- coding: utf-8 -*-
"""
预定义提示词拼图块
"""

import os
import platform
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from app.agent.prompt.piece import PromptPiece

if TYPE_CHECKING:
    from app.agent.prompt.builder import PromptBuilder


# ============================================================
# Helpers
# ============================================================

def _get_current_date() -> str:
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return f"{now.year}年{now.month}月{now.day}号 {weekdays[now.weekday()]}"


def _get_system_info() -> str:
    system = platform.system()
    machine = platform.machine()
    if system == "Windows":
        shell = "PowerShell"
    elif system == "Darwin":
        shell = "zsh/bash"
    else:
        shell = "bash"
    return f"{system} {machine} | {shell}"


def _read_personality(memory_dir: str = "memory") -> str:
    if not isinstance(memory_dir, str):
        return DEFAULT_IDENTITY
    personality_path = os.path.join(memory_dir, "personality.md")
    if os.path.exists(personality_path):
        try:
            with open(personality_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return DEFAULT_IDENTITY


DEFAULT_IDENTITY = """# Cellium Agent

你是一个专业的桌面助手，擅长：
- 执行系统命令和脚本
- 读写文件和管理项目
- 回答技术问题
- 协助开发和调试

"""


# ============================================================
# 静态层 — role: system，永远不变
# ============================================================

def get_identity_piece(memory_dir: str = "memory") -> PromptPiece:
    personality = _read_personality(memory_dir)

    return PromptPiece(
        name="identity",
        content=personality,
        stability="static",
        priority=0,
        role="system",
    )




# ============================================================
# 日更层 — role: user，至少每天才变一次
# ============================================================

def get_context_piece() -> PromptPiece:
    context_lines = [
        f"**当前日期**: {_get_current_date()}",
        f"**系统环境**: {_get_system_info()}",
    ]
    content = "<system-reminder>\n[上下文信息]\n" + "\n".join(context_lines) + "\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>"

    return PromptPiece(
        name="context",
        content=content,
        stability="daily",
        priority=550,
    )


# ============================================================
# 会话层 — role: user，同一会话内不变
# ============================================================

def get_long_term_memory_piece() -> PromptPiece:
    return PromptPiece(
        name="long_term_memory",
        template="<system-reminder>\n[长期记忆检索结果]\n{{ long_term_results }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: not ctx.get('_flash_mode', False) and bool(ctx.get('long_term_results')),
        stability="dynamic",
        priority=800,
    )


# ============================================================
# 动态层 — role: user，每次请求都可能变化
# ============================================================

def get_user_input_piece() -> PromptPiece:
    return PromptPiece(
        name="user_input",
        template="{{ user_input }}",
        condition=lambda ctx: ctx.get('_flash_mode', False) and ctx.get('_is_first_round', False) and not ctx.get('session_messages'),
        stability="dynamic",
        priority=300,
    )



def get_system_injection_piece() -> PromptPiece:
    """
    系统指令注入（来自控制环 Gene）。
    """
    return PromptPiece(
        name="system_injection",
        template="<system-reminder>\n[系统指令]\n{{ system_injection }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('system_injection')),
        stability="dynamic",
        priority=400,
    )


def get_runtime_status_piece() -> PromptPiece:
    """
    运行时状态摘要（来自 LoopState）。
    """
    return PromptPiece(
        name="runtime_status",
        template="<system-reminder>\n[运行时状态]\n{{ runtime_status }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('runtime_status')),
        stability="dynamic",
        priority=500,
    )


def get_plan_summary_piece() -> PromptPiece:
    """
    当前计划执行进度摘要（来自 HybridController）。
    """
    return PromptPiece(
        name="plan_summary",
        template="<system-reminder>\n[当前计划]\n{{ plan_summary }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('plan_summary')),
        stability="dynamic",
        priority=550,
    )


def get_guidance_message_piece() -> PromptPiece:
    """
    系统引导消息（来自启发式模块 / 控制环）。
    使用 <system-reminder> 标签包装，避免 LLM 自言自语。
    """
    return PromptPiece(
        name="guidance_message",
        template="<system-reminder>\n{{ guidance_message }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('guidance_message')),
        stability="dynamic",
        priority=600,
    )


def get_auto_hints_piece() -> PromptPiece:
    """
    工具使用提示。使用 <system-reminder> 标签包装。
    """
    return PromptPiece(
        name="auto_hints",
        template="<system-reminder>\n{{ auto_hints }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('auto_hints')),
        stability="dynamic",
        priority=350,
    )


# ============================================================
# 工厂函数
# ============================================================

def create_default_builder(memory_dir: str = "memory") -> "PromptBuilder":
    from app.agent.prompt.builder import PromptBuilder

    builder = PromptBuilder()

    # static
    builder.register(get_identity_piece(memory_dir))

    # daily
    builder.register(get_context_piece())

    # session
    builder.register(get_long_term_memory_piece())

    # dynamic（按 priority 排序 → 固定顺序）
    builder.register(get_user_input_piece())
    builder.register(get_system_injection_piece())
    builder.register(get_runtime_status_piece())
    builder.register(get_plan_summary_piece())
    builder.register(get_guidance_message_piece())
    builder.register(get_auto_hints_piece())

    return builder
