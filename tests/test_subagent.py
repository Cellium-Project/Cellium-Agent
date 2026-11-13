# -*- coding: utf-8 -*-
"""
子 Agent 组件核心测试（pytest/unittest 合集）

覆盖:
  1. 组件注册与命令（无需主 loop）
  2. tools 白名单解析与禁用过滤（无需主 loop）
  3. system injection 与 help（无需主 loop）
  4. 单任务 / 并行并发 / 白名单隔离（需主 loop）
  5. 异常隔离 / 资源清理 / 人格隔离（需主 loop）
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.di_config import bind_main_loop
from components.sub_agent import SubAgent


# ================================================================
# Mocks
# ================================================================

class MockResponse:
    def __init__(self, content=None, tool_calls=None, usage=None, finish_reason=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}
        self.finish_reason = finish_reason
        self.reasoning_content = reasoning_content


class MockToolCall:
    def __init__(self, name, arguments=None, call_id=None):
        self.name = name
        self.arguments = arguments or {}
        self.id = call_id or f"call_{name}_1"


class FakeTool:
    def __init__(self, name, behavior="normal"):
        self.name = name
        self.behavior = behavior

    @property
    def definition(self):
        return {"type": "function", "function": {"name": self.name, "description": f"模拟 {self.name}", "parameters": {"type": "object", "properties": {}}}}

    def execute_with_context(self, arguments, session_id=None, platform_context=None):
        if self.behavior == "raise":
            raise RuntimeError("工具内部崩溃")
        return {"success": True, "result": f"{self.name} 执行成功", "path": "."}


class SimpleEngine:
    """一轮直接回复"""

    def __init__(self, reply="任务完成"):
        self.reply = reply
        self.call_count = 0
        self.closed = False

    async def chat(self, messages, tools=None, **kwargs):
        self.call_count += 1
        return MockResponse(content=self.reply, finish_reason="stop")

    @property
    def model_info(self):
        from app.agent.llm.engine import ModelInfo
        return ModelInfo(8192, 4096, True)

    @property
    def context_window(self):
        return 8192

    async def health_check(self):
        return True

    async def close(self):
        self.closed = True


class ToolThenReplyEngine:
    """先工具调用，再最终回复"""

    def __init__(self, first_tool="read"):
        self.first_tool = first_tool
        self.call_count = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            return MockResponse(content="先用工具", tool_calls=[MockToolCall(self.first_tool, {"file_path": "t.py"}, "c1")], finish_reason="tool_calls")
        return MockResponse(content="子任务完成", finish_reason="stop")

    @property
    def model_info(self):
        from app.agent.llm.engine import ModelInfo
        return ModelInfo(8192, 4096, True)

    @property
    def context_window(self):
        return 8192

    async def health_check(self):
        return True

    async def close(self):
        pass


class RaiseEngine:
    async def chat(self, messages, tools=None, **kwargs):
        raise RuntimeError("LLM API 故障")

    @property
    def model_info(self):
        from app.agent.llm.engine import ModelInfo
        return ModelInfo(8192, 4096, True)

    @property
    def context_window(self):
        return 8192

    async def health_check(self):
        return True

    async def close(self):
        pass


class SlowEngine:
    """固定延迟，用于并发计时验证"""

    def __init__(self, delay=0.5):
        self.delay = delay
        self.call_count = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.call_count += 1
        await asyncio.sleep(self.delay)
        return MockResponse(content="完成", finish_reason="stop")

    @property
    def model_info(self):
        from app.agent.llm.engine import ModelInfo
        return ModelInfo(8192, 4096, True)

    @property
    def context_window(self):
        return 8192

    async def health_check(self):
        return True

    async def close(self):
        pass


class CaptureEngine:
    """捕获子 Agent 收到的 system prompt 和工具定义"""

    def __init__(self):
        self.captured_system = ""
        self.captured_tools = []

    async def chat(self, messages, tools=None, **kwargs):
        if not self.captured_system:
            for m in messages:
                if m.get("role") == "system":
                    self.captured_system += m.get("content", "") + "\n"
            self.captured_tools = [d["function"]["name"] for d in (tools or [])]
        return MockResponse(content="任务完成", finish_reason="stop")

    @property
    def model_info(self):
        from app.agent.llm.engine import ModelInfo
        return ModelInfo(8192, 4096, True)

    @property
    def context_window(self):
        return 8192

    async def health_check(self):
        return True

    async def close(self):
        pass


# ================================================================
# 主 loop fixture（模块级，模拟 web_server）
# ================================================================

_MAIN_LOOP = None
_PUMP = None
_POOL = None


def setUpModule():
    global _MAIN_LOOP, _PUMP, _POOL
    _MAIN_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_MAIN_LOOP)
    bind_main_loop(_MAIN_LOOP)
    ready = threading.Event()

    def pump():
        asyncio.set_event_loop(_MAIN_LOOP)
        _MAIN_LOOP.call_soon(ready.set)
        _MAIN_LOOP.run_forever()

    _PUMP = threading.Thread(target=pump, daemon=True)
    _PUMP.start()
    ready.wait(timeout=5)
    _POOL = ThreadPoolExecutor(max_workers=16)


def tearDownModule():
    if _POOL:
        _POOL.shutdown(wait=True)
    if _MAIN_LOOP:
        _MAIN_LOOP.call_soon_threadsafe(_MAIN_LOOP.stop)
    if _PUMP:
        _PUMP.join(timeout=5)


def call_cmd(fn, timeout=180):
    """在线程池中同步调用组件命令（等价 ToolExecutor 执行）"""
    return _POOL.submit(fn).result(timeout=timeout)


def setup_subagent(engine_factory, tool_collect=None):
    """打桩子 Agent 内部依赖并返回组件实例"""
    import components.sub_agent as subagent_mod
    subagent_mod._resolve_engine_clone = engine_factory

    def default_collect(self, allowed_names):
        return {n: FakeTool(n) for n in allowed_names if n in ("read", "grep", "shell", "glob")}
    subagent_mod.SubAgent._collect_tool_instances = tool_collect or default_collect
    return subagent_mod.SubAgent()


# ================================================================
# 单元测试（无需主 loop 执行）
# ================================================================

class TestSubAgentComponent(unittest.TestCase):
    """组件注册与命令"""

    def test_cell_name(self):
        c = SubAgent()
        self.assertEqual(c.cell_name, "sub_agent")

    def test_commands(self):
        c = SubAgent()
        cmds = list(c.get_commands().keys())
        self.assertIn("parallel", cmds)
        self.assertIn("list", cmds)
        self.assertIn("status", cmds)
        self.assertIn("cancel", cmds)
        self.assertNotIn("create", cmds, "create 已移除，只保留 parallel")

    def test_tools_whitelist_parsing(self):
        c = SubAgent()
        # 显式传入即精确白名单；不传才回退默认四项
        self.assertEqual(c._allowed_tool_names('["read","grep"]'), ["read", "grep"])
        self.assertEqual(c._allowed_tool_names("read, grep, glob"), ["read", "grep", "glob"])
        self.assertEqual(c._allowed_tool_names(None), ["read", "ls", "glob", "grep"])
        self.assertEqual(c._allowed_tool_names(["read"]), ["read"])
        self.assertEqual(
            c._allowed_tool_names(["shell"]),
            ["shell", "shell_task", "shell_session"],
        )

    def test_tools_blacklist(self):
        c = SubAgent()
        result = c._allowed_tool_names(["read", "weixin_files", "web_search", "config", "scheduler", "grep"])
        self.assertEqual(result, ["read", "grep"], f"禁用工具应被过滤: {result}")

    def test_tools_rule_extraction(self):
        c = SubAgent()
        rules = c._extract_tool_rules(["read", "config", "ls"])
        self.assertIn("read：", rules)
        self.assertIn("ls：", rules)
        self.assertNotIn("config", rules, "config 已从工具铁律移除")
        self.assertNotIn("shell：", rules, "未授权工具不注入铁律")

    def test_normalize_tasks(self):
        c = SubAgent()
        # 对象数组
        self.assertEqual(len(c._normalize_tasks([{"name": "a"}, {"name": "b"}])), 2)
        # JSON 字符串数组（用户曾报错场景）
        arr = [json.dumps({"name": "a", "task": "t"}), json.dumps({"name": "b", "task": "t"})]
        self.assertEqual(len(c._normalize_tasks(arr)), 2)
        # 整个 JSON 字符串
        self.assertEqual(len(c._normalize_tasks(json.dumps([{"name": "a"}], ensure_ascii=False))), 1)
        # 非法输入
        self.assertEqual(c._normalize_tasks(None), [])
        self.assertEqual(c._normalize_tasks({}), [])
        self.assertEqual(c._normalize_tasks(123), [])
        self.assertEqual(c._normalize_tasks("{bad json"), [])

    def test_system_injection(self):
        c = SubAgent()
        inj = c._build_system_injection("你是审查专家", "只读", ["read", "grep"])
        self.assertIn("[子 Agent 任务身份]", inj)
        self.assertIn("你是审查专家", inj)
        self.assertIn("只读", inj)
        self.assertIn("read：", inj)
        self.assertIn("grep：", inj)
        self.assertIn("[输出要求]", inj)
        self.assertIn("社交套话", inj, "子 Agent 应压制寒暄/询问称呼")
        self.assertLess(len(inj), 400, "子 Agent 提示词应保持精简")

    def test_system_injection_inherits_user_name(self):
        from unittest.mock import patch
        c = SubAgent()
        func_globals = type(c)._build_system_injection.__globals__
        with patch.dict(func_globals, {"_resolve_user_name": lambda: "老王"}):
            inj = c._build_system_injection("", "", [])
        self.assertIn("老王", inj, "子 Agent 应继承主 Agent 的用户称呼")
        self.assertNotIn("不要询问用户称呼", inj)

        with patch.dict(func_globals, {"_resolve_user_name": lambda: ""}):
            inj = c._build_system_injection("", "", [])
        self.assertIn("不要询问用户称呼", inj, "无称呼时也不应追问")

    def test_profile_piece_can_skip_ask_user_name(self):
        from app.agent.prompt.pieces import get_profile_piece
        self.assertEqual(get_profile_piece(None, ask_user_name=False).render({}), "")
        self.assertIn("礼貌询问", get_profile_piece(None).render({}))

    def test_help_list_status_cancel(self):
        c = SubAgent()
        help_info = c._cmd_help()
        self.assertEqual(help_info["name"], "sub_agent")
        self.assertIn("parallel", help_info["available_commands"])

        lst = c._cmd_list()
        self.assertIn("success", lst)

        st = c._cmd_status("nope")
        self.assertFalse(st["success"])
        self.assertIn("available", st)

        cn = c._cmd_cancel("nope")
        self.assertFalse(cn["success"])


# ================================================================
# 集成测试（需主 loop）
# ================================================================

class TestSubAgentBackground(unittest.TestCase):
    """后台 spawn / 进度 / 结果 / 取消 / 唤醒"""

    def setUp(self):
        import components.sub_agent as m
        self._mod = m
        self._session = f"bgtest_{uuid.uuid4().hex[:6]}"

    def tearDown(self):
        for key, info in list(self._mod._SUBAGENTS.items()):
            if not key.startswith(self._session + "::"):
                continue
            bg_task = info.get("bg_task")
            if bg_task is not None:
                try:
                    _MAIN_LOOP.call_soon_threadsafe(bg_task.cancel)
                except Exception:
                    pass
            self._mod._SUBAGENTS.pop(key, None)
        worker = self._mod._NOTIFY_WORKERS.pop(self._session, None)
        self._mod._PENDING_NOTIFY.pop(self._session, None)
        if worker is not None:
            try:
                _MAIN_LOOP.call_soon_threadsafe(worker.cancel)
            except Exception:
                pass

    def test_parallel_inherits_parent_undo_context(self):
        from unittest.mock import patch
        c = setup_subagent(lambda: SimpleEngine("完成"))
        with patch.object(self._mod, "_parent_undo_context", return_value=("parent", "turn1")):
            r = call_cmd(lambda: c._cmd_parallel(
                tasks=[{"name": "u", "task": "任务"}], session_id=self._session))
        self.assertTrue(r["success"])
        info = self._mod._SUBAGENTS[self._mod._sub_key(self._session, "u")]
        self.assertEqual(info["loop"]._undo_context, ("parent", "turn1"))

    def test_parent_undo_context_from_manager(self):
        from unittest.mock import patch

        class FakeExecutor:
            def get_turn_context(self):
                return ("s1", "t1")

        class FakeLoop:
            _tool_executor = FakeExecutor()

        class FakeManager:
            def get_loop_sync(self, session_id):
                return FakeLoop()

        with patch("app.agent.loop.agent_loop_manager.AgentLoopManager.get_instance",
                   return_value=FakeManager()):
            self.assertEqual(self._mod._parent_undo_context("s1"), ("s1", "t1"))
        self.assertIsNone(self._mod._parent_undo_context(None))

    def _wait_until(self, cond, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def _wait_done(self, status_fn, timeout=4.0):
        deadline = time.time() + timeout
        status = None
        while time.time() < deadline:
            status = status_fn()
            if status.get("status") in ("done", "error", "cancelled"):
                return status
            time.sleep(0.05)
        return status

    def test_spawn_returns_immediately_and_results(self):
        c = setup_subagent(lambda: SlowEngine(0.6))
        t0 = time.time()
        r = call_cmd(lambda: c._cmd_spawn(
            tasks=[{"name": "bg1", "task": "后台任务"}],
            notify=False, session_id=self._session,
        ))
        submit_cost = time.time() - t0
        self.assertTrue(r["success"], r)
        self.assertLess(submit_cost, 0.3, f"spawn 应立即返回，实际 {submit_cost:.2f}s")

        status = self._wait_done(lambda: call_cmd(
            lambda: c._cmd_status("bg1", session_id=self._session)))
        self.assertEqual(status["status"], "done", status)
        self.assertIn("完成", status.get("result_preview", ""))

        res = call_cmd(lambda: c._cmd_results("bg1", session_id=self._session))
        self.assertFalse(res["running"])
        self.assertIn("完成", res["content"])

    def test_spawn_cancel_marks_cancelled(self):
        c = setup_subagent(lambda: SlowEngine(3))
        r = call_cmd(lambda: c._cmd_spawn(
            tasks=[{"name": "bgc", "task": "长任务"}],
            notify=False, session_id=self._session,
        ))
        self.assertTrue(r["success"], r)
        time.sleep(0.15)
        cn = call_cmd(lambda: c._cmd_cancel("bgc", session_id=self._session))
        self.assertTrue(cn["success"], cn)
        status = self._wait_done(lambda: call_cmd(
            lambda: c._cmd_status("bgc", session_id=self._session)), timeout=3.0)
        self.assertEqual(status["status"], "cancelled", status)

    def test_spawn_limit(self):
        c = setup_subagent(lambda: SlowEngine(2))
        old = c.MAX_BACKGROUND_SUBAGENTS
        c.MAX_BACKGROUND_SUBAGENTS = 1
        try:
            first = call_cmd(lambda: c._cmd_spawn(
                tasks=[{"name": "bgl1", "task": "任务"}],
                notify=False, session_id=self._session,
            ))
            second = call_cmd(lambda: c._cmd_spawn(
                tasks=[{"name": "bgl2", "task": "任务"}],
                notify=False, session_id=self._session,
            ))
            self.assertTrue(first["success"], first)
            self.assertFalse(second["success"], second)
            self.assertIn("上限", second.get("error", ""))
        finally:
            c.MAX_BACKGROUND_SUBAGENTS = old

    def test_duplicate_name_rejected(self):
        c = setup_subagent(lambda: SlowEngine(2))
        first = call_cmd(lambda: c._cmd_spawn(
            tasks=[{"name": "dup", "task": "任务"}], notify=False, session_id=self._session))
        second = call_cmd(lambda: c._cmd_spawn(
            tasks=[{"name": "dup", "task": "任务"}], notify=False, session_id=self._session))
        self.assertTrue(first["success"], first)
        self.assertFalse(second["success"], second)
        self.assertIn("已在使用", second.get("error", ""))

    def test_cancel_before_start_skips_execution(self):
        called = []
        c = setup_subagent(lambda: called.append(1) or SlowEngine(0.1))
        key = self._mod._sub_key(self._session, "pre")
        self._mod._SUBAGENTS[key] = {
            "name": "pre", "task": "任务", "status": "queued",
            "started_at": time.time(), "updated_at": time.time(),
        }
        cn = call_cmd(lambda: c._cmd_cancel("pre", session_id=self._session))
        self.assertTrue(cn["success"], cn)
        fut = asyncio.run_coroutine_threadsafe(
            c._run_background({"name": "pre", "task": "任务"}, self._session, False, False),
            _MAIN_LOOP,
        )
        fut.result(timeout=5)
        status = c._cmd_status("pre", session_id=self._session)
        self.assertEqual(status["status"], "cancelled", status)
        self.assertEqual(called, [], "取消后不应再启动引擎")

    def test_finished_entries_pruned(self):
        c = setup_subagent(lambda: SlowEngine(0.1))
        prefix = self._session + "::"
        for i in range(25):
            self._mod._SUBAGENTS[f"{prefix}old{i}"] = {
                "name": f"old{i}", "status": "done",
                "started_at": time.time() - 100 + i,
                "finished_at": time.time() - 100 + i,
            }
        r = call_cmd(lambda: c._cmd_spawn(
            tasks=[{"name": "fresh", "task": "任务"}], notify=False, session_id=self._session))
        self.assertTrue(r["success"], r)
        finished = [
            k for k, v in self._mod._SUBAGENTS.items()
            if k.startswith(prefix) and v.get("status") in ("done", "error", "cancelled")
        ]
        self.assertLessEqual(len(finished), 20, f"完成记录应被裁剪: {len(finished)}")

    def test_notify_falls_back_to_enqueue(self):
        from unittest.mock import patch
        calls = {}

        class FakeTaskMgr:
            def has_running_task(self, sid):
                return False

            async def start_task(self, **kwargs):
                calls["start"] = kwargs
                return False

            def enqueue_supplement_message(self, sid, payload):
                calls.setdefault("queue", payload)
                return True

        class FakeMgr:
            async def get_loop(self, sid):
                return object()

        class FakeSessionMgr:
            def get_or_create(self, sid):
                return type("S", (), {"memory": object()})()

        c = self._mod.SubAgent()
        payload = {"name": "n2", "success": True, "content": "结论",
                   "iterations": 1, "tools_used": [], "duration_s": 0.1}
        with patch("app.server.task_manager.get_task_manager", return_value=FakeTaskMgr()), \
             patch("app.agent.loop.agent_loop_manager.AgentLoopManager.get_instance", return_value=FakeMgr()), \
             patch("app.agent.loop.session_manager.get_session_manager", return_value=FakeSessionMgr()), \
             patch("app.server.session_mode.get_mode", return_value="run"), \
             patch.object(self._mod, "_NOTIFY_RETRY_INTERVAL", 0.05), \
             patch("app.messaging.message_broker.get_message_broker", side_effect=RuntimeError("no broker")):
            fut = asyncio.run_coroutine_threadsafe(
                c._notify_parent(self._session, payload), _MAIN_LOOP)
            fut.result(timeout=5)
            delivered = self._wait_until(lambda: "queue" in calls)

        self.assertTrue(delivered, "启动失败时应回退到排队")
        self.assertIn("[子Agent完成]", calls["queue"]["content"])

    def test_parallel_publishes_progress_and_done(self):
        from unittest.mock import patch
        events = []

        class FakeBroker:
            def publish(self, sid, evt):
                events.append((sid, evt))

        c = setup_subagent(lambda: SlowEngine(0.2))
        with patch("app.messaging.message_broker.get_message_broker", return_value=FakeBroker()):
            r = call_cmd(lambda: c._cmd_parallel(
                tasks=[{"name": "pl", "task": "任务", "tools": ["read"]}],
                timeout=5, session_id=self._session,
            ))
        self.assertTrue(r["success"], r)
        types = [evt["type"] for _, evt in events]
        self.assertIn("subagent_progress", types, "parallel 也应发布进度事件")
        self.assertIn("subagent_done", types, "parallel 也应发布完成事件")

    def test_spawn_input_validation(self):
        c = setup_subagent(lambda: SlowEngine(0.1))
        r1 = call_cmd(lambda: c._cmd_spawn(tasks=[], session_id=self._session))
        r2 = call_cmd(lambda: c._cmd_spawn(tasks=[{"name": "x", "task": " "}], session_id=self._session))
        self.assertFalse(r1["success"])
        self.assertFalse(r2["success"])
        self.assertIn("task", r2["error"])

    def test_spawn_requires_main_loop(self):
        from unittest.mock import patch
        c = setup_subagent(lambda: SlowEngine(0.1))
        with patch.object(self._mod, "_get_main_loop", return_value=None):
            r = call_cmd(lambda: c._cmd_spawn(
                tasks=[{"name": "nl", "task": "任务"}], notify=False, session_id=self._session))
        self.assertFalse(r["success"])
        self.assertIn("事件循环", r["error"])

    def test_results_missing_name(self):
        c = setup_subagent(lambda: SlowEngine(0.1))
        r1 = call_cmd(lambda: c._cmd_results("", session_id=self._session))
        r2 = call_cmd(lambda: c._cmd_results("nope", session_id=self._session))
        self.assertFalse(r1["success"])
        self.assertFalse(r2["success"])
        self.assertIn("不存在", r2["error"])

    def test_spawn_publishes_done_without_notify(self):
        from unittest.mock import patch
        events = []

        class FakeBroker:
            def publish(self, sid, evt):
                events.append((sid, evt))

        c = setup_subagent(lambda: SlowEngine(0.2))
        with patch("app.messaging.message_broker.get_message_broker", return_value=FakeBroker()):
            r = call_cmd(lambda: c._cmd_spawn(
                tasks=[{"name": "nd", "task": "任务"}],
                notify=False, session_id=self._session,
            ))
            ok = self._wait_until(
                lambda: any(evt["type"] == "subagent_done" for _, evt in events), timeout=2.0)
        self.assertTrue(r["success"], r)
        self.assertTrue(ok, "notify=false 也必须广播完成事件，避免卡片停留在 running")

    def test_progress_counts_tool_results(self):
        from unittest.mock import patch
        events = []

        class FakeBroker:
            def publish(self, sid, evt):
                events.append(evt)

        c = self._mod.SubAgent()
        key = self._mod._sub_key(self._session, "ct")
        self._mod._SUBAGENTS[key] = {
            "name": "ct", "status": "running", "started_at": time.time(), "_pushed_at": 0,
        }
        with patch("app.messaging.message_broker.get_message_broker", return_value=FakeBroker()):
            c._update_progress(key, {"type": "tool_result", "tool": "read"}, self._session)
            self._mod._SUBAGENTS[key]["_pushed_at"] = 0
            c._update_progress(key, {"type": "tool_result", "tool": "grep"}, self._session)
        self.assertEqual(self._mod._SUBAGENTS[key]["tool_calls"], 2, "无 tool_start 时也应按 tool_result 计数")
        self.assertEqual(self._mod._SUBAGENTS[key]["last_tool"], "grep")

    def test_progress_elapsed_uses_real_start(self):
        from unittest.mock import patch
        events = []

        class FakeBroker:
            def publish(self, sid, evt):
                events.append(evt)

        c = self._mod.SubAgent()
        with patch("app.messaging.message_broker.get_message_broker", return_value=FakeBroker()):
            c._publish_progress(self._session, {
                "name": "el", "status": "running", "started_at": time.time() - 3,
            })
        self.assertTrue(events)
        self.assertGreaterEqual(events[0]["elapsed_s"], 2.5)
        self.assertLessEqual(events[0]["elapsed_s"], 4.5)

    def test_elapsed_uses_finished_at(self):
        key = self._mod._sub_key(self._session, "fin")
        now = time.time()
        self._mod._SUBAGENTS[key] = {
            "name": "fin", "status": "done", "started_at": now - 30,
            "finished_at": now - 11.5, "result_content": "ok",
        }
        c = self._mod.SubAgent()
        self.assertAlmostEqual(c._cmd_status("fin", self._session)["elapsed_s"], 18.5, delta=0.1)
        self.assertAlmostEqual(c._cmd_results("fin", self._session)["elapsed_s"], 18.5, delta=0.1)
        item = [x for x in c._cmd_list(self._session)["subagents"] if x["name"] == "fin"][0]
        self.assertAlmostEqual(item["elapsed_s"], 18.5, delta=0.1)

    def test_done_message_is_compact(self):
        payload = {
            "name": "x", "success": True, "content": "a" * 5000,
            "iterations": 2, "tools_used": ["read"], "duration_s": 1.2,
        }
        msg = SubAgent._build_done_message(payload)
        self.assertIn("[子Agent完成]", msg)
        self.assertIn("[截断]", msg)
        self.assertLess(len(msg), 4300)

    def test_progress_publishes_to_broker(self):
        from unittest.mock import patch
        events = []

        class FakeBroker:
            def publish(self, sid, evt):
                events.append((sid, evt))

        c = self._mod.SubAgent()
        key = self._mod._sub_key(self._session, "px")
        self._mod._SUBAGENTS[key] = {
            "name": "px", "status": "running", "task": "t",
            "started_at": time.time() - 3, "_pushed_at": 0,
        }
        with patch("app.messaging.message_broker.get_message_broker", return_value=FakeBroker()):
            c._update_progress(key, {"type": "tool_start", "tool": "read"}, self._session)
        self.assertTrue(events, "应发布进度事件")
        self.assertEqual(events[0][0], self._session)
        self.assertEqual(events[0][1]["type"], "subagent_progress")
        self.assertEqual(events[0][1]["current_tool"], "read")

    def test_done_event_carries_tools_and_elapsed(self):
        from unittest.mock import patch
        events = []

        class FakeBroker:
            def publish(self, sid, evt):
                events.append(evt)

        with patch("app.messaging.message_broker.get_message_broker", return_value=FakeBroker()):
            self._mod.SubAgent._publish_done_event(self._session, {
                "name": "dx", "success": True, "content": "ok", "iterations": 2,
                "tools_used": ["read", "grep"], "tool_calls": 3, "duration_s": 8.5,
            })
        evt = events[0]
        self.assertEqual(evt["tool_calls"], 3, "完成事件应带工具调用次数")
        self.assertEqual(evt["tools_used"], ["read", "grep"])
        self.assertEqual(evt["elapsed_s"], 8.5, "完成事件应带真实耗时")

    def test_notify_wakes_idle_parent(self):
        from unittest.mock import patch
        calls = {}

        class FakeTaskMgr:
            def has_running_task(self, sid):
                return False

            async def start_task(self, **kwargs):
                calls["start"] = kwargs
                return True

            def enqueue_supplement_message(self, sid, payload):
                calls["queue"] = payload
                return True

        class FakeMgr:
            async def get_loop(self, sid):
                return object()

        class FakeSessionMgr:
            def get_or_create(self, sid):
                return type("S", (), {"memory": object()})()

        c = self._mod.SubAgent()
        payload = {"name": "n1", "success": True, "content": "结论", "iterations": 1,
                   "tools_used": ["read"], "duration_s": 0.5}
        with patch("app.server.task_manager.get_task_manager", return_value=FakeTaskMgr()), \
             patch("app.agent.loop.agent_loop_manager.AgentLoopManager.get_instance", return_value=FakeMgr()), \
             patch("app.agent.loop.session_manager.get_session_manager", return_value=FakeSessionMgr()), \
             patch("app.server.session_mode.get_mode", return_value="run"), \
             patch.object(self._mod, "_NOTIFY_RETRY_INTERVAL", 0.05), \
             patch("app.messaging.message_broker.get_message_broker", side_effect=RuntimeError("no broker")):
            fut = asyncio.run_coroutine_threadsafe(
                c._notify_parent(self._session, payload), _MAIN_LOOP
            )
            fut.result(timeout=5)
            delivered = self._wait_until(lambda: "start" in calls)

        self.assertTrue(delivered, "空闲父会话应自动起一轮")
        self.assertEqual(calls["start"]["session_id"], self._session)
        self.assertIn("[子Agent完成]", calls["start"]["user_input"])

    def test_notify_retries_until_task_running(self):
        from unittest.mock import patch
        state = {"attempt": 0, "queued": []}

        class FakeTaskMgr:
            def has_running_task(self, sid):
                return state["attempt"] >= 2

            async def start_task(self, **kwargs):
                state["attempt"] += 1
                return False

            def enqueue_supplement_message(self, sid, payload):
                if state["attempt"] >= 2:
                    state["queued"].append(payload)
                    return True
                return False

        class FakeMgr:
            async def get_loop(self, sid):
                return object()

        class FakeSessionMgr:
            def get_or_create(self, sid):
                return type("S", (), {"memory": object()})()

        c = self._mod.SubAgent()
        payload = {"name": "race", "success": True, "content": "结论",
                   "iterations": 1, "tools_used": [], "duration_s": 0.1}
        with patch("app.server.task_manager.get_task_manager", return_value=FakeTaskMgr()), \
             patch("app.agent.loop.agent_loop_manager.AgentLoopManager.get_instance", return_value=FakeMgr()), \
             patch("app.agent.loop.session_manager.get_session_manager", return_value=FakeSessionMgr()), \
             patch("app.server.session_mode.get_mode", return_value="run"), \
             patch.object(self._mod, "_NOTIFY_RETRY_INTERVAL", 0.05), \
             patch("app.messaging.message_broker.get_message_broker", side_effect=RuntimeError("no broker")):
            fut = asyncio.run_coroutine_threadsafe(
                c._notify_parent(self._session, payload), _MAIN_LOOP)
            fut.result(timeout=5)
            delivered = self._wait_until(lambda: bool(state["queued"]))

        self.assertTrue(delivered, "启动竞争失败后应重试并最终进入队列")
        self.assertIn("[子Agent完成]", state["queued"][0]["content"])


class TestSubAgentIntegration(unittest.TestCase):
    """单任务、并行、白名单"""

    def test_single_task(self):
        import components.sub_agent as subagent_mod
        engine = SimpleEngine("子任务完成")
        c = setup_subagent(lambda: engine)
        r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "s1", "task": "任务", "tools": ["read"]}]))
        item = r["results"][0]
        self.assertTrue(item["success"], f"应成功: {item}")
        self.assertIn("子任务完成", item["content"])
        self.assertNotIn("tool_traces", item, "默认不应回传工具轨迹")
        self.assertTrue(engine.closed, "engine 应被 close")

    def test_parallel_concurrency_limit(self):
        sid = f"conc_{uuid.uuid4().hex[:6]}"
        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        class TrackEngine(SlowEngine):
            async def chat(self, messages, tools=None, **kwargs):
                with lock:
                    active["now"] += 1
                    active["peak"] = max(active["peak"], active["now"])
                try:
                    return await super().chat(messages, tools=tools, **kwargs)
                finally:
                    with lock:
                        active["now"] -= 1

        c = setup_subagent(lambda: TrackEngine(0.3))
        old = c.MAX_PARALLEL_SUBAGENTS
        c.MAX_PARALLEL_SUBAGENTS = 2
        try:
            r = call_cmd(lambda: c._cmd_parallel(
                tasks=[{"name": f"c{i}", "task": "任务"} for i in range(6)],
                timeout=10, session_id=sid,
            ))
        finally:
            c.MAX_PARALLEL_SUBAGENTS = old
        self.assertTrue(r["success"], r)
        self.assertEqual(active["peak"], 2, f"应受并发上限约束，实际峰值 {active['peak']}")

    def test_parallel_concurrent(self):
        import components.sub_agent as subagent_mod
        engines = {"a": SlowEngine(0.5), "b": SlowEngine(0.5)}
        idx = {"n": 0}

        def clone():
            i = idx["n"]
            idx["n"] += 1
            return engines[["a", "b"][i % 2]]

        c = setup_subagent(clone)
        t0 = time.time()
        r = call_cmd(lambda: c._cmd_parallel(tasks=[
            {"name": "pa", "task": "任务A"},
            {"name": "pb", "task": "任务B"},
        ]))
        dur = time.time() - t0
        self.assertEqual(r["success_count"], 2, f"应全成功: {r}")
        self.assertLess(dur, 1.8, f"应并发（串行需 ~1s×2），实际 {dur:.2f}s")
        # 每个子 Agent 独立引擎
        self.assertEqual(engines["a"].call_count, 1)
        self.assertEqual(engines["b"].call_count, 1)

    def test_parallel_duplicate_names_are_split(self):
        import components.sub_agent as subagent_mod
        sid = f"dup_{uuid.uuid4().hex[:6]}"
        c = setup_subagent(lambda: SlowEngine(0.1))
        r = call_cmd(lambda: c._cmd_parallel(
            tasks=[
                {"name": "x", "task": "任务1"},
                {"name": "x", "task": "任务2"},
                {"name": "x", "task": "任务3"},
            ],
            timeout=5, session_id=sid,
        ))
        names = [item["name"] for item in r["results"]]
        self.assertEqual(names, ["x", "x_2", "x_3"], "同名任务应自动拆分")
        try:
            for name in names:
                res = c._cmd_results(name, session_id=sid)
                self.assertTrue(res["success"], res)
                self.assertFalse(res["running"])
                self.assertIn("完成", res["content"])
        finally:
            for key in [k for k in subagent_mod._SUBAGENTS if k.startswith(sid + "::")]:
                subagent_mod._SUBAGENTS.pop(key, None)

    def test_whitelist_isolation(self):
        import components.sub_agent as subagent_mod
        # 只授权 grep；shell 不属于默认自动附带 → mock 强求 shell 应被拒绝
        engine = ToolThenReplyEngine(first_tool="shell")
        c = setup_subagent(lambda: engine)
        r = call_cmd(lambda: c._cmd_parallel(
            tasks=[{"name": "iso", "task": "任务", "tools": '["grep"]'}], verbose=True))
        item = r["results"][0]
        traces = item.get("tool_traces", [])
        self.assertTrue(traces, "应有工具轨迹")
        self.assertIn("not allowed", str(traces[0]["result_preview"]))

    def test_string_json_compat(self):
        import components.sub_agent as subagent_mod
        c = setup_subagent(lambda: SimpleEngine("完成"))
        r = call_cmd(lambda: c._cmd_parallel(tasks=json.dumps([{"name": "j1", "task": "任务"}])))
        self.assertTrue(r["success"], f"字符串 JSON 应兼容: {r}")

    def test_timeout_param_cancels(self):
        from unittest.mock import patch
        import components.sub_agent as subagent_mod
        c = setup_subagent(lambda: SlowEngine(5))
        with patch.object(subagent_mod, "SUB_AGENT_TIMEOUT_SECONDS", 0.3):
            r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "to", "task": "任务"}]))
        self.assertFalse(r["success"])
        self.assertTrue(r.get("timeout"))
        self.assertIn("超时", r["error"])
        item = r["results"][0]
        self.assertTrue(item.get("timed_out"))
        for key in ("name", "success", "content", "iterations", "tools_used", "duration_s", "error"):
            self.assertIn(key, item, f"超时项结构应完整: {item}")

    def test_timeout_clamped_to_max(self):
        from unittest.mock import patch
        import components.sub_agent as subagent_mod
        c = setup_subagent(lambda: SlowEngine(5))
        with patch.object(subagent_mod, "SUB_AGENT_MAX_TIMEOUT_SECONDS", 0.3):
            r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "clamp", "task": "任务"}], timeout=99999))
        self.assertFalse(r["success"])
        self.assertTrue(r.get("timeout"))

    def test_invalid_timeout_falls_back_to_default(self):
        import components.sub_agent as subagent_mod
        c = setup_subagent(lambda: SimpleEngine("完成"))
        r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "bad", "task": "任务"}], timeout="abc"))
        self.assertTrue(r["success"], f"非法 timeout 应回退默认值: {r}")

    def test_nan_timeout_falls_back_to_default(self):
        from unittest.mock import patch
        import components.sub_agent as subagent_mod
        c = setup_subagent(lambda: SlowEngine(5))
        with patch.object(subagent_mod, "SUB_AGENT_TIMEOUT_SECONDS", 0.3):
            r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "nan", "task": "任务"}], timeout=float("nan")))
        self.assertTrue(r.get("timeout"), f"NaN 应回退默认值而非报错: {r}")

    def test_definition_exposes_optional_timeout(self):
        from app.core.util.cell_tool_adapter import CellToolAdapter
        d = CellToolAdapter(SubAgent()).definition
        params = d["function"]["parameters"]
        self.assertEqual(params["properties"]["timeout"]["type"], "number")
        self.assertNotIn("timeout", params["required"])


class TestSubAgentIsolation(unittest.TestCase):
    """异常隔离、资源清理、人格隔离"""

    def test_llm_failure_isolated(self):
        import components.sub_agent as subagent_mod
        c = setup_subagent(lambda: RaiseEngine())
        r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "f1", "task": "任务"}]))
        item = r["results"][0]
        self.assertFalse(item["success"], "LLM 挂应返回失败")
        self.assertIn("RuntimeError", str(item["error"]))
        self.assertEqual(r["total"], 1, "结果结构应完整")

    def test_tool_failure_isolated(self):
        import components.sub_agent as subagent_mod
        engine = ToolThenReplyEngine("read")
        def collect(self, names):
            return {"read": FakeTool("read", behavior="raise")}
        c = setup_subagent(lambda: engine, tool_collect=collect)
        r = call_cmd(lambda: c._cmd_parallel(
            tasks=[{"name": "b1", "task": "任务", "tools": ["read"]}], verbose=True))
        item = r["results"][0]
        self.assertTrue(item["success"], "工具失败不应让子 Agent 崩溃")
        self.assertIn("RuntimeError", str(item["tool_traces"][0]["result_preview"]))

    def test_no_temp_dir_leak(self):
        import components.sub_agent as subagent_mod
        import glob as glob_mod
        c = setup_subagent(lambda: SimpleEngine("完成"))
        for i in range(5):
            call_cmd(lambda: c._cmd_parallel(tasks=[{"name": f"t{i}", "task": "任务"}]))
        time.sleep(0.3)
        leaked = glob_mod.glob(os.path.join(tempfile.gettempdir(), "cellium_subagent_*"))
        self.assertEqual(leaked, [], f"临时目录应零残留: {leaked}")

    def test_engine_closed_on_construct_failure(self):
        from unittest.mock import patch
        import app.agent.loop.agent_loop as al_mod
        import components.sub_agent as subagent_mod

        holder = {"engine": None}
        c = setup_subagent(lambda: holder.__setitem__("engine", SimpleEngine("x")) or holder["engine"])
        with patch.object(al_mod, "AgentLoop", side_effect=RuntimeError("构造失败")):
            r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "cf", "task": "任务"}]))
        item = r["results"][0]
        self.assertFalse(item["success"])
        self.assertTrue(holder["engine"].closed, "构造失败时 engine 应被释放")

    def test_personality_isolated(self):
        import components.sub_agent as subagent_mod
        engine = CaptureEngine()
        c = setup_subagent(lambda: engine)
        call_cmd(lambda: c._cmd_parallel(tasks=[{
            "name": "pr", "task": "任务", "tools": ["read", "ls"],
            "persona": "你是勘察员", "constraints": "只读",
        }]))
        sys_prompt = engine.captured_system
        # 主 Agent 专属内容应隔离
        self.assertNotIn("get_gene", sys_prompt)
        self.assertNotIn("component.generate", sys_prompt)
        self.assertNotIn("skill_manager", sys_prompt)
        self.assertNotIn("你是用户的交互式桌面 AI 助手", sys_prompt)
        # 子 Agent 身份和工具铁律注入
        self.assertIn("[子 Agent 任务身份]", sys_prompt)
        self.assertIn("read：", sys_prompt)
        self.assertIn("ls：", sys_prompt)
        # 未授权工具不注入
        self.assertNotIn("shell：", sys_prompt)
        self.assertNotIn("edit：", sys_prompt)


class _RecordingEngine:
    """记录可用工具，先调用 edit 再回复"""

    def __init__(self):
        self.call_count = 0
        self.visible_tools = None

    async def chat(self, messages, tools=None, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            self.visible_tools = [d["function"]["name"] for d in (tools or [])]
            return MockResponse(
                content="先改文件",
                tool_calls=[MockToolCall("edit", {"file_path": "x.py", "old_string": "a", "new_string": "b"}, "c1")],
                finish_reason="tool_calls",
            )
        return MockResponse(content="子任务完成", finish_reason="stop")

    @property
    def model_info(self):
        from app.agent.llm.engine import ModelInfo
        return ModelInfo(8192, 4096, True)

    @property
    def context_window(self):
        return 8192

    async def health_check(self):
        return True

    async def close(self):
        pass


class TestPlanModeInheritance(unittest.TestCase):
    """子 Agent 必须继承父会话 Plan 权限"""

    def _setup(self):
        executed = []
        engine = _RecordingEngine()

        def collect(self, allowed_names):
            out = {}
            for n in ("read", "edit"):
                if n not in allowed_names:
                    continue
                tool = FakeTool(n)
                if n == "edit":
                    def _exec(args, session_id=None, platform_context=None):
                        executed.append("edit")
                        return {"success": True}
                    tool.execute_with_context = _exec
                out[n] = tool
            return out

        c = setup_subagent(lambda: engine, tool_collect=collect)
        return c, engine, executed

    def test_parallel_accepts_session_id(self):
        import inspect
        params = inspect.signature(SubAgent._cmd_parallel).parameters
        self.assertIn("session_id", params, "适配层需要 session_id 才能继承父会话模式")

    def test_plan_blocks_edit_in_subagent(self):
        from app.server.session_mode import set_mode
        c, engine, executed = self._setup()
        set_mode("parent_plan_mode", "plan", broadcast=False)

        r = call_cmd(lambda: c._cmd_parallel(
            tasks=[{"name": "p", "task": "只读调查", "tools": ["edit", "read"]}],
            session_id="parent_plan_mode",
            verbose=True,
        ))
        item = r["results"][0]
        self.assertNotIn("edit", engine.visible_tools or [], "Plan 模式下 edit 不应下发")
        self.assertNotIn("edit", executed, "Plan 模式下 edit 不应真正执行")
        traces = item.get("tool_traces") or []
        self.assertTrue(
            any("Plan 模式禁止" in str(t.get("result_preview", {})) for t in traces),
            f"应返回拦截原因: {traces}",
        )

    def test_adapter_passes_parent_session(self):
        """真实链路：CellToolAdapter 必须把父会话 id 传到 _cmd_parallel"""
        from app.core.util.cell_tool_adapter import CellToolAdapter
        from app.server.session_mode import set_mode

        c, _, executed = self._setup()
        set_mode("parent_adapter_plan", "plan", broadcast=False)
        adapter = CellToolAdapter(c)
        adapter.execute_with_context(
            {"command": "parallel", "tasks": [{"name": "p", "task": "调查", "tools": ["edit", "read"]}]},
            session_id="parent_adapter_plan",
        )
        self.assertNotIn("edit", executed, "适配层未传 session_id 会导致子 Agent 越权")

    def test_run_allows_edit_in_subagent(self):
        from app.server.session_mode import set_mode
        c, engine, executed = self._setup()
        set_mode("parent_run_mode", "run", broadcast=False)

        r = call_cmd(lambda: c._cmd_parallel(
            tasks=[{"name": "p", "task": "改文件", "tools": ["edit", "read"]}],
            session_id="parent_run_mode",
        ))
        item = r["results"][0]
        self.assertIn("edit", engine.visible_tools or [], "Run 模式应下发 edit")
        self.assertIn("edit", executed, "Run 模式下 edit 应正常执行")


class TestSubAgentMainChain(unittest.TestCase):
    """主 Agent → 子 Agent 全链路"""

    def test_main_agent_calls_subagent(self):
        import components.sub_agent as subagent_mod
        from app.agent.loop.agent_loop import AgentLoop
        from app.agent.loop.memory import MemoryManager
        from app.core.util.cell_tool_adapter import CellToolAdapter

        sub_engines = {"r1": SimpleEngine("审查结果1"), "r2": SimpleEngine("审查结果2")}
        idx = {"n": 0}

        def clone():
            i = idx["n"]
            idx["n"] += 1
            return sub_engines[["r1", "r2"][i % 2]]

        c = setup_subagent(clone)

        class MainEngine:
            def __init__(self):
                self.call_count = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.call_count += 1
                if self.call_count == 1:
                    return MockResponse(content="派发", tool_calls=[MockToolCall("sub_agent", {
                        "command": "parallel",
                        "tasks": [{"name": "r1", "task": "任务1"}, {"name": "r2", "task": "任务2"}],
                    }, "call_p")], finish_reason="tool_calls")
                return MockResponse(content="汇总完成", finish_reason="stop")

            @property
            def model_info(self):
                from app.agent.llm.engine import ModelInfo
                return ModelInfo(8192, 4096, True)

            @property
            def context_window(self):
                return 8192

            async def health_check(self):
                return True

            async def close(self):
                pass

        adapter = CellToolAdapter(c)
        loop = AgentLoop(
            llm_engine=MainEngine(),
            memory=MemoryManager(),
            three_layer_memory=None,
            tools={"sub_agent": adapter},
            max_iterations=3,
            session_id="main-chain",
            enable_heuristics=False,
            flash_mode=True,
            enable_learning=False,
            intent_enabled=False,
        )

        async def run_main():
            result = {}
            async for evt in loop.run_stream("并行审查"):
                if evt.get("type") == "done":
                    result = evt
            return result

        fut = asyncio.run_coroutine_threadsafe(run_main(), _MAIN_LOOP)
        result = fut.result(timeout=180)

        self.assertEqual(result.get("type"), "done", f"主 Agent 应完成: {result}")
        traces = result.get("tool_traces", [])
        self.assertEqual(len(traces), 1, "应有 1 条 sub_agent 轨迹")
        self.assertEqual(traces[0]["tool"], "sub_agent")
        self.assertTrue(traces[0]["success"], "sub_agent 应成功")
        self.assertEqual(traces[0]["result"].get("success_count"), 2, "两个子 Agent 都应成功")


if __name__ == "__main__":
    unittest.main(verbosity=2)
