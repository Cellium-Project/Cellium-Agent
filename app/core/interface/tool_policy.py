# -*- coding: utf-8 -*-

from typing import Dict, Any, FrozenSet, Optional

MODE_BLACKLIST = "blacklist"
MODE_WHITELIST = "whitelist"

DEFAULT_BLOCKED_TOOLS: FrozenSet[str] = frozenset({
    "shell", "file", "edit", "read", "ls", "glob", "grep", "memory",
    "config", "web_search", "web_fetch", "scheduler",
    "component", "skill_installer", "skill_manager", "sub_agent",
    "qq_files", "weixin_files", "telegram_files", "feishu_files",
})

class ToolPolicy:
    """blacklist=拒绝列表内的，whitelist=只放行列表内的"""

    def __init__(self, mode: str = MODE_BLACKLIST, tools=None):
        self.mode = mode
        if mode == MODE_WHITELIST:
            self.tools = frozenset() if tools is None else frozenset(tools)
        else:
            # 默认黑名单兜底，组件自定义列表与之合并
            self.tools = DEFAULT_BLOCKED_TOOLS if tools is None else frozenset(tools) | DEFAULT_BLOCKED_TOOLS

    def allows(self, tool_name: str) -> bool:
        if self.mode == MODE_WHITELIST:
            return tool_name in self.tools
        return tool_name not in self.tools

    def block_reason(self, tool_name: str) -> str:
        if self.mode == MODE_WHITELIST:
            return f"权限不足，当前会话仅允许使用: {', '.join(sorted(self.tools)) or '无'}"
        return "权限不足，当前会话禁止使用工具"

    @classmethod
    def from_context(cls, platform_context: Optional[Dict[str, Any]]) -> Optional["ToolPolicy"]:
        """没配策略就返回 None，不限制"""
        if not platform_context:
            return None
        raw = platform_context.get("tool_policy")
        if isinstance(raw, dict):
            return cls(mode=raw.get("mode", MODE_BLACKLIST), tools=raw.get("tools"))
        if isinstance(raw, ToolPolicy):
            return raw
        if platform_context.get("tool_allowed") is False:
            return cls()
        return None


def apply_component_tool_policy(platform_context: Optional[Dict[str, Any]], source_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """把组件声明的 TRIGGER_TOOL_POLICY 合并进 platform_context。

    组件类上定义 TRIGGER_TOOL_POLICY = {"mode": "blacklist", "tools": [...]}，
    经该组件触发的 Agent 任务自动携带此策略（调用方显式传入的 tool_policy 优先）。
    """
    if not source_name:
        return platform_context
    try:
        from app.core.util.components_loader import get_cell
        cell = get_cell(source_name)
        declared = getattr(cell, "TRIGGER_TOOL_POLICY", None) if cell else None
    except Exception:
        declared = None
    if isinstance(declared, dict) and (not platform_context or not platform_context.get("tool_policy")):
        platform_context = dict(platform_context or {})
        platform_context["tool_policy"] = declared
    return platform_context

class CommandPolicy:

    @staticmethod
    def is_admin(cell, platform_context: Optional[Dict[str, Any]]) -> bool:
        admin_check = getattr(cell, "is_policy_admin", None)
        if not callable(admin_check):
            return False
        try:
            return bool(admin_check(platform_context))
        except Exception:
            return False

    @staticmethod
    def check(cell, command: str, platform_context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """拒绝原因，放行返回 None"""
        policy_map = getattr(cell, "TOOL_POLICY", None) or {}
        policy = policy_map.get(command)
        if policy is None:
            policy = getattr(cell, "TOOL_POLICY_DEFAULT", "allow")
        if policy == "deny":
            return f"命令 {command} 已被禁用"
        if policy == "admin":
            if CommandPolicy.is_admin(cell, platform_context):
                return None
            return f"命令 {command} 仅管理员可用"
        if isinstance(policy, dict):
            mode = policy.get("mode", MODE_BLACKLIST)
            users = {str(u) for u in policy.get("users", [])}
            caller = str((platform_context or {}).get("user_id") or "")
            if mode == MODE_WHITELIST:
                if caller and caller in users:
                    return None
                return f"命令 {command} 仅限指定用户使用"
            if caller and caller in users:
                return f"命令 {command} 对你禁用"
            return None
        return None
