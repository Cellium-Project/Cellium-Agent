import asyncio
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.loop.agent_loop import AgentLoop
from app.agent.loop import agent_loop_manager as manager_module


class _FakeLoopController:
    def __init__(self, stop_requested=False):
        self.is_stop_requested = stop_requested


class _FakeToolExecutor:
    def __init__(self, result=None, delay=0.0, error=None):
        self.result = result
        self.delay = delay
        self.error = error
        self.tools = {}

    def execute_sync(self, tool_call, session_id=None, platform_context=None):
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


def _make_loop(executor, timeout=900.0):
    loop = AgentLoop.__new__(AgentLoop)
    loop._tool_timeout = timeout
    loop._loop_controller = _FakeLoopController()
    loop._stop_event = asyncio.Event()
    loop.shell = None
    loop._tool_executor = executor
    return loop


class TestToolTimeout(unittest.TestCase):
    def test_slow_tool_times_out(self):
        loop = _make_loop(_FakeToolExecutor(result={"ok": True}, delay=5), timeout=0.2)
        started = time.time()
        result = asyncio.run(
            loop._execute_tool_cancellable(SimpleNamespace(name="slow"), session_id="s1")
        )
        elapsed = time.time() - started
        self.assertTrue(result.get("timeout"))
        self.assertNotIn("_stopped_by_user", result)
        self.assertLess(elapsed, 3)

    def test_fast_tool_returns_result(self):
        loop = _make_loop(_FakeToolExecutor(result={"ok": 1}), timeout=5)
        result = asyncio.run(
            loop._execute_tool_cancellable(SimpleNamespace(name="fast"), session_id="s1")
        )
        self.assertEqual(result, {"ok": 1})

    def test_zero_timeout_disables_timeout(self):
        loop = _make_loop(_FakeToolExecutor(result={"ok": 2}, delay=0.3), timeout=0)
        result = asyncio.run(
            loop._execute_tool_cancellable(SimpleNamespace(name="medium"), session_id="s1")
        )
        self.assertEqual(result, {"ok": 2})
        self.assertNotIn("timeout", result)

    def test_stop_during_execution(self):
        loop = _make_loop(_FakeToolExecutor(result={"ok": True}, delay=5), timeout=0)

        async def _scenario():
            task = asyncio.create_task(
                loop._execute_tool_cancellable(SimpleNamespace(name="slow"), session_id="s1")
            )
            await asyncio.sleep(0.2)
            loop._stop_event.set()
            return await task

        result = asyncio.run(_scenario())
        self.assertTrue(result.get("_stopped_by_user"))

    def test_tool_error_propagates(self):
        loop = _make_loop(_FakeToolExecutor(error=ValueError("boom")), timeout=5)
        result = asyncio.run(
            loop._execute_tool_cancellable(SimpleNamespace(name="bad"), session_id="s1")
        )
        self.assertEqual(result, {"error": "ValueError: boom"})

    def test_create_loop_passes_tool_timeout(self):
        manager = manager_module.AgentLoopManager()
        manager._tools = {}
        manager._llm_engine = object()
        manager._three_layer_memory = None
        manager._intent_enabled = False
        manager._intent_llm = None
        manager._global_config = {"tool_timeout": 123}

        with patch("app.agent.loop.agent_loop.AgentLoop") as fake_loop:
            manager._create_loop("s1")

        self.assertEqual(fake_loop.call_args.kwargs.get("tool_timeout"), 123)


if __name__ == "__main__":
    unittest.main()
