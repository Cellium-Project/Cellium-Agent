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
        self.assertEqual(c._allowed_tool_names('["read","grep"]'), ["read", "grep"])
        self.assertEqual(c._allowed_tool_names("read, grep, glob"), ["read", "grep", "glob"])
        self.assertEqual(c._allowed_tool_names(None), [])
        self.assertEqual(c._allowed_tool_names(["read"]), ["read"])

    def test_tools_blacklist(self):
        c = SubAgent()
        # 平台组件工具被过滤
        result = c._allowed_tool_names(["read", "weixin_files", "web_search", "config", "scheduler", "grep"])
        self.assertEqual(result, ["read", "grep"], f"禁用工具应被过滤: {result}")

    def test_tools_rule_extraction(self):
        c = SubAgent()
        rules = c._extract_tool_rules(["read", "config", "ls"])
        self.assertIn("**read 工具**", rules)
        self.assertIn("**ls 工具**", rules)
        self.assertNotIn("**config 工具**", rules, "config 已从工具铁律移除")
        self.assertNotIn("**shell 工具**", rules, "未授权工具不注入铁律")

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
        self.assertIn("**read 工具**", inj)
        self.assertIn("**grep 工具**", inj)

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
        self.assertTrue(engine.closed, "engine 应被 close")

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

    def test_whitelist_isolation(self):
        import components.sub_agent as subagent_mod
        # 只授权 grep，mock 强求 read → 应被拒绝
        engine = ToolThenReplyEngine(first_tool="read")
        c = setup_subagent(lambda: engine)
        r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "iso", "task": "任务", "tools": '["grep"]'}]))
        item = r["results"][0]
        traces = item.get("tool_traces", [])
        self.assertTrue(traces, "应有工具轨迹")
        self.assertIn("not allowed", str(traces[0]["result"]))

    def test_string_json_compat(self):
        import components.sub_agent as subagent_mod
        c = setup_subagent(lambda: SimpleEngine("完成"))
        r = call_cmd(lambda: c._cmd_parallel(tasks=json.dumps([{"name": "j1", "task": "任务"}])))
        self.assertTrue(r["success"], f"字符串 JSON 应兼容: {r}")


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
        r = call_cmd(lambda: c._cmd_parallel(tasks=[{"name": "b1", "task": "任务", "tools": ["read"]}]))
        item = r["results"][0]
        self.assertTrue(item["success"], "工具失败不应让子 Agent 崩溃")
        self.assertIn("RuntimeError", str(item["tool_traces"][0]["result"]))

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
        self.assertIn("**read 工具**", sys_prompt)
        self.assertIn("**ls 工具**", sys_prompt)
        # 未授权工具不注入
        self.assertNotIn("**shell 工具**", sys_prompt)
        self.assertNotIn("**edit 工具**", sys_prompt)


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
