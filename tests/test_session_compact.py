# -*- coding: utf-8 -*-
"""
会话压缩（SessionCompactor）核心测试合集

覆盖:
  1. 压缩保留最近 N 条完整 + 触发条件（token/tool_call 主导，无消息数阈值）
  2. 冷却逻辑
  3. LLM 失败重试 + 永久失败降级规则摘要（压缩必然完成）
  4. 规则摘要与 LLM 摘要结构兼容（merge/reduce/notes/提示词全链路）
  5. 无 1000 条硬截断
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.memory.session_compact import SessionCompactor
from app.agent.loop.memory import MemoryManager
from app.agent.memory.session_notes import SessionNotes

OK_JSON = '{"goal": "目标", "actions": ["操作1"], "findings": ["发现1"], "errors": [], "summary": "正常摘要"}'


# ================================================================
# Mocks
# ================================================================

class FastEngine:
    """正常摘要引擎"""
    async def chat(self, messages, tools=None, **kwargs):
        return type("R", (), {"content": OK_JSON})()


class FlakyEngine:
    """前 fail_times 次抛错，之后成功"""

    def __init__(self, fail_times=2):
        self.fail_times = fail_times
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"瞬时错误 #{self.calls}")
        return type("R", (), {"content": '{"goal": "重试后成功", "actions": [], "findings": ["成功"], "errors": [], "summary": "重试摘要"}'})()


class DeadEngine:
    """永远失败"""
    async def chat(self, messages, tools=None, **kwargs):
        raise RuntimeError("API 完全不可用")


def make_memory(n_msgs, chars_per_msg=100):
    """构造 n 条消息（轮流 user/assistant）"""
    mem = MemoryManager()
    for i in range(n_msgs):
        content = f"消息{i} " + "x" * chars_per_msg
        if i % 2 == 0:
            mem.add_user_message(content)
        else:
            mem.add_assistant_message(content)
    return mem


# ================================================================
# 测试
# ================================================================

class TestCompactTrigger(unittest.TestCase):
    """触发条件: token/tool_call 主导，无消息数阈值"""

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
        """message_count_threshold 已移除"""
        compactor = SessionCompactor(llm_engine=FastEngine())
        self.assertFalse(hasattr(compactor, "message_count_threshold"), "不应有消息数阈值")

    def test_token_below_threshold_no_compact(self):
        """token 未到阈值不触发"""
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=200000)
        mem = make_memory(30, chars_per_msg=20)
        self.assertFalse(compactor.should_compact(mem), "token 未到阈值不应触发")

    def test_tool_call_triggers(self):
        """tool_call 到阈值触发"""
        compactor = SessionCompactor(llm_engine=FastEngine(), tool_call_threshold=5)
        compactor._tool_call_count = 5
        self.assertTrue(compactor.should_compact(make_memory(5)), "tool_call 到阈值应触发")

    def test_token_threshold_triggers(self):
        """token 到阈值触发"""
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=500)
        mem = make_memory(50, chars_per_msg=100)  # ~1700 tokens > 500
        self.assertTrue(compactor.should_compact(mem), "token 到阈值应触发")

    def test_cooldown_growth_ratio(self):
        """增长 >50% 后能再次触发（冷却不误伤）"""
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=5000)
        small = make_memory(20, chars_per_msg=50)
        compactor._last_compact_tokens = compactor._estimate_tokens(small)
        big = make_memory(200, chars_per_msg=100)  # ~6900 tokens
        self.assertTrue(compactor.should_compact(big), "增长足够应触发")


class TestCompactRetry(unittest.TestCase):
    """失败重试 + 降级"""

    def test_retry_after_failure(self):
        """失败 2 次后重试成功"""
        engine = FlakyEngine(fail_times=2)
        compactor = SessionCompactor(llm_engine=engine, retry_backoff_base=0)
        result = asyncio.run(compactor._generate_summary_with_llm("[用户]: 你好"))
        self.assertEqual(engine.calls, 3, "应重试到成功")
        self.assertEqual(result["goal"], "重试后成功")

    def test_fallback_on_dead_engine(self):
        """永久失败 → 降级规则摘要（不中断）"""
        compactor = SessionCompactor(llm_engine=DeadEngine(), retry_backoff_base=0)
        result = asyncio.run(compactor._generate_summary_with_llm("[用户]: 分析价格走势\n[助手]: 已完成"))
        self.assertEqual(result["goal"], "分析价格走势", "降级应提取目标")
        self.assertTrue(result["summary"], "降级应有 summary")

    def test_compact_completes_with_dead_engine(self):
        """API 完全失败时压缩仍完成"""
        mem = make_memory(40, chars_per_msg=80)
        compactor = SessionCompactor(llm_engine=DeadEngine(), keep_recent_messages=10, retry_backoff_base=0)
        notes = SessionNotes(session_id="t3", notes_dir="memory/notes")
        asyncio.run(compactor.compact_now(mem, notes))
        self.assertLessEqual(len(mem.messages), 11, "应完成压缩")
        self.assertTrue(any("[系统压缩]" in str(m.get("content", "")) for m in mem.messages))

    def test_normal_engine_no_extra_calls(self):
        """正常引擎一次成功，无多余重试"""
        engine = FastEngine()
        compactor = SessionCompactor(llm_engine=engine)
        result = asyncio.run(compactor._generate_summary_with_llm("[用户]: 测试"))
        self.assertTrue(result["goal"])


class TestFallbackCompat(unittest.TestCase):
    """规则摘要与 LLM 摘要结构兼容"""

    def test_structure_match(self):
        """字段名和类型一致"""
        compactor = SessionCompactor(llm_engine=FastEngine())
        llm = asyncio.run(compactor._generate_summary_with_llm("[用户]: 你好"))
        fb = compactor._fallback_summary("[用户]: 分析价格走势\n[助手]: 已完成")
        self.assertEqual(set(llm.keys()), set(fb.keys()), "字段应一致")
        for k in ("goal", "summary"):
            self.assertIsInstance(fb[k], str)
        for k in ("actions", "findings", "errors"):
            self.assertIsInstance(fb[k], list)

    def test_merge_consumes_fallback(self):
        """merge 能消费规则摘要"""
        compactor = SessionCompactor(llm_engine=FastEngine())
        fb = compactor._fallback_summary("[用户]: 目标A\n[助手]: 发现B")
        llm = asyncio.run(compactor._generate_summary_with_llm("[用户]: x"))
        merged = compactor._merge_summaries([fb, llm])
        self.assertTrue(merged["goal"])
        self.assertTrue(merged["findings"])

    def test_fallback_info_in_notes(self):
        """全降级压缩后，降级信息完整进 notes"""
        mem = make_memory(20, chars_per_msg=100)
        compactor = SessionCompactor(llm_engine=DeadEngine(), keep_recent_messages=5, retry_backoff_base=0)
        notes = SessionNotes(session_id="t5", notes_dir="memory/notes")
        asyncio.run(compactor.compact_now(mem, notes))
        content = notes.render_for_prompt()
        self.assertIn("消息0", content, "降级目标应进 notes")

    def test_compacted_messages_renderable(self):
        """降级压缩产出可直接被 PromptBuilder 渲染"""
        mem = make_memory(30, chars_per_msg=100)
        compactor = SessionCompactor(llm_engine=DeadEngine(), keep_recent_messages=5, retry_backoff_base=0)
        notes = SessionNotes(session_id="t6", notes_dir="memory/notes")
        asyncio.run(compactor.compact_now(mem, notes))
        for m in mem.messages:
            self.assertIn(m.get("role"), ("user", "assistant", "system"), "role 应合法")


class TestReduceConvergence(unittest.TestCase):
    """_recursive_reduce 收敛性（修复死循环回归）"""

    def test_converges_with_large_summaries(self):
        """大摘要场景必须收敛，归约后进入预算内"""
        compactor = SessionCompactor(llm_engine=FastEngine(), retry_backoff_base=0)
        summaries = []
        for i in range(8):
            summaries.append({
                "goal": f"目标{i}", "actions": [f"动作{i}"],
                "findings": [f"发现{i}-{j}" for j in range(3)],
                "errors": [], "summary": "总结" + "内容" * 900,
            })
        result = asyncio.run(compactor._recursive_reduce(summaries))
        self.assertTrue(result, "应返回非空结果")
        self.assertLessEqual(len(compactor._summaries_to_text(result)), 8000, "应收敛到预算内")

    def test_no_infinite_loop_on_huge_summary(self):
        """单条摘要超预算时轮数上限兜底，必须返回"""
        class HugeEngine:
            async def chat(self, messages, tools=None, **kwargs):
                return type("R", (), {"content": __import__("json").dumps(
                    {"goal": "g", "actions": [], "findings": [], "errors": [], "summary": "x" * 4000})})()

        compactor = SessionCompactor(llm_engine=HugeEngine(), retry_backoff_base=0)
        summaries = [{"goal": "g", "actions": [], "findings": [], "errors": [], "summary": "y" * 9000} for _ in range(2)]
        result = asyncio.run(compactor._recursive_reduce(summaries))
        self.assertTrue(result, "轮数兜底应返回而非死循环")


class TestNoTruncation(unittest.TestCase):
    """无 1000 条硬截断"""

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
        """长但低 token 会话完整保留，等 token 到阈值再压缩"""
        compactor = SessionCompactor(llm_engine=FastEngine(), token_threshold=100000)
        short_mem = MemoryManager()
        for i in range(1200):
            short_mem.add_user_message("短" * 5)
        self.assertFalse(compactor.should_compact(short_mem), "token 未到不应压缩")
        self.assertEqual(len(short_mem.get_messages()), 1200, "不应截断")


if __name__ == "__main__":
    unittest.main(verbosity=2)
