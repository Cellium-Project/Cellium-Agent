# -*- coding: utf-8 -*-
"""
Agent Loop 核心功能测试
"""

import os
import sys
import unittest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.loop.agent_loop import AgentLoop
from app.agent.loop.memory import MemoryManager
from app.agent.shell.cellium_shell import CelliumShell


class MockLLMEngine:
    """模拟 LLM 引擎"""

    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0
        self.chat_calls = []

    async def chat(self, messages, tools=None, **kwargs):
        self.chat_calls.append({"messages": messages, "tools": tools, **kwargs})
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        return MockResponse(content="default response")


class MockToolCall:
    """模拟工具调用对象"""

    def __init__(self, name, arguments=None):
        self.name = name
        self.arguments = arguments or {}


class MockResponse:
    """模拟 LLM 响应"""

    def __init__(self, content=None, tool_calls=None, usage=None, finish_reason=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.finish_reason = finish_reason


class TestAgentLoopBasic(unittest.TestCase):
    """测试 AgentLoop 基本功能"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_agent_loop_initialization(self):
        """测试 AgentLoop 初始化"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.assertIsNotNone(loop)
        self.assertEqual(loop.session_id, "default")

    def test_agent_loop_with_custom_session(self):
        """测试自定义会话 ID"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            session_id="test_session",
            enable_heuristics=False,
            enable_learning=False,
        )

        self.assertEqual(loop.session_id, "test_session")

    def test_agent_loop_max_iterations(self):
        """测试最大迭代次数"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            max_iterations=5,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.assertEqual(loop.max_iterations, 5)

    def test_memory_manager_attachment(self):
        """测试记忆管理器关联"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.assertIsNotNone(loop.memory)
        self.assertIsInstance(loop.memory, MemoryManager)


class TestAgentLoopMemoryIntegration(unittest.TestCase):
    """测试 AgentLoop 与记忆系统集成"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_add_messages_to_memory(self):
        """测试添加消息到记忆"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.memory.add_user_message("test message")
        self.memory.add_assistant_message("test response")

        messages = self.memory.get_messages()
        self.assertEqual(len(messages), 2)

    def test_memory_manager_max_history(self):
        """测试记忆管理器最大历史"""
        memory = MemoryManager(max_history=3)

        for i in range(5):
            memory.add_user_message(f"message {i}")

        messages = memory.get_messages()
        self.assertLessEqual(len(messages), 3)


class TestAgentLoopToolRegistration(unittest.TestCase):
    """测试工具注册"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_register_tool(self):
        """测试注册工具"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        loop.register_tool("test", mock_tool)

        self.assertIn("test", loop.tools)


class TestAgentLoopHeuristics(unittest.TestCase):
    """测试启发式功能"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_heuristics_disabled(self):
        """测试禁用启发式"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.assertIsNone(loop.heuristics)

    def test_heuristics_enabled(self):
        """测试启用启发式"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=True,
            enable_learning=False,
        )

        self.assertIsNotNone(loop.heuristics)


class TestAgentLoopFlashMode(unittest.TestCase):
    """测试 Flash 模式"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_flash_mode_enabled(self):
        """测试启用 Flash 模式"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            flash_mode=True,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.assertTrue(loop.flash_mode)

    def test_flash_mode_disabled(self):
        """测试禁用 Flash 模式"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            flash_mode=False,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.assertFalse(loop.flash_mode)


class TestAgentLoopStop(unittest.TestCase):
    """测试停止功能"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_stop_request(self):
        """测试请求停止"""
        llm = MockLLMEngine()

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        loop.stop()
        self.assertTrue(loop._loop_controller._stop_requested)


class TestAgentLoopRuntimeGuards(unittest.TestCase):
    """测试运行时硬约束"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    @unittest.skip("存在 _loop_state 为空引用 bug，待修复")
    def test_run_returns_stop_result_when_max_iterations_exceeded(self):
        llm = MockLLMEngine(responses=[
            MockResponse(tool_calls=[MockToolCall("unknown_tool", {"value": 1})]),
        ])
        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            max_iterations=1,
            enable_heuristics=False,
            enable_learning=False,
        )

        result = asyncio.run(loop.run("please keep trying", memory=self.memory))

        self.assertEqual(result["type"], "done")
        self.assertEqual(result["stop_reason"], "max_iterations_exceeded")
        self.assertFalse(result["completed"])

    def test_compress_constraint_passes_real_max_tokens(self):
        llm = MockLLMEngine(responses=[MockResponse(content="final answer")])
        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        loop.control_loop = Mock()
        loop.control_loop.start_session = Mock()
        loop.control_loop.step = Mock(return_value=Mock(action_type="compress", should_stop=False, force_memory_compact=False))
        loop.control_loop.end_round = Mock(return_value=0.5)
        loop.control_loop.end_session = Mock()
        loop._constraint_renderer = Mock()
        loop._constraint_renderer.render = Mock(return_value=Mock(trigger_reason="compress", max_tokens=42, force_stop=False, forbidden=[]))
        loop._constraint_renderer.render_combined = Mock(return_value="")

        result = asyncio.run(loop.run("compress this", memory=self.memory))

        self.assertEqual(result["type"], "done")
        self.assertEqual(llm.chat_calls[0]["max_tokens"], 42)

    def test_forbidden_tool_is_blocked_before_execution(self):
        llm = MockLLMEngine(responses=[
            MockResponse(tool_calls=[MockToolCall("forbidden_tool", {"a": 1})]),
            MockResponse(content="fallback answer"),
        ])
        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        loop.control_loop = Mock()
        loop.control_loop.start_session = Mock()
        loop.control_loop.step = Mock(return_value=Mock(action_type="redirect", should_stop=False, force_memory_compact=False))
        loop.control_loop.end_round = Mock(return_value=0.0)
        loop.control_loop.end_session = Mock()
        loop._constraint_renderer = Mock()
        loop._constraint_renderer.render = Mock(return_value=Mock(trigger_reason="redirect", max_tokens=0, force_stop=False, forbidden=["forbidden_tool"]))
        loop._constraint_renderer.render_combined = Mock(return_value="")
        loop.tools["forbidden_tool"] = Mock()
        loop._builtin_tools["forbidden_tool"] = loop.tools["forbidden_tool"]
        loop._tool_executor.refresh_tools(loop.tools)

        async def _collect_events():
            events = []
            async for event in loop.run_stream("use blocked tool", memory=self.memory):
                events.append(event)
            return events

        events = asyncio.run(_collect_events())
        tool_results = [e for e in events if e["type"] == "tool_result"]

        self.assertTrue(tool_results)
        self.assertTrue(tool_results[0]["result"].get("blocked"))
        self.assertIn("blocked by current control decision", tool_results[0]["result"]["error"])


class TestAgentLoopMemoryPersistence(unittest.TestCase):
    """测试 AgentLoop 持久化接线"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_persist_conversation_uses_unified_memory_api(self):
        llm = MockLLMEngine()
        three_layer_memory = Mock()
        three_layer_memory.memory_dir = "memory"
        three_layer_memory.persist_session = Mock(return_value="archive-1")

        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            three_layer_memory=three_layer_memory,
            enable_heuristics=False,
            enable_learning=False,
        )

        self.memory.add_user_message("用户消息")
        self.memory.add_assistant_message("助手消息")
        loop._persist_conversation("用户消息", "助手消息", session_id="session-z", memory=self.memory)

        three_layer_memory.persist_session.assert_called_once()
        kwargs = three_layer_memory.persist_session.call_args.kwargs
        self.assertEqual(kwargs["session_id"], "session-z")
        self.assertEqual(kwargs["messages"], self.memory.get_messages())


if __name__ == "__main__":
    unittest.main()
