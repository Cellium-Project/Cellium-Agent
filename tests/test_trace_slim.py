# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.loop.agent_loop import (
    _fold_old_traces, _slim_trace, _slim_trace_value,
    _TRACE_FOLD_TRIGGER, _TRACE_KEEP_RECENT,
)


class _MockToolCall:
    def __init__(self, name, arguments=None, call_id=None):
        self.name = name
        self.arguments = arguments or {}
        self.id = call_id or f"call_{name}_1"


class _MockResponse:
    def __init__(self, content=None, tool_calls=None, usage=None,
                 finish_reason=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.finish_reason = finish_reason
        self.reasoning_content = reasoning_content


class _MockLLM:
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
        self.chat_calls = []

    async def chat(self, messages, tools=None, **kwargs):
        self.chat_calls.append({"messages": messages, "tools": tools, **kwargs})
        if self.call_count < len(self.responses):
            r = self.responses[self.call_count]
            self.call_count += 1
            return r
        return _MockResponse(content="default response")


class TestSlimTrace(unittest.TestCase):

    def test_small_result_unchanged(self):
        t = {"tool": "file", "arguments": {"path": "a.txt"}, "result": {"status": "ok", "content": "short"},
             "duration_ms": 5, "success": True}
        s = _slim_trace(t)
        self.assertEqual(s["result"]["content"], "short")
        self.assertTrue(s["success"])
        self.assertNotIn("truncated", s)

    def test_big_result_truncated_with_flag(self):
        big = "x" * 100_000
        t = {"tool": "file", "arguments": {"path": "a.txt"}, "result": {"status": "ok", "content": big},
             "duration_ms": 5, "success": True}
        s = _slim_trace(t)
        self.assertLess(len(s["result"]["content"]), 9000)
        self.assertIn("截断", s["result"]["content"])
        self.assertTrue(s.get("truncated"))

    def test_arguments_16kb_limit(self):
        args = {"path": "a.txt", "content": "y" * 40_000}
        t = {"tool": "write", "arguments": args, "result": {"status": "ok"},
             "duration_ms": 5, "success": True}
        s = _slim_trace(t)
        self.assertLess(len(s["arguments"]["content"]), 18_000)
        self.assertGreater(len(s["arguments"]["content"]), 15_000)
        self.assertTrue(s.get("truncated"))

    def test_error_message_never_truncated(self):
        t = {"tool": "shell", "arguments": {"cmd": "ls"},
             "result": {"success": False, "error": "e" * 50_000, "message": "m" * 30_000, "error_type": "TimeoutError"},
             "duration_ms": 1, "success": False}
        s = _slim_trace(t)
        self.assertEqual(len(s["result"]["error"]), 50_000)
        self.assertEqual(len(s["result"]["message"]), 30_000)
        self.assertEqual(s["result"]["error_type"], "TimeoutError")
        self.assertFalse(s.get("truncated", False))

    def test_list_clipped_with_flag(self):
        t = {"tool": "ls", "arguments": {},
             "result": {"items": [f"f{i}" for i in range(1000)]},
             "duration_ms": 1, "success": True}
        s = _slim_trace(t)
        self.assertEqual(len(s["result"]["items"]), 20)
        self.assertTrue(s.get("truncated"))

    def test_deep_nesting_bounded(self):
        nested = {"a": {"b": {"c": {"d": {"e": "z" * 99999}}}}}
        s = _slim_trace_value(nested, 8 * 1024, [False])
        self.assertLess(len(str(s)), 500)

    def test_non_dict_result(self):
        t = {"tool": "x", "arguments": {}, "result": "plain text " * 5000,
             "duration_ms": 1, "success": True}
        s = _slim_trace(t)
        self.assertLess(len(s["result"]), 9000)
        self.assertTrue(s.get("truncated"))


def _mk_trace(i, big=True):
    content = f"{i}-" + "z" * (20 * 1024) if big else "ok"
    return _slim_trace({
        "tool": f"t{i}", "arguments": {"path": f"/f{i}.txt"},
        "result": {"status": "ok", "content": content},
        "duration_ms": i, "success": True,
    })


class TestFoldOldTraces(unittest.TestCase):

    def test_no_fold_below_trigger(self):
        traces = [_mk_trace(i) for i in range(_TRACE_FOLD_TRIGGER)]
        _fold_old_traces(traces)
        self.assertFalse(any(t.get("folded") for t in traces))

    def test_fold_one_step_per_append(self):
        traces = [_mk_trace(i) for i in range(_TRACE_FOLD_TRIGGER)]
        self.assertFalse(any(t.get("folded") for t in traces))
        traces.append(_mk_trace(_TRACE_FOLD_TRIGGER))
        _fold_old_traces(traces)
        self.assertTrue(traces[0].get("folded"))
        self.assertFalse(traces[-1].get("folded"))

    def test_recent_kept_intact(self):
        traces = [_mk_trace(i) for i in range(_TRACE_FOLD_TRIGGER + 10)]
        for _ in range(10):
            _fold_old_traces(traces)
        recent = traces[-_TRACE_KEEP_RECENT:]
        self.assertFalse(any(t.get("folded") for t in recent))
        old = traces[:-_TRACE_KEEP_RECENT]
        self.assertTrue(all(t.get("folded") for t in old))

    def test_folded_content_shrunk(self):
        traces = [_mk_trace(i) for i in range(_TRACE_FOLD_TRIGGER + 1)]
        _fold_old_traces(traces)
        f = traces[0]
        self.assertTrue(f["folded"])
        self.assertLess(len(f["result"]["content"]), 600)
        self.assertEqual(f["tool"], "t0")
        self.assertEqual(f["duration_ms"], 0)
        self.assertTrue(f["success"])

    def test_folded_error_kept_1000(self):
        traces = [_slim_trace({
            "tool": "boom", "arguments": {},
            "result": {"success": False, "error": "E" * 5000},
            "duration_ms": 1, "success": False,
        })]
        traces.extend(_mk_trace(i) for i in range(_TRACE_FOLD_TRIGGER))
        _fold_old_traces(traces)
        err = traces[0]["result"]["error"]
        self.assertLess(len(err), 1100)
        self.assertIn("截断", err)
        self.assertFalse(traces[0]["success"])

    def test_idempotent(self):
        traces = [_mk_trace(i) for i in range(_TRACE_FOLD_TRIGGER + 5)]
        for _ in range(5):
            _fold_old_traces(traces)
        folded = [t for t in traces if t.get("folded")]
        self.assertEqual(len(folded), len(traces) - _TRACE_KEEP_RECENT)


class TestBehaviorEquivalence(unittest.TestCase):
    """端到端验证：slim/fold 只影响 trace 副本，memory 与实时事件保持完整"""

    def setUp(self):
        from app.agent.shell.cellium_shell import CelliumShell
        self.shell = CelliumShell()

    def tearDown(self):
        self.shell.close()

    def _run_once(self, big_content):
        from app.agent.loop.agent_loop import AgentLoop
        from app.agent.loop.memory import MemoryManager

        llm = _MockLLM(responses=[
            _MockResponse(content="none"),
            _MockResponse(tool_calls=[_MockToolCall("big_tool", {"q": "x"})]),
            _MockResponse(content="done"),
        ])
        memory = MemoryManager()
        loop = AgentLoop(
            llm_engine=llm, shell=self.shell, memory=memory,
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

        tool = Mock()
        tool.definition = {"type": "function", "function": {
            "name": "big_tool", "description": "big",
            "parameters": {"type": "object", "properties": {}}}}
        tool.execute = Mock(return_value={"status": "ok", "content": big_content})
        tool.execute_with_context = Mock(return_value={"status": "ok", "content": big_content})
        loop.tools["big_tool"] = tool
        loop._builtin_tools["big_tool"] = tool
        loop._tool_executor.refresh_tools(loop.tools)

        async def _collect():
            return [e async for e in loop.run_stream("read big", memory=memory)]
        events = asyncio.run(_collect())
        return events, memory

    def test_realtime_event_and_memory_keep_full_result(self):
        big = "D" * (50 * 1024)
        events, memory = self._run_once(big)

        tr = [e for e in events if e.get("type") == "tool_result"]
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["result"]["content"], big)

        tool_msgs = [m for m in memory.messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn(big, tool_msgs[0]["content"])

    def test_done_trace_is_slimmed_copy(self):
        big = "D" * (50 * 1024)
        events, _ = self._run_once(big)

        done = [e for e in events if e.get("type") == "done"][0]
        trace = done["tool_traces"][0]
        self.assertEqual(trace["tool"], "big_tool")
        self.assertTrue(trace["success"])
        self.assertLess(len(trace["result"]["content"]), 9000)
        self.assertTrue(trace.get("truncated"))

    def test_llm_prompt_contains_full_result(self):
        """LLM 每轮看到的对话历史必须是完整结果，不受 trace 压缩影响"""
        big = "D" * (50 * 1024)
        events, memory = self._run_once(big)
        from app.agent.loop.agent_loop import AgentLoop
        # 第二次 LLM 调用的 messages 应包含完整工具结果
        full = "".join(m.get("content", "") for m in memory.messages if m["role"] == "tool")
        self.assertIn(big, full)


if __name__ == "__main__":
    unittest.main()
