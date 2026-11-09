# -*- coding: utf-8 -*-
"""Plan / Run 模式：黑名单拦截与执行限制"""
import asyncio
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security.run_mode import (
    normalize_mode, plan_block_reason, plan_injection, run_injection, shell_block_reason,
    delegate_block_reason, delegate_injection, mode_block_reason,
    blocked_tools_for_mode, is_delegate,
)


class TestShellBlacklist(unittest.TestCase):

    def test_readonly_allowed(self):
        for cmd in [
            "pytest -q",
            "python -m pytest tests",
            "python -m unittest",
            "npm test",
            "npm run lint",
            "git status",
            "git diff HEAD",
            "git branch -a",
            "git tag -l",
            "git stash list",
            "git stash show",
            "git clean -n",
            "git config --get user.name",
            "git log | head -5",
            "git log; git diff",
            "Get-ChildItem | Select-Object -First 5",
            "rg foo src",
            "rg git commit src",
            "find . -name test.py",
            "cat a 2>/dev/null",
            "echo hi >&2",
            "curl -s https://example.com",
            "wget -O - https://example.com",
            "tar -tzf a.tar",
            "docker ps",
            "docker compose ps",
            "kubectl get pods",
            "ruff check .",
            "tsc --noEmit",
            "ls -la",
            "wc -l a.txt",
        ]:
            self.assertIsNone(shell_block_reason(cmd), cmd)

    def test_write_blocked(self):
        for cmd in [
            "rm -rf build",
            "del a.txt",
            "mv a b",
            "pytest && rm -rf build",
            "sleep 10 & rm -rf x",
            "pytest | tee out.log",
            "cat a > b",
            "cat a >> b",
            "python -c \"open('x','w').write('1')\"",
            "bash -c 'echo hi'",
            "powershell -Command Remove-Item x",
            "& notepad.exe",
            "git commit -m x",
            "git checkout main",
            "git stash",
            "git clean -fdx",
            "git push origin main",
            "git branch -D foo",
            "git tag -d v1",
            "pip install requests",
            "python -m pip install requests",
            "npm i lodash",
            "docker compose up -d",
            "docker-compose up -d",
            "sed -i 's/a/b/' x",
            "find . -name x -delete",
            "find . -exec rm {} ;",
            "tar -xzf a.tar",
            "tar -czf out.tar src",
            "unzip a.zip",
            "curl -o out.html https://x",
            "curl -Lo out.html https://x",
            "wget https://x/a.zip",
            "Invoke-WebRequest -OutFile x https://y",
            "iwr https://y -OutFile x",
            "Get-Content a | Set-Content b",
            "Start-Process notepad",
            "echo $(git rev-parse HEAD)",
        ]:
            self.assertIsNotNone(shell_block_reason(cmd), cmd)


class TestPlanBlock(unittest.TestCase):

    def test_edit_blocked(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("edit", {"path": "a.py"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("skill_installer", {"command": "install"}))

    def test_file_write_blocked_insight_allowed(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("file", {"command": "fs", "action": "create"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("file", {"command": "fs", "action": "delete"}))
        self.assertIsNone(plan_block_reason("file", {"command": "fs", "action": "exists"}))
        self.assertIsNone(plan_block_reason("file", {"command": "insight", "mode": "structure"}))

    def test_shell_blacklist(self):
        self.assertIsNone(plan_block_reason("shell", {"command": "run", "cmd": "pytest -q"}))
        self.assertIsNone(plan_block_reason("shell", {"command": "run", "cmd": "git log | head -5"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "run", "cmd": "rm -rf x"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "run", "cmd": "python -c \"1\""}))
        self.assertIsNone(plan_block_reason("shell", {"command": "list"}))
        self.assertIsNone(plan_block_reason("shell", {"command": "output", "task_id": "t"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "kill", "task_id": "t"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "session"}))
        self.assertIsNone(plan_block_reason("shell", {"command": "session", "action": "list"}))

    def test_shell_simple_mode(self):
        self.assertIsNone(plan_block_reason("shell", {"command": "git status"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("shell", {"command": "rm -rf x"}))

    def test_memory_write_blocked(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("memory", {"command": "store"}))
        self.assertIsNone(plan_block_reason("memory", {"command": "search"}))
        self.assertIsNone(plan_block_reason("memory", {"command": "list"}))
        self.assertIsNone(plan_block_reason("memory", {}))

    def test_memory_profile_writes_blocked(self):
        self.assertIsNone(plan_block_reason("memory", {"command": "profile", "subcommand": "get"}))
        self.assertIsNone(plan_block_reason("memory", {"command": "profile"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("memory", {"command": "profile", "subcommand": "set_plan_habit"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("memory", {"command": "profile", "subcommand": "set_persona"}))

    def test_query_tools_allowed(self):
        self.assertIsNone(plan_block_reason("config", {"command": "list_models"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("config", {"command": "add_model"}))
        self.assertIsNone(plan_block_reason("component", {"command": "list"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("component", {"command": "sandbox_add"}))
        self.assertIsNone(plan_block_reason("component", {"command": "cleanup", "dry_run": True}))
        self.assertIn("Plan 模式禁止", plan_block_reason("component", {"command": "cleanup", "force": True}))
        self.assertIsNone(plan_block_reason("scheduler", {"command": "list"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("scheduler", {"command": "create_interval"}))
        self.assertIsNone(plan_block_reason("skill_manager", {"command": "list"}))
        self.assertIsNone(plan_block_reason("qq_files", {"command": "list"}))
        self.assertIn("Plan 模式禁止", plan_block_reason("qq_files", {"command": "send_file"}))

    def test_unknown_tool_allowed(self):
        self.assertIsNone(plan_block_reason("some_custom_tool", {"command": "query"}))

    def test_sub_agent_allowed_in_plan(self):
        self.assertIsNone(plan_block_reason("sub_agent", {"command": "parallel", "tasks": []}))

    def test_mode_and_injection(self):
        self.assertEqual(normalize_mode("plan"), "plan")
        self.assertEqual(normalize_mode("PLAN"), "plan")
        self.assertEqual(normalize_mode("delegate"), "delegate")
        self.assertEqual(normalize_mode("bad"), "run")
        self.assertIn("PLAN（只读）", plan_injection())
        self.assertLess(len(plan_injection()), 420, "提示词应保持精简")
        self.assertIn("<user_habit>", plan_injection("计划不超过 5 步，优先 pytest"))
        self.assertNotIn("<user_habit>", plan_injection())
        self.assertIn("RUN（正常执行）", run_injection())
        self.assertIn("请忽略", run_injection())

    def test_plan_blocks_session_agent(self):
        self.assertIn("Plan 模式禁止", plan_block_reason("session_agent", {"command": "send"}))
        self.assertIn("session_agent", blocked_tools_for_mode("plan"))
        self.assertNotIn("session_agent", blocked_tools_for_mode("delegate"))
        self.assertEqual(blocked_tools_for_mode("run"), frozenset())

    def test_delegate_mode_blocks_direct_writes(self):
        self.assertTrue(is_delegate("delegate"))
        self.assertIn("委托模式禁止", delegate_block_reason("edit", {"path": "a.py"}))
        self.assertIn("委托模式禁止", delegate_block_reason("file", {"command": "fs", "action": "create"}))
        self.assertIn("委托模式禁止", delegate_block_reason("shell", {"command": "run", "cmd": "rm -rf x"}))
        self.assertIsNone(delegate_block_reason("shell", {"command": "run", "cmd": "pytest -q"}))
        self.assertIsNone(delegate_block_reason("session_agent", {"command": "send", "target": "B"}))
        self.assertIsNone(delegate_block_reason("read", {"path": "a.py"}))
        self.assertIn("委托模式禁止", mode_block_reason("edit", {}, "delegate"))
        self.assertIsNone(mode_block_reason("edit", {}, "run"))

    def test_delegate_injection(self):
        text = delegate_injection()
        self.assertIn("DELEGATE", text)
        self.assertIn("session_agent", text)
        self.assertIn("禁止", text)
        # 明确要求复用会话并给出分工维度
        self.assertIn("优先复用", text)
        self.assertIn("sessions", text)
        self.assertIn("按能力", text)
        self.assertIn("按角色", text)

    def test_shell_array_and_bypass_blocked(self):
        for cmd in (
            ["rm", "-rf", "x"],
            ["git", "commit", "-m", "x"],
            ["python", "-c", "open(1)"],
        ):
            self.assertIsNotNone(plan_block_reason("shell", {"command": "run", "cmd": cmd}), cmd)
            self.assertIsNotNone(
                delegate_block_reason("shell", {"command": "run", "cmd": cmd}), cmd
            )
        for cmd in (
            "git -C repo commit -m x",
            "git -c user.name=x commit",
            "git --git-dir=.git commit",
            "env rm -rf x",
            "timeout 5 rm -rf x",
            "nice rm -rf x",
            "command rm -rf x",
            "r''m -rf x",
            "g''it commit -m x",
            "rm${IFS}-rf x",
            "pip -q install requests",
            "npm --prefix ui install",
            "pnpm -C ui add x",
            "docker --context foo run x",
            "kubectl --namespace x apply",
        ):
            self.assertIsNotNone(plan_block_reason("shell", {"command": "run", "cmd": cmd}), cmd)

    def test_shell_readonly_still_allowed(self):
        for cmd in (
            "pytest -q", "git log | head -5", "git status", "git diff HEAD",
            "git log --grep commit", "npm test", "npm run lint",
            "docker ps", "kubectl get pods", "find . -name test.py",
        ):
            self.assertIsNone(plan_block_reason("shell", {"command": "run", "cmd": cmd}), cmd)

    def test_normalize_non_string_inputs(self):
        self.assertEqual(normalize_mode(1), "run")
        self.assertEqual(normalize_mode(["plan"]), "run")


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
