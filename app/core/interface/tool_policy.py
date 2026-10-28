# -*- coding: utf-8 -*-

from typing import Dict, Any, FrozenSet, Optional

MODE_BLACKLIST = "blacklist"
MODE_WHITELIST = "whitelist"

DEFAULT_BLOCKED_TOOLS: FrozenSet[str] = frozenset({
    "shell", "file", "edit", "read", "ls", "glob", "grep", "memory",
    "config", "web_search", "web_fetch", "scheduler",
    "napcat.add_user", "napcat.remove_user", "napcat.add_group", "napcat.remove_group",
    "napcat.subscribe", "napcat.unsubscribe",
    "napcat.set_bot_qq", "napcat.set_port",
    "napcat.enable_user_whitelist", "napcat.disable_user_whitelist",
    "napcat.enable_group_whitelist", "napcat.disable_group_whitelist",
    "napcat.enable_at", "napcat.disable_at",
    "napcat.enable_proactive", "napcat.disable_proactive",
    "napcat.set_proactive_threshold", "napcat.set_proactive_interval",
    "napcat.add_wake_word", "napcat.remove_wake_word", "napcat.list_wake_words",
    "component.reload", "component.load", "component.unload",
    "component.discover", "component.scan",
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
