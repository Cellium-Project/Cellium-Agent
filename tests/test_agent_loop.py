# -*- coding: utf-8 -*-
"""
Agent Loop 核心功能测试
"""

import json
import os
import sys
import unittest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

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

    def __init__(self, name, arguments=None, call_id=None):
        self.name = name
        self.arguments = arguments or {}
        self.id = call_id or f"call_{name}_1"


class MockResponse:
    """模拟 LLM 响应"""

    def __init__(self, content=None, tool_calls=None, usage=None, finish_reason=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.finish_reason = finish_reason
        self.reasoning_content = reasoning_content


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


class TestGoalDetection(unittest.TestCase):
    """测试目标检测与相似度判断"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_should_update_goal_when_empty(self):
        self.assertTrue(self.loop._should_update_goal("新任务", ""))

    def test_should_update_goal_on_new_task_keyword_chinese(self):
        result = self.loop._should_update_goal(
            "新任务：帮我分析这个文件",
            "旧的目标内容"
        )
        self.assertTrue(result)

    def test_should_update_goal_on_new_task_keyword_english(self):
        result = self.loop._should_update_goal(
            "new task: help me analyze this",
            "old goal"
        )
        self.assertTrue(result)

    def test_should_update_goal_on_cancel_keyword(self):
        result = self.loop._should_update_goal(
            "不对，我不要这个了",
            "当前目标"
        )
        self.assertTrue(result)

    def test_should_not_update_goal_for_continuation(self):
        result = self.loop._should_update_goal(
            "继续完成这件事",
            "完成这个数据分析任务"
        )
        self.assertFalse(result)

    def test_is_similar_goal_high_overlap_english(self):
        result = self.loop._is_similar_goal(
            "analyze today's log file",
            "analyze today's log"
        )
        self.assertTrue(result)

    def test_is_similar_goal_low_overlap(self):
        result = self.loop._is_similar_goal(
            "write an email for me",
            "analyze today data"
        )
        self.assertFalse(result)

    def test_is_similar_goal_empty_words(self):
        result = self.loop._is_similar_goal("", "a")
        self.assertFalse(result)

    def test_is_similar_goal_same_words(self):
        result = self.loop._is_similar_goal(
            "analyze data and report",
            "analyze data and report"
        )
        self.assertTrue(result)


class TestThinkingJSONParsing(unittest.TestCase):
    """测试 JSON 思考块解析"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_is_thinking_json_codeblock_format(self):
        content = '```json\n{"reasoning": "思考中", "action": "test"}\n```'
        self.assertTrue(self.loop._is_thinking_json(content))

    def test_is_thinking_json_inline_format(self):
        content = '{"reasoning": "思考", "action": "test"}'
        self.assertTrue(self.loop._is_thinking_json(content))

    def test_is_not_thinking_json_plain_text(self):
        content = "普通文本，没有 JSON"
        self.assertFalse(self.loop._is_thinking_json(content))

    def test_is_thinking_json_empty(self):
        self.assertFalse(self.loop._is_thinking_json(""))
        self.assertFalse(self.loop._is_thinking_json(None))

    def test_is_thinking_json_invalid_json(self):
        content = '```json\n{invalid json}\n```'
        self.assertFalse(self.loop._is_thinking_json(content))

    def test_is_thinking_json_missing_keys(self):
        content = '{"reasoning": "思考"}'
        self.assertFalse(self.loop._is_thinking_json(content))

    def test_extract_text_from_codeblock(self):
        content = '```json\n{"reasoning": "我需要调用工具", "action": "tool_call"}\n```\n这是后续文本'
        is_json, reasoning, after = self.loop._extract_text_from_thinking(content)
        self.assertTrue(is_json)
        self.assertEqual(reasoning, "我需要调用工具")
        self.assertIn("这是后续文本", after)

    def test_extract_text_from_inline_json(self):
        content = '{"reasoning": "思考", "action": "respond"}'
        is_json, reasoning, after = self.loop._extract_text_from_thinking(content)
        self.assertTrue(is_json)
        self.assertEqual(reasoning, "思考")
        self.assertEqual(after, "")

    def test_extract_text_from_plain(self):
        content = "普通回复文本"
        is_json, reasoning, after = self.loop._extract_text_from_thinking(content)
        self.assertFalse(is_json)
        self.assertEqual(reasoning, "")
        self.assertEqual(after, "普通回复文本")

    def test_extract_text_from_empty(self):
        is_json, reasoning, after = self.loop._extract_text_from_thinking("")
        self.assertFalse(is_json)
        self.assertEqual(reasoning, "")
        self.assertEqual(after, "")

    def test_extract_text_invalid_json_returns_whole(self):
        content = '```json\n{broken}\n```\n后续文本'
        is_json, reasoning, after = self.loop._extract_text_from_thinking(content)
        self.assertFalse(is_json)
        self.assertEqual(after, content)


class TestGetLastAssistantMessage(unittest.TestCase):
    """测试获取最后助手消息"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_get_last_assistant_skips_thinking(self):
        self.memory.add_user_message("问题")
        self.memory.add_assistant_message('{"reasoning": "思考", "action": "test"}')
        self.memory.add_assistant_message("真实回复")

        result = self.loop._get_last_assistant_message(self.memory, skip_thinking=True)
        self.assertEqual(result, "真实回复")

    def test_get_last_assistant_returns_thinking_when_not_skipping(self):
        self.memory.add_assistant_message('{"reasoning": "思考", "action": "test"}')

        result = self.loop._get_last_assistant_message(self.memory, skip_thinking=False)
        self.assertIn("思考", result)

    def test_get_last_assistant_empty_memory(self):
        result = self.loop._get_last_assistant_message(self.memory)
        self.assertEqual(result, "")

    def test_get_last_assistant_no_assistant_message(self):
        self.memory.add_user_message("用户消息")
        result = self.loop._get_last_assistant_message(self.memory)
        self.assertEqual(result, "")


class TestToolExecution(unittest.TestCase):
    """测试工具执行流程"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    async def _collect_events(self, **kwargs):
        events = []
        async for event in self.loop.run_stream(**kwargs):
            events.append(event)
        return events

    def test_tool_execution_blocked_by_constraint(self):
        """被禁止的工具应被阻止执行"""
        self.loop.control_loop = Mock()
        self.loop.control_loop.start_session = Mock()
        self.loop.control_loop.step = Mock(return_value=Mock(
            action_type="redirect", should_stop=False, force_memory_compact=False
        ))
        self.loop.control_loop.end_round = Mock(return_value=0.0)
        self.loop.control_loop.end_session = Mock()
        self.loop._constraint_renderer = Mock()
        self.loop._constraint_renderer.render = Mock(return_value=Mock(
            trigger_reason="redirect", max_tokens=0, force_stop=False, forbidden=["forbidden_tool"]
        ))
        self.loop._constraint_renderer.render_combined = Mock(return_value="")

        from app.agent.loop.memory import MemoryManager
        from unittest.mock import AsyncMock
        self.loop.tools["forbidden_tool"] = Mock()
        self.loop._builtin_tools["forbidden_tool"] = self.loop.tools["forbidden_tool"]
        self.loop._tool_executor.refresh_tools(self.loop.tools)

        self.llm.responses = [
            MockResponse(tool_calls=[MockToolCall("forbidden_tool", {"a": 1})]),
            MockResponse(content="最终回复"),
        ]

        async def run_test():
            events = []
            async for event in self.loop.run_stream("测试", memory=self.memory):
                events.append(event)
                if event.get("type") == "done":
                    break
            return events

        events = asyncio.run(run_test())
        tool_results = [e for e in events if e.get("type") == "tool_result"]
        self.assertTrue(len(tool_results) > 0)
        self.assertTrue(tool_results[0]["result"].get("blocked"))

    def test_tool_execution_error_handled(self):
        """工具执行异常应被捕获并返回错误结果"""
        error_tool = Mock()
        error_tool.definition = {"name": "error_tool", "description": "错误工具"}

        self.loop.tools["error_tool"] = error_tool
        self.loop._builtin_tools["error_tool"] = error_tool

        async def fake_execute(tool_call, session_id=None, platform_context=None):
            return {"error": "工具执行失败"}

        original_execute = self.loop._tool_executor.execute
        self.loop._tool_executor.execute = fake_execute

        try:
            self.llm.responses = [
                MockResponse(tool_calls=[MockToolCall("error_tool", {})]),
                MockResponse(content="兜底回复"),
            ]

            async def run_test():
                events = []
                async for event in self.loop.run_stream("测试", memory=self.memory):
                    events.append(event)
                    if event.get("type") == "done":
                        break
                return events

            events = asyncio.run(run_test())
            tool_results = [e for e in events if e.get("type") == "tool_result"]
            self.assertTrue(len(tool_results) > 0, f"未收到 tool_result 事件，事件类型: {[e.get('type') for e in events]}")
            self.assertIn("error", tool_results[0].get("result", {}))
        finally:
            self.loop._tool_executor.execute = original_execute


class TestSessionLifecycle(unittest.TestCase):
    """测试会话生命周期（开始/结束/异常）"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_finalize_session_publishes_events(self):
        with patch.object(self.loop._event_publisher, 'publish_response_complete') as mock_pub:
            result = self.loop._finalize_session(
                user_input="测试",
                effective_session="s1",
                effective_memory=self.memory,
                tool_traces=[],
                iteration=1,
                start_time=0,
                final_content="回复",
                reason="complete",
            )
            mock_pub.assert_called_once()
            self.assertEqual(result["type"], "done")
            self.assertEqual(result["content"], "回复")
            self.assertTrue(result["completed"])

    def test_finalize_session_with_error(self):
        result = self.loop._finalize_session(
            user_input="测试",
            effective_session="s1",
            effective_memory=self.memory,
            tool_traces=[],
            iteration=1,
            start_time=0,
            final_content="",
            reason="error",
            completed=False,
            error=True,
        )
        self.assertEqual(result["type"], "done")
        self.assertFalse(result["completed"])

    def test_finalize_session_persists_conversation(self):
        three_layer_memory = Mock()
        three_layer_memory.memory_dir = "memory"
        three_layer_memory.persist_session = Mock(return_value="archive-1")
        self.loop.three_layer_memory = three_layer_memory

        self.loop._finalize_session(
            user_input="用户问题",
            effective_session="s1",
            effective_memory=self.memory,
            tool_traces=[],
            iteration=1,
            start_time=0,
            final_content="助手回复",
            reason="complete",
        )
        three_layer_memory.persist_session.assert_called_once()

    def test_cleanup_incomplete_tool_calls(self):
        """应清理没有对应 tool_result 的 tool_call"""
        self.memory.add_user_message("问题")
        self.memory.add_tool_calls_batch([
            {"tool_name": "tool1", "arguments": {}, "tool_call_id": "tc-1"}
        ], content="调用工具")
        self.memory.add_tool_result("tc-1", {"result": "ok"})
        self.memory.add_tool_calls_batch([
            {"tool_name": "tool2", "arguments": {}, "tool_call_id": "tc-2"}
        ], content="再调用")

        self.loop._cleanup_incomplete_tool_calls(self.memory)
        messages = self.memory.get_messages()
        assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        tc_ids = set()
        for m in assistant_msgs:
            for tc in m.get("tool_calls", []):
                tc_ids.add(tc.get("id"))
        self.assertNotIn("tc-2", tc_ids)
        self.assertIn("tc-1", tc_ids)


class TestConstraintResolution(unittest.TestCase):
    """测试运行时约束解析"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_resolve_max_tokens_none_constraint(self):
        result = self.loop._resolve_runtime_max_tokens(None)
        self.assertIsNone(result)

    def test_resolve_max_tokens_with_constraint(self):
        constraint = Mock(max_tokens=128)
        result = self.loop._resolve_runtime_max_tokens(constraint)
        self.assertEqual(result, 128)

    def test_get_forbidden_tool_names_empty(self):
        constraint = Mock(forbidden=[])
        result = self.loop._get_forbidden_tool_names(constraint)
        self.assertEqual(result, set())

    def test_get_forbidden_tool_names_with_tools(self):
        tool_a = Mock()
        tool_b = Mock()
        self.loop.tools["tool_a"] = tool_a
        self.loop.tools["tool_b"] = tool_b
        constraint = Mock(forbidden=["tool_a", "tool_b", "non_existent"])
        result = self.loop._get_forbidden_tool_names(constraint)
        self.assertEqual(result, {"tool_a", "tool_b"})

    def test_get_forbidden_tool_names_none(self):
        result = self.loop._get_forbidden_tool_names(None)
        self.assertEqual(result, set())


class TestRunStreamIntegration(unittest.TestCase):
    """测试 run_stream 主流程（无 Mock control_loop，使用真实逻辑）"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_run_stream_simple_response_no_tools(self):
        """最简单的场景：直接 LLM 回复，无工具调用"""
        llm = MockLLMEngine(responses=[MockResponse(content="直接回复")])
        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

        async def run_test():
            events = []
            async for event in loop.run_stream("你好", memory=self.memory):
                events.append(event)
                if event.get("type") in ("done", "error"):
                    break
            return events

        events = asyncio.run(run_test())
        done_events = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done_events), 1)
        self.assertEqual(done_events[0]["content"], "直接回复")
        self.assertTrue(done_events[0]["completed"])

    def test_run_stream_preserves_user_input(self):
        """验证用户输入被记录到 memory"""
        llm = MockLLMEngine(responses=[MockResponse(content="ok")])
        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

        async def run_test():
            async for event in loop.run_stream("测试输入", memory=self.memory):
                if event.get("type") in ("done", "error"):
                    return

        asyncio.run(run_test())
        messages = self.memory.get_messages()
        user_msgs = [m for m in messages if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1)
        self.assertEqual(user_msgs[0]["content"], "测试输入")

    def test_run_stream_detects_max_iterations(self):
        """超过 max_iterations 应被终止"""
        llm = MockLLMEngine(responses=[
            MockResponse(content="持续循环", tool_calls=[MockToolCall("missing_tool", {})])
        ] * 5)
        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            max_iterations=2,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

        async def run_test():
            events = []
            async for event in loop.run_stream("test", memory=self.memory):
                events.append(event)
                if event.get("type") in ("done", "error", "stopped", "control_loop_stop"):
                    return events
            return events

        events = asyncio.run(run_test())
        stop_events = [e for e in events if e.get("type") in ("stopped", "control_loop_stop", "done")]
        self.assertGreater(len(stop_events), 0)

    def test_run_stream_handles_llm_exception(self):
        """LLM 异常应被捕获并返回 error 事件"""
        llm = MockLLMEngine()
        llm.chat = AsyncMock(side_effect=Exception("LLM 服务不可用"))
        loop = AgentLoop(
            llm_engine=llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

        async def run_test():
            events = []
            async for event in loop.run_stream("test", memory=self.memory):
                events.append(event)
                if event.get("type") in ("done", "error"):
                    return events
            return events

        events = asyncio.run(run_test())
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("LLM 服务不可用", error_events[0]["error"])


class TestToolRefresh(unittest.TestCase):
    """测试工具刷新机制"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_register_tool_updates_both_dicts(self):
        new_tool = Mock()
        new_tool.definition = {"name": "new_tool"}
        self.loop.register_tool("new_tool", new_tool)
        self.assertIn("new_tool", self.loop.tools)
        self.assertIn("new_tool", self.loop._builtin_tools)

    def test_on_tools_changed_callback(self):
        new_tools = {"a": Mock(), "b": Mock()}
        self.loop._on_tools_changed(new_tools)
        self.assertEqual(self.loop.tools, new_tools)

    def test_refresh_tools_no_registry_uses_builtin(self):
        with patch("app.agent.loop.agent_loop.get_component_tool_registry") as mock_get:
            mock_registry = Mock()
            mock_registry.get_component_tools = Mock(return_value={})
            mock_get.return_value = mock_registry
            self.loop._refresh_tools()
            self.assertEqual(self.loop.tools, self.loop._builtin_tools)

    def test_refresh_tools_merges_component_and_builtin(self):
        with patch("app.agent.loop.agent_loop.get_component_tool_registry") as mock_get:
            component_tool = Mock()
            component_tool.definition = {"name": "comp_tool"}
            mock_registry = Mock()
            mock_registry.get_component_tools = Mock(return_value={"comp_tool": component_tool})
            mock_get.return_value = mock_registry

            self.loop._refresh_tools()
            self.assertIn("comp_tool", self.loop.tools)
            for k in self.loop._builtin_tools:
                self.assertIn(k, self.loop.tools)

    def test_refresh_tools_registry_failure_fallback(self):
        with patch("app.agent.loop.agent_loop.get_component_tool_registry") as mock_get:
            mock_get.side_effect = Exception("Registry error")
            self.loop._refresh_tools()
            self.assertEqual(self.loop.tools, self.loop._builtin_tools)


class TestHybridState(unittest.TestCase):
    """测试 Hybrid 状态同步"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()
        self.loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
            enable_hybrid=True,
        )

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_sync_hybrid_state_disabled(self):
        self.loop._hybrid_controller = None
        result = self.loop._sync_hybrid_state()
        self.assertIsNone(result)

    def test_sync_hybrid_state_no_loop_state(self):
        self.loop._loop_state = None
        result = self.loop._sync_hybrid_state()
        self.assertIsNone(result)


class TestMemoryConfigLoading(unittest.TestCase):
    """测试记忆配置加载"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_load_memory_config_default(self):
        loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )
        self.assertIn("short_term", loop._mem_config)
        self.assertIn("max_history", loop._mem_config["short_term"])

    def test_load_learning_config_default(self):
        loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )
        result = loop._get_learning_config()
        self.assertIsInstance(result, dict)


class TestPromptContextBuilder(unittest.TestCase):
    """测试 PromptContextBuilder 上下文构建"""

    def test_build_first_round_structure(self):
        """第一轮应包含 system + prefix + user_input"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        messages = builder.build_first_round(
            user_input="你好",
            session_messages=[],
        )

        self.assertGreaterEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertTrue(any(m.get("role") == "user" and "你好" in m.get("content", "") for m in messages))

    def test_build_first_round_with_long_term_memory(self):
        """有长期记忆时应检索并注入"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        mock_memory = Mock()
        mock_memory.retrieve_context.return_value = [
            {"content": "用户之前问过Python问题", "score": 0.9}
        ]
        mock_memory.format_retrieved_context.return_value = "用户之前问过Python问题"

        builder = PromptContextBuilder(
            three_layer_memory=mock_memory,
            flash_mode=False,
        )

        messages = builder.build_first_round(
            user_input="继续上次的问题",
            session_messages=[],
        )

        mock_memory.retrieve_context.assert_called()
        all_content = " ".join(m["content"] for m in messages if m.get("role") == "user")
        self.assertIn("长期记忆", all_content)

    def test_build_first_round_no_memory(self):
        """无长期记忆时应正常构建"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(three_layer_memory=None, flash_mode=False)

        messages = builder.build_first_round(
            user_input="你好",
            session_messages=[],
        )

        self.assertEqual(len(messages), 2)
        self.assertNotIn("长期记忆", messages[1]["content"])

    def test_build_first_round_with_system_injection(self):
        """应正确注入系统指令"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        messages = builder.build_first_round(
            user_input="执行任务",
            session_messages=[],
            system_injection="优先使用文件工具",
        )

        all_content = " ".join(m["content"] for m in messages if m.get("role") == "user")
        self.assertIn("系统指令", all_content)
        self.assertIn("优先使用文件工具", all_content)

    def test_build_first_round_with_guidance(self):
        """应正确注入引导消息"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        messages = builder.build_first_round(
            user_input="测试",
            session_messages=[],
            guidance_message="建议使用read_file",
        )

        self.assertIn("系统引导", messages[-1]["content"])

    def test_build_subsequent_round_different_structure(self):
        """后续轮次不应重复 system_injection"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        first_round = builder.build_first_round(
            user_input="第一轮",
            session_messages=[{"role": "assistant", "content": "回复1"}],
        )

        subsequent_round = builder.build_subsequent_round(
            session_messages=[
                {"role": "user", "content": "第一轮"},
                {"role": "assistant", "content": "回复1"},
                {"role": "user", "content": "第二轮"},
            ],
        )

        self.assertEqual(first_round[0]["role"], "system")
        self.assertEqual(subsequent_round[0]["role"], "system")
        self.assertNotIn("系统指令", subsequent_round[1]["content"])

    def test_system_message_contains_thought_schema(self):
        """system 消息必须包含 THOUGHT_SCHEMA（关键结构）"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        messages = builder.build_first_round("test", [])
        system_msg = messages[0]

        self.assertEqual(system_msg["role"], "system")
        # THOUGHT_SCHEMA 是 JSON 思考格式定义，必须存在
        self.assertIn("reasoning", system_msg["content"])
        self.assertIn("action", system_msg["content"])

    def test_system_message_contains_identity(self):
        """system 消息必须包含身份定义"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        messages = builder.build_first_round("test", [])
        system_msg = messages[0]

        # 身份定义（Cellium Agent）必须存在
        self.assertIn("Cellium", system_msg["content"])
        self.assertIn("桌面助手", system_msg["content"])

    def test_system_message_unchanged_across_rounds(self):
        """system 消息在多轮对话中应保持不变"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        first = builder.build_first_round("第一轮", [])
        second = builder.build_first_round("第二轮", [])
        subsequent = builder.build_subsequent_round([{"role": "user", "content": "test"}])

        # 三种调用方式的 system 消息应该相同
        self.assertEqual(first[0]["content"], second[0]["content"])
        self.assertEqual(first[0]["content"], subsequent[0]["content"])

    def test_system_message_not_modified_by_injection(self):
        """system_injection 不会修改 system 消息"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        normal = builder.build_first_round("test", [])
        with_injection = builder.build_first_round("test", [], system_injection="注入内容")

        # system 消息应该完全相同
        self.assertEqual(normal[0]["content"], with_injection[0]["content"])
        self.assertEqual(normal[0]["role"], "system")

    def test_build_subsequent_round_with_auto_hints(self):
        """后续轮次应包含 auto_hints"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        messages = builder.build_subsequent_round(
            session_messages=[{"role": "user", "content": "test"}],
            auto_hints="建议使用 file 工具",
        )

        all_content = " ".join(m["content"] for m in messages if m.get("role") == "user")
        self.assertIn("工具使用提示", all_content)

    def test_prefix_cache_works(self):
        """固定人格应被缓存"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        msg1 = builder.build_first_round("第一", [])
        msg2 = builder.build_first_round("第二", [])

        self.assertEqual(msg1[0]["content"], msg2[0]["content"])

    def test_retrieve_long_term_memory_returns_none_without_memory(self):
        """无三层记忆时应返回 None"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(three_layer_memory=None)

        result = builder._retrieve_long_term_memory("查询")
        self.assertIsNone(result)

    def test_retrieve_long_term_memory_handles_error(self):
        """检索失败时应返回 None"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        mock_memory = Mock()
        mock_memory.retrieve_context.side_effect = Exception("检索失败")

        builder = PromptContextBuilder(three_layer_memory=mock_memory)

        result = builder._retrieve_long_term_memory("查询")
        self.assertIsNone(result)

    def test_build_context_message_includes_date(self):
        """上下文信息应包含日期"""
        from app.agent.loop.prompt_context_builder import PromptContextBuilder

        builder = PromptContextBuilder(flash_mode=True)

        context = builder._build_static_context()

        self.assertIn("日期", context)
        self.assertIn("系统环境", context)


class TestStopMethod(unittest.TestCase):
    """测试 stop() 方法"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_stop_requests_termination(self):
        """stop() 应请求终止当前推理"""
        loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            max_iterations=10,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

        loop.stop()
        self.assertTrue(loop._loop_controller._stop_requested)


class TestEndToEndIntegration(unittest.TestCase):
    """端到端集成测试"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory = MemoryManager()
        self.llm = MockLLMEngine()

    def tearDown(self):
        self.shell.close()
        self.memory.clear()

    def test_complete_workflow_with_tools_and_iterations(self):
        """完整工作流：用户输入 → LLM → 工具调用 → 结果 → LLM → 回复"""
        self.llm.responses = [
            MockResponse(tool_calls=[MockToolCall("test_tool", {"arg": "value"})]),
            MockResponse(content="好的，已完成"),
        ]

        loop = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            max_iterations=3,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )

        async def fake_execute(tool_call, session_id=None, platform_context=None):
            return {"result": "工具执行成功", "success": True}

        loop._tool_executor.execute = fake_execute

        async def run_test():
            events = []
            async for event in loop.run_stream("执行任务", memory=self.memory):
                events.append(event)
                if event.get("type") in ("done", "error"):
                    break
            return events

        events = asyncio.run(run_test())
        event_types = [e.get("type") for e in events]

        self.assertIn("tool_start", event_types)
        self.assertIn("tool_result", event_types)
        self.assertIn("done", event_types)

    def test_has_long_term_memory_property(self):
        """has_long_term_memory 属性应正确反映三层记忆状态"""
        loop1 = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            three_layer_memory=None,
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )
        self.assertFalse(loop1.has_long_term_memory)

        loop2 = AgentLoop(
            llm_engine=self.llm,
            shell=self.shell,
            memory=self.memory,
            three_layer_memory=Mock(),
            enable_heuristics=False,
            enable_learning=False,
            flash_mode=True,
        )
        self.assertTrue(loop2.has_long_term_memory)


if __name__ == "__main__":
    unittest.main()
