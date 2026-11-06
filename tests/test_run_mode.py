# -*- coding: utf-8 -*-
"""Plan / Run 模式：白名单、工具裁剪与执行拦截"""
import asyncio
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security.run_mode import (
    is_verify_command, normalize_mode, plan_block_reason, plan_injection, run_injection,
)


class TestVerifyCommand(unittest.TestCase):

    def test_allowed(self):
        for cmd in [
            "pytest -q",
            "python -m pytest tests",
            "npm test",
            "npm run lint",
            "git status",
            "git diff HEAD",
            "ruff check .",
            "tsc --noEmit",
            "ls -la",
        ]:
            self.assertTrue(is_verify_command(cmd), cmd)

    def test_blocked(self):
        for cmd in [
            "rm -rf build",
            "python script.py",
            "python -c \"open('x','w').write('1')\"",
            "git commit -m x",
            "pip install requests",
            "pytest && rm -rf build",
            "pytest | tee out.log",
            "cat a > b",
            "",
        ]:
            self.assertFalse(is_verify_command(cmd), cmd)


class TestPlanBlock(unittest.TestCase):

    def test_edit_blocked(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("edit", {"path": "a.py"}))

    def test_file_write_blocked_insight_allowed(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("file", {"command": "fs", "action": "create"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("file", {"command": "fs", "action": "delete"}))
        self.assertIsNone(plan_block_reason("file", {"command": "fs", "action": "exists"}))
        self.assertIsNone(plan_block_reason("file", {"command": "insight", "mode": "structure"}))

    def test_shell_only_verify(self):
        self.assertIsNone(plan_block_reason("shell", {"command": "run", "cmd": "pytest -q"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "run", "cmd": "rm -rf x"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "session"}))

    def test_shell_simple_mode(self):
        """shell 兼容写法：command 直接是命令内容"""
        self.assertIsNone(plan_block_reason("shell", {"command": "git status"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "rm -rf x"}))

    def test_memory_write_blocked(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("memory", {"command": "store"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("memory", {}))
        self.assertIsNone(plan_block_reason("memory", {"command": "search"}))

    def test_memory_profile_writes_blocked(self):
        self.assertIsNone(plan_block_reason("memory", {"command": "profile", "subcommand": "get"}))
        self.assertIsNone(plan_block_reason("memory", {"command": "profile"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("memory", {"command": "profile", "subcommand": "set_plan_habit"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("memory", {"command": "profile", "subcommand": "set_persona"}))

    def test_unknown_tool_blocked(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("scheduler", {"command": "add"}))

    def test_sub_agent_allowed_in_plan(self):
        self.assertIsNone(plan_block_reason("sub_agent", {"command": "parallel", "tasks": []}))

    def test_mode_and_injection(self):
        self.assertEqual(normalize_mode("plan"), "plan")
        self.assertEqual(normalize_mode("PLAN"), "plan")
        self.assertEqual(normalize_mode("bad"), "run")
        self.assertIn("PLAN（只读）", plan_injection())
        self.assertLess(len(plan_injection()), 360, "提示词应保持精简")
        self.assertIn("<user_habit>", plan_injection("计划不超过 5 步，优先 pytest"))
        self.assertNotIn("<user_habit>", plan_injection())
        self.assertIn("RUN（正常执行）", run_injection())
        self.assertIn("请忽略", run_injection())


def _fake_memory(value=""):
    class _Repo:
        def __init__(self):
            self.value = value

        def get_by_memory_key(self, key, schema_type=None):
            return {"content": self.value} if self.value else None

    class _Mem:
        def __init__(self):
            self.repository = _Repo()
            self.calls = []

        def upsert_memory(self, **kwargs):
            self.calls.append(("upsert", kwargs))
            self.repository.value = kwargs.get("content", "")

        def delete_memory(self, **kwargs):
            self.calls.append(("delete", kwargs))
            self.repository.value = ""

    return _Mem()


class TestPlanHabitStore(unittest.TestCase):

    def test_memory_read(self):
        from app.core.security.run_mode import get_plan_habit
        mem = _fake_memory("记忆里的习惯")
        self.assertEqual(get_plan_habit(mem), "记忆里的习惯")

    def test_set_writes_memory(self):
        from app.core.security.run_mode import set_plan_habit
        mem = _fake_memory()
        self.assertTrue(set_plan_habit("先给结论", memory=mem))
        self.assertEqual(mem.calls[0][1]["memory_key"], "profile:plan_habit")
        self.assertEqual(mem.calls[0][1]["content"], "先给结论")
        self.assertTrue(mem.calls[0][1]["allow_protected"])

    def test_clear_removes_memory(self):
        from app.core.security.run_mode import set_plan_habit
        mem = _fake_memory("旧习惯")
        self.assertTrue(set_plan_habit("", memory=mem))
        self.assertEqual(mem.calls[0][0], "delete")
        self.assertTrue(mem.calls[0][1]["allow_protected"])

    def test_no_memory_returns_empty(self):
        from app.core.security import run_mode
        self.assertEqual(run_mode.get_plan_habit(object()), "")
        self.assertFalse(run_mode.set_plan_habit("x", memory=object()))

    def test_memory_tool_profile_sets_habit(self):
        from app.agent.tools.memory_tool import MemoryTool
        mem = _fake_memory()
        result = MemoryTool(mem)._cmd_profile(subcommand="set_plan_habit", plan_habit="优先 pytest")
        self.assertTrue(result["success"])
        self.assertEqual(mem.calls[0][1]["content"], "优先 pytest")


class _MockToolCall:
    def __init__(self, name, arguments=None, call_id=None):
        self.name = name
        self.arguments = arguments or {}
        self.id = call_id or f"call_{name}_1"


class _MockResponse:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = None
        self.finish_reason = None
        self.reasoning_content = None


class _MockLLM:
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
        self.chat_calls = []

    async def chat(self, messages, tools=None, **kwargs):
        self.chat_calls.append({"messages": messages, "tools": tools})
        if self.call_count < len(self.responses):
            r = self.responses[self.call_count]
            self.call_count += 1
            return r
        return _MockResponse(content="default")


class TestPlanModeLoop(unittest.TestCase):

    def setUp(self):
        from app.agent.shell.cellium_shell import CelliumShell
        self.shell = CelliumShell()
        self.marker = os.path.join(os.path.dirname(__file__), "_plan_blocked_marker.txt")

    def tearDown(self):
        self.shell.close()
        if os.path.exists(self.marker):
            os.remove(self.marker)

    def _build_loop(self, responses):
        from app.agent.loop.agent_loop import AgentLoop
        from app.agent.loop.memory import MemoryManager

        llm = _MockLLM(responses)
        loop = AgentLoop(
            llm_engine=llm, shell=self.shell, memory=MemoryManager(),
            enable_heuristics=False, enable_learning=False, enable_hybrid=False,
        )
        loop.control_loop = Mock()
        loop.control_loop.start_session = Mock()
        loop.control_loop.step = Mock(return_value=Mock(
            action_type="redirect", should_stop=False, force_memory_compact=False))
        loop.control_loop.end_round = Mock(return_value=0.0)
        loop.control_loop.end_session = Mock()
        loop._constraint_renderer = Mock()
        loop._constraint_renderer.render = Mock(return_value=Mock(
            trigger_reason="redirect", max_tokens=0, force_stop=False, forbidden=[]))
        loop._constraint_renderer.render_combined = Mock(return_value="")

        edit_tool = Mock()
        edit_tool.definition = {"type": "function", "function": {
            "name": "edit", "description": "edit file",
            "parameters": {"type": "object", "properties": {}}}}
        edit_tool.execute = Mock(side_effect=lambda *a, **kw: self._touch())
        edit_tool.execute_with_context = Mock(side_effect=lambda *a, **kw: self._touch())
        read_tool = Mock()
        read_tool.definition = {"type": "function", "function": {
            "name": "read", "description": "read file",
            "parameters": {"type": "object", "properties": {}}}}
        read_tool.execute = Mock(return_value={"success": True, "content": "ok"})
        read_tool.execute_with_context = Mock(return_value={"success": True, "content": "ok"})
        loop.tools.update({"edit": edit_tool, "read": read_tool})
        loop._builtin_tools.update({"edit": edit_tool, "read": read_tool})
        loop._tool_executor.refresh_tools(loop.tools)
        return loop, llm

    def _touch(self, **_kw):
        with open(self.marker, "w", encoding="utf-8") as f:
            f.write("written")
        return {"success": True}

    def test_tools_filtered_in_plan(self):
        loop, llm = self._build_loop([
            _MockResponse(content="none"),
            _MockResponse(content="done"),
        ])
        asyncio.run(self._run(loop, "plan"))
        tool_lists = [c["tools"] for c in llm.chat_calls if c["tools"]]
        self.assertTrue(tool_lists, "应有带工具定义的 LLM 调用")
        names = {d["function"]["name"] for d in tool_lists[-1]}
        self.assertIn("read", names)
        self.assertNotIn("edit", names)

    def test_edit_call_blocked(self):
        loop, llm = self._build_loop([
            _MockResponse(content="none"),
            _MockResponse(tool_calls=[_MockToolCall("edit", {"path": self.marker})]),
            _MockResponse(content="plan only"),
        ])
        events = asyncio.run(self._run(loop, "plan"))
        tr = [e for e in events if e.get("type") == "tool_result"]
        self.assertEqual(len(tr), 1)
        self.assertIn("Plan 模式禁止", tr[0]["result"]["error"])
        self.assertFalse(os.path.exists(self.marker), "Plan 模式不允许真正写文件")

    def test_mode_reset_after_run(self):
        loop, _ = self._build_loop([
            _MockResponse(content="none"),
            _MockResponse(tool_calls=[_MockToolCall("read", {"path": "a.txt"})]),
            _MockResponse(content="done"),
        ])
        asyncio.run(self._run(loop, "plan"))
        self.assertEqual(loop._run_mode, "run")

    def test_run_mode_injects_override_notice(self):
        loop, llm = self._build_loop([
            _MockResponse(content="none"),
            _MockResponse(content="done"),
        ])
        asyncio.run(self._run(loop, "run"))
        texts = "".join(
            str(m.get("content") or "")
            for call in llm.chat_calls for m in call["messages"]
        )
        self.assertIn("RUN（正常执行）", texts)

    def test_run_mode_allows_edit(self):
        loop, _ = self._build_loop([
            _MockResponse(content="none"),
            _MockResponse(tool_calls=[_MockToolCall("edit", {"path": self.marker})]),
            _MockResponse(content="done"),
        ])
        events = asyncio.run(self._run(loop, "run"))
        tr = [e for e in events if e.get("type") == "tool_result"]
        self.assertTrue(os.path.exists(self.marker))
        self.assertNotIn("Plan 模式禁止", str(tr[0]["result"]))

    async def _run(self, loop, mode):
        return [e async for e in loop.run_stream("do it", mode=mode)]


class TestModePassthrough(unittest.TestCase):

    def test_task_manager_passes_mode(self):
        from app.server.task_manager import BackgroundTaskManager

        tm = BackgroundTaskManager()
        captured = {}

        async def _fake(**kwargs):
            captured.update(kwargs)

        tm._run_agent_loop = _fake
        asyncio.run(tm.start_task(
            session_id="s1", agent_loop=None, user_input="hi", memory=None, mode="plan"))
        self.assertEqual(captured.get("mode"), "plan")

    def test_chat_request_accepts_mode(self):
        from app.server.routes.chat import ChatRequest
        self.assertEqual(ChatRequest(message="hi", mode="plan").mode, "plan")
        self.assertIsNone(ChatRequest(message="hi").mode, "默认不覆盖会话模式")


class TestSessionModeStore(unittest.TestCase):

    def test_default_and_set(self):
        from app.server.session_mode import get_mode, set_mode
        self.assertEqual(get_mode("mode_test_1"), "run")
        self.assertEqual(set_mode("mode_test_1", "plan"), "plan")
        self.assertEqual(get_mode("mode_test_1"), "plan")
        self.assertEqual(set_mode("mode_test_1", "bogus"), "run")

    def test_subagent_inherits_mode(self):
        from components.sub_agent import _resolve_parent_mode
        from app.server.session_mode import set_mode

        self.assertEqual(_resolve_parent_mode(None), "run")
        self.assertEqual(_resolve_parent_mode(""), "run")
        set_mode("parent_sess_mode", "plan", broadcast=False)
        self.assertEqual(_resolve_parent_mode("parent_sess_mode"), "plan")

    def test_broadcast_payload(self):
        from unittest.mock import patch, Mock
        from app.server.session_mode import set_mode

        broker = Mock()
        with patch("app.messaging.message_broker.get_message_broker", return_value=broker), \
             patch("app.server.routes.ws_event_manager.ws_publish_event") as ws:
            set_mode("mode_test_2", "plan")

        broker.publish.assert_called_once()
        sid, payload = broker.publish.call_args[0]
        self.assertEqual(sid, "mode_test_2")
        self.assertEqual(payload["type"], "mode_changed")
        self.assertEqual(payload["mode"], "plan")
        ws.assert_called_once()
        self.assertEqual(ws.call_args[0][0], "chat_event")


if __name__ == "__main__":
    unittest.main()
