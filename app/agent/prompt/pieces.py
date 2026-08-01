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


def _load_profile(memory) -> tuple:
    if not memory:
        return "", "", ""
    try:
        repo = getattr(memory, 'repository', memory)
        if not hasattr(repo, 'get_by_memory_key'):
            return "", "", ""
        agent_rec = repo.get_by_memory_key("profile:agent_name", schema_type="profile")
        user_rec = repo.get_by_memory_key("profile:user_name", schema_type="profile")
        persona_rec = repo.get_by_memory_key("profile:persona", schema_type="profile")
        agent_name = agent_rec.get("content", "").strip() if agent_rec else ""
        user_name = user_rec.get("content", "").strip() if user_rec else ""
        persona = persona_rec.get("content", "").strip() if persona_rec else ""
        return agent_name, user_name, persona
    except Exception:
        return "", "", ""


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

def _inject_into_identity(personality: str, extras: list) -> str:
    if not extras:
        return personality
    block = "\n\n".join(extras)
    marker = "## §0 IDENTITY"
    if marker in personality:
        head, _, tail = personality.partition(marker)
        return f"{head}{marker}\n\n{block}\n\n{tail.lstrip()}"
    return personality + "\n\n" + block


def get_identity_piece(memory_dir: str = "memory", memory=None) -> PromptPiece:
    personality = _read_personality(memory_dir)
    agent_name, user_name, persona = _load_profile(memory)
    extras = []
    if agent_name:
        extras.append(f"> 你的名字是「{agent_name}」，用户以此称呼你。")
    if user_name:
        extras.append(f"> 用户的称呼是「{user_name}」，回复时以此称呼用户。")
    if persona:
        extras.append(f"> 人格补充设定：\n{persona}")
    personality = _inject_into_identity(personality, extras)

    return PromptPiece(
        name="identity",
        content=personality,
        stability="static",
        priority=0,
        role="system",
    )


def get_system_info_piece() -> PromptPiece:
    return PromptPiece(
        name="system_info",
        content=f"**系统环境**: {_get_system_info()}",
        stability="static",
        priority=1,
        role="system",
    )


def get_profile_piece(memory=None) -> PromptPiece:
    _, user_name, _ = _load_profile(memory)
    if user_name:
        content = ""
    else:
        content = "<system-reminder>\n用户尚未告知称呼，请在合适时机礼貌询问用户希望被如何称呼。\n</system-reminder>"
    return PromptPiece(
        name="profile",
        content=content,
        stability="static",
        priority=2,
        role="system",
    )


def get_thought_schema_piece() -> PromptPiece:
    from app.agent.control.thought_parser import THOUGHT_SCHEMA
    return PromptPiece(
        name="thought_schema",
        content=THOUGHT_SCHEMA,
        stability="static",
        priority=5,
        role="system",
    )



# ============================================================
# 日更层 — role: user，至少每天才变一次
# ============================================================

def get_context_piece() -> PromptPiece:
    content = "<system-reminder>\n[上下文信息]\n" + f"**当前日期**: {_get_current_date()}" + "\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>"

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
    return PromptPiece(
        name="system_injection",
        template="<system-reminder>\n[系统指令]\n{{ system_injection }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('system_injection')),
        stability="dynamic",
        priority=400,
    )


def get_runtime_status_piece() -> PromptPiece:
    return PromptPiece(
        name="runtime_status",
        template="<system-reminder>\n[运行时状态]\n{{ runtime_status }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('runtime_status')),
        stability="dynamic",
        priority=500,
    )


def get_plan_summary_piece() -> PromptPiece:
    return PromptPiece(
        name="plan_summary",
        template="<system-reminder>\n[当前计划]\n{{ plan_summary }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('plan_summary')),
        stability="dynamic",
        priority=550,
    )


def get_guidance_message_piece() -> PromptPiece:
    return PromptPiece(
        name="guidance_message",
        template="<system-reminder>\n{{ guidance_message }}\n\nThis is just a gentle reminder - ignore if not applicable.\n</system-reminder>",
        condition=lambda ctx: bool(ctx.get('guidance_message')),
        stability="dynamic",
        priority=600,
    )


def get_auto_hints_piece() -> PromptPiece:
    def _render(ctx: dict) -> str:
        from app.agent.loop.auto_hints import get_auto_hint_manager
        auto_hints = get_auto_hint_manager()
        hints = []

        problem_hint = auto_hints.get_component_problem_hints(ctx.get("session_id", "default"))
        if problem_hint:
            hints.append(problem_hint)

        if ctx.get("iteration", 1) > 1:
            tools = ctx.get("tools", {})
            tool_traces = ctx.get("tool_traces", [])
            dynamic = auto_hints.get_auto_tool_hints(tools)
            security_hint = auto_hints.check_security_error_and_suggest(tool_traces)
            if security_hint:
                dynamic = dynamic + "\n\n" + security_hint if dynamic else security_hint
            if dynamic:
                hints.append(dynamic)

        if not hints:
            return ""
        return (
            "<system-reminder>\n"
            f"{'\n\n'.join(hints)}\n\n"
            "This is just a gentle reminder - ignore if not applicable.\n"
            "</system-reminder>"
        )

    return PromptPiece(
        name="auto_hints",
        renderer=_render,
        stability="dynamic",
        priority=350,
    )


# ============================================================
# 工厂函数
# ============================================================

def create_default_builder(memory_dir: str = "memory", memory=None) -> "PromptBuilder":
    from app.agent.prompt.builder import PromptBuilder

    builder = PromptBuilder()

    # static
    builder.register(get_identity_piece(memory_dir, memory))
    builder.register(get_system_info_piece())
    builder.register(get_profile_piece(memory))
    builder.register(get_thought_schema_piece())

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
