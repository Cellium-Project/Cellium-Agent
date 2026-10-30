# -*- coding: utf-8 -*-
"""收尾 drain 与 Gene 机制的集成验证

覆盖：
  1. 最终回复后到达的补充消息被消费（不被丢弃）
  2. drain 消费的补充消息不污染 Gene 评估上下文
  3. 复杂度 Gene 评估触发不被 drain 吞掉
"""

import asyncio
import sys
import unittest

sys.path.insert(0, ".")

from app.agent.loop.agent_loop import AgentLoop
from app.agent.loop.memory import MemoryManager
from app.agent.llm.models import ChatResponse
from app.server.task_manager import get_task_manager


class _FakeLLM:
    """按脚本产出响应；可在指定轮次注入补充消息"""

    def __init__(self, script):
        self.script = script
        self.calls = 0

    async def chat_stream(self, messages, tools=None, **kwargs):
        idx = min(self.calls, len(self.script) - 1)
        step = self.script[idx]
        self.calls += 1
        for chunk in step.get("chunks", []):
            yield chunk
            if step.get("inject_on_chunk"):
                step["inject_on_chunk"]()
        resp = ChatResponse(
            content=step.get("content", ""),
            tool_calls=step.get("tool_calls", []),
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
        yield {"type": "done", "response": resp}

    async def chat(self, messages, tools=None, **kwargs):
        return ChatResponse(content="", finish_reason="stop")


def _text_chunk(text):
    return {"type": "content", "text": text}


class _ToolCall:
    def __init__(self, name, arguments, call_id="call_probe_1"):
        self.id = call_id
        self.name = name
        self.arguments = arguments


class _GeneProbe:
    """拦截 AgentLoop 的 Gene 评估触发，记录标志位流转"""

    def __init__(self):
        self.seen = []

    def reset_score(self):
        pass

    def build_agent_gene_prompt(self, user_input, tool_traces, loop_state, total_time_ms, final_content):
        self.seen.append(dict(loop_state.__dict__))
        return "GENE_PROMPT_FOR_TEST"


class TestPreFinalDrainIntegration(unittest.TestCase):

    def setUp(self):
        self.tm = get_task_manager()
        self.session = "itest_prefinal"
        self.tm._supplement_messages.pop(self.session, None)

    def _run(self, script, max_iterations=10, session=None):
        sess = session or self.session
        llm = _FakeLLM(script)
        loop = AgentLoop(
            llm_engine=llm,
            memory=MemoryManager(),
            max_iterations=max_iterations,
            session_id=sess,
            flash_mode=True,
            enable_learning=False,
            enable_hybrid=False,
        )
        events = []

        async def _drive():
            async for evt in loop.run_stream("原始请求", session_id=sess):
                events.append(evt)

        asyncio.run(_drive())
        return events, loop

    def test_supplement_arriving_during_final_reply_is_consumed(self):
        """最终回复流式产出期间到达的补充消息，必须被消费"""
        def _inject():
            self.tm._info.setdefault(self.session, type("I", (), {
                "status": type("S", (), {"value": "running"})()})())
            self.tm._supplement_messages.setdefault(self.session, []).append({
                "content": "补充：再检查一下日志",
                "source": "test",
                "received_at": 0,
            })

        script = [{
            "content": "第一轮回复",
            "chunks": [_text_chunk("第一轮回复")],
            "inject_on_chunk": _inject,
        }, {
            "content": "第二轮回复（已吸收补充）",
            "chunks": [_text_chunk("第二轮回复（已吸收补充）")],
        }]

        events, loop = self._run(script)

        injected = [e for e in events if e.get("type") == "supplement_injected"]
        self.assertTrue(injected, "补充消息应被注入")
        self.assertEqual(injected[0]["source"], "pre_final_drain")
        self.assertIn("补充：再检查一下日志", injected[0]["content"])
        # 队列已被消费干净
        self.assertEqual(self.tm.drain_supplement_messages(self.session), [])

    def test_supplement_reaches_llm_context(self):
        """补充消息必须进入 memory 上下文，LLM 才能看到"""
        seen_messages = []

        def _inject():
            self.tm._supplement_messages.setdefault(self.session, []).append({
                "content": "UNIQUE_MARKER_XYZ",
                "source": "test",
                "received_at": 0,
            })

        class _SpyLLM(_FakeLLM):
            async def chat_stream(self, messages, tools=None, **kwargs):
                seen_messages.append(list(messages))
                async for c in super().chat_stream(messages, tools=tools, **kwargs):
                    yield c

        llm = _SpyLLM([{
            "content": "回复1",
            "chunks": [_text_chunk("回复1")],
            "inject_on_chunk": _inject,
        }, {
            "content": "回复2",
            "chunks": [_text_chunk("回复2")],
        }])

        loop = AgentLoop(
            llm_engine=llm,
            memory=MemoryManager(),
            max_iterations=10,
            session_id=self.session,
            flash_mode=True,
            enable_learning=False,
            enable_hybrid=False,
        )

        async def _drive():
            async for _ in loop.run_stream("原始请求", session_id=self.session):
                pass

        asyncio.run(_drive())

        self.assertGreaterEqual(len(seen_messages), 2, "应至少有第二轮 LLM 调用")
        second_call_text = "".join(
            str(m.get("content", "")) for m in seen_messages[1]
        )
        self.assertIn("UNIQUE_MARKER_XYZ", second_call_text,
                      "补充消息必须进入第二轮 LLM 上下文")

    def test_gene_trigger_not_swallowed_by_drain(self):
        """drain 消费补充消息后，复杂度 Gene 评估仍应触发（不丢）"""
        probe = _GeneProbe()

        def _inject():
            self.tm._supplement_messages.setdefault(self.session, []).append({
                "content": "补充内容",
                "source": "test",
                "received_at": 0,
            })

        # 第1轮：工具调用轮（复杂度检测只在这里发生）
        # 第2轮：最终回复 + drain 消费补充消息
        # 第3轮：Gene 评估轮
        script = [{
            "content": "调用工具",
            "chunks": [_text_chunk("调用工具")],
            "tool_calls": [_ToolCall("nonexistent_tool_probe", {})],
        }, {
            "content": "最终回复",
            "chunks": [_text_chunk("最终回复")],
            "inject_on_chunk": _inject,
        }, {
            "content": "Gene 评估回复",
            "chunks": [_text_chunk("Gene 评估回复")],
        }]

        llm = _FakeLLM(script)
        loop = AgentLoop(
            llm_engine=llm,
            memory=MemoryManager(),
            max_iterations=10,
            session_id=self.session,
            flash_mode=True,
            enable_learning=False,
            enable_hybrid=False,
        )
        loop._gene_post_session_analyzer = probe

        events = []

        async def _drive():
            async for evt in loop.run_stream("原始请求", session_id=self.session):
                events.append(evt)

        asyncio.run(_drive())

        injected = [e for e in events if e.get("type") == "supplement_injected"]
        self.assertTrue(injected, "补充消息应被 drain 消费")

        gene_status = [e for e in events if e.get("type") == "status"
                       and "Gene" in str(e.get("content", ""))]
        self.assertTrue(gene_status, "Gene 评估轮应被触发，drain 不得吞掉它")
        self.assertTrue(probe.seen, "复杂度检测应至少执行一次")

    def test_drain_does_not_reset_complexity_source(self):
        """drain 复位只在 gene_processing_done=True 时发生，不得抹掉 complexity 来源"""
        probe = _GeneProbe()

        def _inject():
            self.tm._supplement_messages.setdefault(self.session, []).append({
                "content": "补充内容",
                "source": "test",
                "received_at": 0,
            })

        script = [{
            "content": "调用工具",
            "chunks": [_text_chunk("调用工具")],
            "tool_calls": [_ToolCall("nonexistent_tool_probe", {})],
        }, {
            "content": "最终回复",
            "chunks": [_text_chunk("最终回复")],
            "inject_on_chunk": _inject,
        }, {
            "content": "Gene 评估回复",
            "chunks": [_text_chunk("Gene 评估回复")],
        }]

        llm = _FakeLLM(script)
        loop = AgentLoop(
            llm_engine=llm,
            memory=MemoryManager(),
            max_iterations=10,
            session_id=self.session,
            flash_mode=True,
            enable_learning=False,
            enable_hybrid=False,
        )
        loop._gene_post_session_analyzer = probe

        # 在 drain 调用时刻抓 loop_state 快照（此时复位尚未发生）
        observed = []
        real_drain = self.tm.drain_supplement_messages

        def _spy_drain(session_id):
            st = loop._loop_state
            msgs = real_drain(session_id)
            observed.append({
                "source": getattr(st, "gene_creation_source", ""),
                "done": getattr(st, "gene_processing_done", False),
                "prompt": getattr(st, "gene_creation_prompt", None),
                "count": len(msgs),
            })
            return msgs

        self.tm.drain_supplement_messages = _spy_drain
        try:
            async def _drive():
                async for _ in loop.run_stream("原始请求", session_id=self.session):
                    pass

            asyncio.run(_drive())
        finally:
            self.tm.drain_supplement_messages = real_drain

        self.assertTrue(observed, "drain 应被调用")
        # 取到消息的那次 = 收尾 drain（补充消息在此被消费）
        consuming = [o for o in observed if o["count"] > 0]
        self.assertEqual(len(consuming), 1, "补充消息应恰好被消费一次")
        snap = consuming[0]
        # 收尾 drain 时刻 gene_processing_done 仍为 False → 复位条件不成立
        # → complexity 来源与 prompt 不得被抹掉
        self.assertFalse(snap["done"])
        self.assertEqual(snap["source"], "complexity")
        self.assertTrue(snap["prompt"], "Gene prompt 不得被 drain 抹掉")

    def test_limit_round_leaves_message_in_queue(self):
        """达到最大迭代时不清队列，消息留给下一次任务"""
        def _inject():
            self.tm._supplement_messages.setdefault(self.session, []).append({
                "content": "上限轮补充",
                "source": "test",
                "received_at": 0,
            })

        script = [{
            "content": "回复1",
            "chunks": [_text_chunk("回复1")],
            "inject_on_chunk": _inject,
        }]

        events, loop = self._run(script, max_iterations=1)

        injected = [e for e in events if e.get("type") == "supplement_injected"]
        self.assertFalse(injected, "上限轮不应注入补充消息")
        left = self.tm.drain_supplement_messages(self.session)
        self.assertEqual(len(left), 1, "上限轮消息必须留在队列，不能被丢弃")
        self.assertEqual(left[0]["content"], "上限轮补充")


if __name__ == "__main__":
    unittest.main()
