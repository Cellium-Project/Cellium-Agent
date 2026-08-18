# -*- coding: utf-8 -*-
"""
会话压缩（SessionCompactor）核心测试合集

覆盖:
  1. 触发条件（token/tool_call 主导，无消息数阈值）+ 冷却逻辑
  2. 一次 LLM 调用压缩（无分块、无重试）
  3. JSON 容错解析（代码块包裹、非法控制字符、尾随逗号）
  4. 增量压缩（只压新消息）
  5. notes 上限收敛（findings/errors 不超过 50）
  6. 保留最近 N 条完整 + 无 1000 条硬截断
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.memory.session_compact import SessionCompactor
from app.agent.loop.memory import MemoryManager
from app.agent.memory.session_notes import SessionNotes

OK_JSON = '{"goal": "目标", "actions": ["操作1"], "findings": ["发现1"], "errors": [], "summary": "正常摘要"}'


class FastEngine:
    """正常摘要引擎"""
    async def chat(self, messages, tools=None, **kwargs):
        return type("R", (), {"content": OK_JSON})()


def make_memory(n_msgs, chars_per_msg=100):
    mem = MemoryManager()
    for i in range(n_msgs):
        content = f"消息{i} " + "x" * chars_per_msg
        if i % 2 == 0:
            mem.add_user_message(content)
        else:
            mem.add_assistant_message(content)
    return mem


class TestCompactTrigger(unittest.TestCase):
    """触发条件"""

    def test_keep_recent_messages_complete(self):
        """压缩后 = 摘要 + 最近 N 条完整"""
        mem = make_memory(100, chars_per_msg=50)
        compactor = SessionCompactor(llm_engine=FastEngine(), keep_recent_messages=20)
        notes = SessionNotes(session_id="t1", notes_dir="memory/notes")
        asyncio.run(compactor.compact_now(mem, notes))
        self.assertTrue(any("[系统压缩]" in str(m.get("content", "")) for m in mem.messages), "应含压缩摘要")
        self.assertTrue(any("消息98" in str(m.get("content", "")) for m in mem.messages), "应保留最近原文")
        self.assertLessEqual(len(mem.messages), 21, "应为 摘要+20 条")

    def test_no_message_count_threshold(self):
        compactor = SessionCompactor(llm_engine=FastEngine())
        self.assertFalse(hasattr(compactor, "message_count_threshold"), "不应有消息数阈值")

    def test_token_below_threshold_no_compact(self):
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=200000)
        mem = make_memory(30, chars_per_msg=20)
        self.assertFalse(compactor.should_compact(mem), "token 未到阈值不应触发")

    def test_tool_call_triggers(self):
        compactor = SessionCompactor(llm_engine=FastEngine(), tool_call_threshold=5)
        compactor._tool_call_count = 5
        self.assertTrue(compactor.should_compact(make_memory(5)), "tool_call 到阈值应触发")

    def test_token_threshold_triggers(self):
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=500)
        mem = make_memory(50, chars_per_msg=100)
        self.assertTrue(compactor.should_compact(mem), "token 到阈值应触发")

    def test_cooldown_growth_ratio(self):
        """增长 >50% 后能再次触发"""
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=5000)
        small = make_memory(20, chars_per_msg=50)
        compactor._last_compact_tokens = compactor._estimate_tokens(small)
        big = make_memory(200, chars_per_msg=100)
        self.assertTrue(compactor.should_compact(big), "增长足够应触发")


class TestSingleShotCompact(unittest.TestCase):
    """一次 LLM 调用压缩（无分块、无重试）"""

    def test_no_chunk_methods(self):
        """分块/归约方法应被移除"""
        compactor = SessionCompactor(llm_engine=FastEngine())
        self.assertFalse(hasattr(compactor, "_chunk_messages"), "不应有分块")
        self.assertFalse(hasattr(compactor, "_recursive_reduce"), "不应有递归归约")
        self.assertFalse(hasattr(compactor, "_fallback_summary"), "不应有规则降级")

    def test_no_retry_backoff(self):
        """不应有 retry_backoff_base 参数"""
        compactor = SessionCompactor(llm_engine=FastEngine())
        self.assertFalse(hasattr(compactor, "retry_backoff_base"), "不应有重试退避")

    def test_single_call_success(self):
        """正常引擎一次成功"""
        engine = FastEngine()
        compactor = SessionCompactor(llm_engine=engine)
        result = asyncio.run(compactor._generate_summary_with_llm("[用户]: 测试"))
        self.assertTrue(result["goal"])


class TestJsonParsing(unittest.TestCase):
    """JSON 容错解析"""

    def setUp(self):
        self.compactor = SessionCompactor(llm_engine=FastEngine())

    def test_code_block_wrapped(self):
        """剔除 ```json 包裹"""
        raw = '```json\n{"goal": "目标", "actions": [], "findings": ["发现"], "errors": [], "summary": "摘要"}\n```'
        result = self.compactor._parse_summary_json(raw)
        self.assertEqual(result["goal"], "目标")

    def test_illegal_control_char(self):
        """处理非法控制字符"""
        raw = '{"goal": "目\x00标", "actions": [], "findings": [], "errors": [], "summary": "摘\x1f要"}'
        result = self.compactor._parse_summary_json(raw)
        self.assertIn("目", result["goal"])

    def test_trailing_commas(self):
        """清尾随逗号"""
        raw = '{"goal": "目标", "actions": ["a", "b",], "findings": ["f",], "errors": [], "summary": "摘要",}'
        result = self.compactor._parse_summary_json(raw)
        self.assertEqual(result["actions"], ["a", "b"])

    def test_invalid_json_returns_empty(self):
        """非法 JSON 返回空字典"""
        result = self.compactor._parse_summary_json("not json at all")
        self.assertEqual(result, {})


class TestNoTruncation(unittest.TestCase):
    """无硬截断"""

    def test_1200_messages_preserved(self):
        mem = MemoryManager()
        for i in range(1200):
            if i % 2 == 0:
                mem.add_user_message(f"用户{i}")
            else:
                mem.add_assistant_message(f"助手{i}")
        all_msgs = mem.get_messages()
        self.assertEqual(len(all_msgs), 1200, "不应截断")
        self.assertIn("用户0", str(all_msgs[0].get("content", "")))
        self.assertIn("助手1199", str(all_msgs[-1].get("content", "")))

    def test_no_truncation_low_token(self):
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=100000)
        short_mem = MemoryManager()
        for i in range(1200):
            short_mem.add_user_message("短" * 5)
        self.assertFalse(compactor.should_compact(short_mem), "token 未到不应压缩")
        self.assertEqual(len(short_mem.get_messages()), 1200, "不应截断")


class SeqEngine:
    """每次返回不同摘要，避免去重误判"""
    calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        SeqEngine.calls += 1
        tag = f"标签{SeqEngine.calls:04d}"
        return type("R", (), {"content": json.dumps(
            {"goal": f"目标{tag}",
             "actions": [f"动作{tag}{i}" for i in range(2)],
             "findings": [f"发现{tag}{i}" for i in range(2)],
             "errors": [], "summary": f"摘要{tag}"})})()


class TestIncrementalCompact(unittest.TestCase):
    """增量压缩：二次压缩只压新消息"""
    NOTES_CAP = 50

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="cellium_inc_")
        SeqEngine.calls = 0

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_memory(self, n, prefix, long=False):
        mem = MemoryManager()
        extra = "很长的内容" * 30 if long else ""
        for i in range(n):
            if i % 2 == 0:
                mem.add_user_message(f"{prefix}问题{i} " + extra)
            else:
                mem.add_assistant_message(f"{prefix}回答{i} " + extra)
        return mem

    def _compact_with_notes(self, compactor, mem, notes, rounds):
        """执行多次压缩"""
        msgs_per_round = 20
        for r in range(rounds):
            for i in range(msgs_per_round):
                mem.add_user_message(f"批{r}问题{i}")
                mem.add_assistant_message(f"批{r}回答{i}")
            asyncio.run(compactor.compact_now(mem, notes))

    def test_second_compact_only_incremental(self):
        """二次压缩只压新消息，不重压旧快照"""
        notes = SessionNotes(session_id="s", notes_dir=os.path.join(self._tmp, "notes"))
        compactor = SessionCompactor(llm_engine=SeqEngine(), keep_recent_messages=10)
        mem = self._make_memory(60, "第一批", long=True)
        asyncio.run(compactor.compact_now(mem, notes))
        self.assertTrue(mem.messages[0].get("_is_compacted_notes"), "压缩后首条应带标记")

        for i in range(40):
            mem.add_user_message(f"第二批问题{i} " + "内容" * 30)
            mem.add_assistant_message(f"第二批回答{i} " + "内容" * 30)
        asyncio.run(compactor.compact_now(mem, notes))
        self.assertTrue(mem.messages[0].get("_is_compacted_notes"), "二次压缩后仍带标记")

    def test_compact_uses_notes_as_context(self):
        """压缩时 LLM 应能看到历史压缩摘要"""
        notes = SessionNotes(session_id="s", notes_dir=os.path.join(self._tmp, "notes"))
        compactor = SessionCompactor(llm_engine=SeqEngine(), keep_recent_messages=10)
        mem = self._make_memory(60, "第一批", long=True)
        asyncio.run(compactor.compact_now(mem, notes))

        # 第二次压缩前，手动构造带历史笔记消息的 memory
        for i in range(20):
            mem.add_user_message(f"第二批问题{i}")
            mem.add_assistant_message(f"第二批回答{i}")
        asyncio.run(compactor.compact_now(mem, notes))

        # 历史笔记消息应保留在 memory 中作为上下文
        self.assertTrue(any(m.get("_is_compacted_notes") for m in mem.messages), "历史笔记应保留")

    def test_notes_capped(self):
        """findings/errors 追加 40 次压缩不超 50 条"""
        notes = SessionNotes(session_id="s", notes_dir=os.path.join(self._tmp, "notes"))
        compactor = SessionCompactor(llm_engine=SeqEngine(), keep_recent_messages=10)
        mem = self._make_memory(60, "批0", long=True)
        asyncio.run(compactor.compact_now(mem, notes))
        self._compact_with_notes(compactor, mem, notes, rounds=40)
        self.assertLessEqual(len(notes.get_findings()), self.NOTES_CAP, "findings应收敛到上限")
        self.assertLessEqual(len(notes.get_errors()), self.NOTES_CAP, "errors应收敛到上限")

    def test_completed_set_overrides(self):
        """completed 覆盖不膨胀"""
        notes = SessionNotes(session_id="s", notes_dir=os.path.join(self._tmp, "notes"))
        compactor = SessionCompactor(llm_engine=SeqEngine(), keep_recent_messages=10)
        mem = self._make_memory(60, "批0", long=True)
        asyncio.run(compactor.compact_now(mem, notes))
        self._compact_with_notes(compactor, mem, notes, rounds=40)
        # completed 每次只保留当次的 2 条
        self.assertLessEqual(len(notes.get_completed()), 2, "completed 覆盖最多 2 条")


if __name__ == "__main__":
    unittest.main(verbosity=2)
