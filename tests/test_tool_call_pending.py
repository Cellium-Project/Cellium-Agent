# -*- coding: utf-8 -*-
"""工具调用预显示：tool_call_pending 必须在 tool_start 之前发出，且带工具名与动作描述"""
import asyncio
import unittest
from unittest.mock import Mock

from app.agent.loop.agent_loop import AgentLoop
from app.agent.loop.memory import MemoryManager
from app.agent.llm.models import ChatResponse, ToolCall
from app.agent.shell.cellium_shell import CelliumShell


class _FakeStreamLLM:
    model = "fake-model"

    def __init__(self):
        self.turn = 0

    async def chat_stream(self, messages, tools=None, max_tokens=None, **kwargs):
        self.turn += 1
        if self.turn == 1:
            call = ToolCall(id="call_p1", name="pending_demo", arguments={"x": 1})
            yield {"type": "tool_call_start", "index": 0, "id": "call_p1", "name": "pending_demo"}
            yield {"type": "tool_calls", "calls": [call]}
            yield {"type": "done", "response": ChatResponse(
                content="", tool_calls=[call], model="fake-model", finish_reason="tool_calls",
            )}
        else:
            yield {"type": "content", "text": "完成"}
            yield {"type": "done", "response": ChatResponse(
                content="完成", tool_calls=[], model="fake-model", finish_reason="stop",
            )}


class TestToolCallPending(unittest.TestCase):
    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def _make_loop(self):
        loop = AgentLoop(
            llm_engine=_FakeStreamLLM(),
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )
        tool = Mock()
        tool.definition = {
            "type": "function",
            "function": {
                "name": "pending_demo",
                "description": "测试工具",
                "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}},
            },
        }
        tool.execute = Mock(return_value={"result": "ok"})
        tool.execute_with_context = Mock(return_value={"result": "ok"})
        loop.tools["pending_demo"] = tool
        loop._builtin_tools["pending_demo"] = tool
        loop._tool_executor.refresh_tools(loop.tools)
        return loop

    def test_pending_before_tool_start(self):
        loop = self._make_loop()

        async def run_test():
            events = []
            async for event in loop.run_stream("测试", memory=self.memory):
                events.append(event)
                if event.get("type") == "done":
                    break
            return events

        events = asyncio.run(run_test())
        types = [e.get("type") for e in events]

        self.assertIn("tool_call_pending", types)
        self.assertIn("tool_start", types)
        self.assertLess(types.index("tool_call_pending"), types.index("tool_start"))

        pending = next(e for e in events if e.get("type") == "tool_call_pending")
        self.assertEqual(pending["tool"], "pending_demo")
        self.assertEqual(pending["call_id"], "call_p1")
        self.assertTrue(pending["description"])

        start = next(e for e in events if e.get("type") == "tool_start")
        self.assertEqual(start["call_id"], pending["call_id"])


if __name__ == "__main__":
    unittest.main()
