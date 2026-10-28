# -*- coding: utf-8 -*-
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.core.interface.tool_policy import (
    ToolPolicy, CommandPolicy, DEFAULT_BLOCKED_TOOLS, MODE_BLACKLIST, MODE_WHITELIST,
)
from app.agent.loop.tool_executor import ToolExecutor


class TestToolPolicy(unittest.TestCase):

    def test_default_blacklist(self):
        p = ToolPolicy()
        self.assertEqual(p.mode, MODE_BLACKLIST)
        self.assertFalse(p.allows("shell"))
        self.assertFalse(p.allows("napcat.set_bot_qq"))
        self.assertTrue(p.allows("web_query"))
        self.assertFalse(p.allows("scheduler"))

    def test_custom_blacklist_merges_default(self):
        p = ToolPolicy(mode=MODE_BLACKLIST, tools={"web_query"})
        self.assertFalse(p.allows("web_query"))
        self.assertFalse(p.allows("shell"))
        self.assertFalse(p.allows("memory"))
        self.assertTrue(p.allows("custom_extra_tool"))

    def test_whitelist(self):
        p = ToolPolicy(mode=MODE_WHITELIST, tools={"web_query", "memory"})
        self.assertTrue(p.allows("web_query"))
        self.assertTrue(p.allows("memory"))
        self.assertFalse(p.allows("shell"))
        self.assertFalse(p.allows("scheduler"))

    def test_from_context_none(self):
        self.assertIsNone(ToolPolicy.from_context(None))
        self.assertIsNone(ToolPolicy.from_context({}))
        self.assertIsNone(ToolPolicy.from_context({"platform": "telegram"}))

    def test_from_context_legacy_tool_allowed(self):
        p = ToolPolicy.from_context({"tool_allowed": False})
        self.assertIsNotNone(p)
        self.assertFalse(p.allows("shell"))
        self.assertFalse(p.allows("scheduler"))
        self.assertIsNone(ToolPolicy.from_context({"tool_allowed": True}))
        self.assertIsNone(ToolPolicy.from_context({"tool_allowed": None}))

    def test_from_context_tool_policy_dict(self):
        p = ToolPolicy.from_context({"tool_policy": {"mode": "whitelist", "tools": ["web_query"]}})
        self.assertEqual(p.mode, MODE_WHITELIST)
        self.assertTrue(p.allows("web_query"))
        self.assertFalse(p.allows("shell"))

        p2 = ToolPolicy.from_context({"tool_policy": {"tools": ["shell"]}})
        self.assertEqual(p2.mode, MODE_BLACKLIST)
        self.assertFalse(p2.allows("shell"))
        self.assertFalse(p2.allows("file"))

    def test_from_context_tool_policy_object(self):
        original = ToolPolicy(mode=MODE_WHITELIST, tools={"a"})
        p = ToolPolicy.from_context({"tool_policy": original})
        self.assertIs(p, original)


class TestExecutorPolicyCheck(unittest.TestCase):

    def _executor(self):
        return ToolExecutor(tools={}, builtin_tools={})

    def _call(self, name):
        return SimpleNamespace(name=name, arguments={})

    def test_legacy_blacklist_blocks_both_paths(self):
        ex = self._executor()
        ctx = {"tool_allowed": False}
        r1 = ex._check_policy("shell", ctx, "s1")
        self.assertIsNotNone(r1)
        self.assertTrue(r1["_tool_blocked"])
        r2 = ex._check_policy("web_query", ctx, "s1")
        self.assertIsNone(r2)

    def test_context_whitelist_blocks_unlisted(self):
        ex = self._executor()
        ctx = {"tool_policy": {"mode": "whitelist", "tools": ["memory"]}}
        self.assertIsNotNone(ex._check_policy("shell", ctx, "s1"))
        self.assertIsNone(ex._check_policy("memory", ctx, "s1"))

    def test_instance_policy_used_when_context_empty(self):
        ex = self._executor()
        ex.set_tool_policy(ToolPolicy(mode=MODE_WHITELIST, tools={"memory"}))
        self.assertIsNone(ex._check_policy("memory", {}, "s1"))
        self.assertIsNotNone(ex._check_policy("shell", {}, "s1"))

    def test_context_policy_overrides_instance(self):
        ex = self._executor()
        ex.set_tool_policy(ToolPolicy(mode=MODE_BLACKLIST, tools={"memory"}))
        ctx = {"tool_policy": {"mode": "whitelist", "tools": ["memory", "shell"]}}
        self.assertIsNone(ex._check_policy("memory", ctx, "s1"))
        self.assertIsNone(ex._check_policy("shell", ctx, "s1"))

    def test_no_policy_no_block(self):
        ex = self._executor()
        self.assertIsNone(ex._check_policy("shell", {}, "s1"))
        self.assertIsNone(ex._check_policy("shell", None, "s1"))

    def test_whitelist_block_reason_lists_allowed(self):
        ex = self._executor()
        ctx = {"tool_policy": {"mode": "whitelist", "tools": ["memory"]}}
        r = ex._check_policy("shell", ctx, "s1")
        self.assertIn("memory", r["error"])

    def test_command_level_blacklist(self):
        ex = self._executor()
        ctx = {"tool_policy": {"mode": "blacklist", "tools": ["custom_util.reset"]}}
        self.assertIsNotNone(ex._check_policy("custom_util", ctx, "s1", {"command": "reset"}))
        self.assertIsNone(ex._check_policy("custom_util", ctx, "s1", {"command": "list"}))
        self.assertIsNone(ex._check_policy("custom_util", ctx, "s1"))
        self.assertIsNotNone(ex._check_policy("shell", ctx, "s1"))

    def test_command_level_whitelist(self):
        ex = self._executor()
        ctx = {"tool_policy": {"mode": "whitelist", "tools": ["config.list_models"]}}
        self.assertIsNone(ex._check_policy("config", ctx, "s1", {"command": "list_models"}))
        self.assertIsNotNone(ex._check_policy("config", ctx, "s1", {"command": "switch_model"}))
        self.assertIsNotNone(ex._check_policy("tool_name_only", ctx, "s1"))

    def test_tool_level_blacklist_covers_commands(self):
        ex = self._executor()
        ctx = {"tool_policy": {"mode": "blacklist", "tools": ["config"]}}
        self.assertIsNotNone(ex._check_policy("config", ctx, "s1", {"command": "list_models"}))
        self.assertIsNotNone(ex._check_policy("config", ctx, "s1", {"command": "switch_model"}))

    def test_scheduler_blocked_by_default(self):
        ex = self._executor()
        ctx = {"tool_allowed": False}
        self.assertIsNotNone(ex._check_policy("scheduler", ctx, "s1", {"command": "create_daily"}))
        self.assertIsNotNone(ex._check_policy("scheduler", ctx, "s1"))


class TestCellToolPolicy(unittest.TestCase):
    """组件级命令策略（组件自带 TOOL_POLICY，不改基类）"""

    def _make_cell(self, policy, admin_users=frozenset()):
        class _Dummy:
            TOOL_POLICY = dict(policy)
            _admin_users = set(admin_users)

            def is_policy_admin(self, platform_context):
                user_id = str((platform_context or {}).get("user_id") or "")
                return bool(user_id) and user_id in self._admin_users

            def get_commands(self):
                return {"add_user": ""}

            def execute_with_context(self, arguments, session_id=None, platform_context=None):
                return {"success": True, "added": arguments.get("user_id")}

        return _Dummy()

    def test_no_policy_allows(self):
        cell = self._make_cell({})
        self.assertIsNone(CommandPolicy.check(cell, "anything", {"user_id": "1"}))

    def test_deny_blocks_everyone(self):
        cell = self._make_cell({"export": "deny"})
        self.assertIn("已被禁用", CommandPolicy.check(cell, "export", {"user_id": "1"}))
        self.assertIn("已被禁用", CommandPolicy.check(cell, "export", None))

    def test_admin_allows_and_blocks(self):
        cell = self._make_cell({"add_user": "admin"}, admin_users={"888"})
        self.assertIsNone(CommandPolicy.check(cell, "add_user", {"user_id": "888"}))
        self.assertIn("仅管理员", CommandPolicy.check(cell, "add_user", {"user_id": "123"}))
        self.assertIn("仅管理员", CommandPolicy.check(cell, "add_user", None))

    def test_admin_via_sender_role(self):
        class _RoleCell:
            TOOL_POLICY = {"kick": "admin"}

            def is_policy_admin(self, platform_context):
                return str((platform_context or {}).get("sender_role") or "") in ("owner", "admin")

        cell = _RoleCell()
        self.assertIsNone(CommandPolicy.check(cell, "kick", {"user_id": "1", "sender_role": "admin"}))
        self.assertIsNotNone(CommandPolicy.check(cell, "kick", {"user_id": "1", "sender_role": "member"}))

    def test_per_user_blacklist_whitelist(self):
        cell = self._make_cell({
            "cmd_b": {"mode": "blacklist", "users": ["123"]},
            "cmd_w": {"mode": "whitelist", "users": ["456"]},
        })
        self.assertIn("对你禁用", CommandPolicy.check(cell, "cmd_b", {"user_id": "123"}))
        self.assertIsNone(CommandPolicy.check(cell, "cmd_b", {"user_id": "789"}))
        self.assertIsNone(CommandPolicy.check(cell, "cmd_w", {"user_id": "456"}))
        self.assertIn("仅限指定用户", CommandPolicy.check(cell, "cmd_w", {"user_id": "789"}))
        self.assertIn("仅限指定用户", CommandPolicy.check(cell, "cmd_w", None))

    def test_default_deny_mode(self):
        class _Strict:
            TOOL_POLICY_DEFAULT = "deny"
            TOOL_POLICY = {"send_msg": "allow", "status": "allow"}

        cell = _Strict()
        self.assertIsNone(CommandPolicy.check(cell, "send_msg", None))
        self.assertIsNone(CommandPolicy.check(cell, "status", None))
        self.assertIn("已被禁用", CommandPolicy.check(cell, "unknown_cmd", None))

    def test_adapter_enforces_cell_policy(self):
        from app.core.util.cell_tool_adapter import CellToolAdapter

        cell = self._make_cell({"add_user": "admin"}, admin_users={"888"})
        adapter = CellToolAdapter(cell, use_sandbox=False)

        blocked = adapter.execute_with_context(
            {"command": "add_user", "user_id": "999"}, platform_context={"user_id": "123"})
        self.assertTrue(blocked.get("_tool_blocked"))
        self.assertIn("仅管理员", blocked.get("error", ""))

        allowed = adapter.execute_with_context(
            {"command": "add_user", "user_id": "999"}, platform_context={"user_id": "888"})
        self.assertFalse(allowed.get("_tool_blocked", False))


if __name__ == "__main__":
    unittest.main()
