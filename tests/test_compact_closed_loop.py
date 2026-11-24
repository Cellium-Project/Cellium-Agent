# -*- coding: utf-8 -*-
"""
压缩闭环回归测试

覆盖整条链路：
  1. 触发判定（阈值 / 超窗口）
  2. 切点计算（token 预算、工具组完整性、极端场景不切空）
  3. 巨型消息截断（assistant / user / tool / reasoning，tool_calls 保留）
  4. 用户当前任务保留（被切时补回、不重复）
  5. 摘要/笔记解析（<summary> + <notes>、截断容错、旧 JSON 兼容）
  6. 归档恢复闭环（快照起点 / 预算裁剪 / 惰性加载与全量一致）
  7. memory.compact 工具（runtime_memory 同一实例、参数校验）
  8. token 估算缓存一致性与 effective_context_window 校准
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.llm.engine import (
    OpenAICompatibleEngine,
    _MSG_TOKEN_MEMO,
    _estimate_messages_tokens,
)
from app.agent.loop.memory import MemoryManager
from app.agent.loop.session_manager import SessionManager
from app.agent.memory.session_compact import SessionCompactor
from app.agent.memory.session_notes import SessionNotes
from app.agent.tools.memory_tool import MemoryTool


# ---------------------------------------------------------------- helpers

def tool_turn(i, chars=3000):
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c%d" % i,
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": json.dumps({"output": "y" * chars}), "tool_call_id": "c%d" % i},
    ]


def make_compactor(**kw):
    kw.setdefault("keep_recent_tokens", 15000)
    return SessionCompactor(llm_engine=None, **kw)


class FakeArchive:
    """内存归档，模拟 SqliteArchiveStore 的恢复接口"""

    def __init__(self, records):
        self.records = records

    def find_last_snapshot_seq(self, session_id):
        for rec in reversed(self.records):
            msgs = rec.get("messages") or []
            if any(isinstance(m, dict) and m.get("_is_compacted_notes") for m in msgs):
                return rec.get("seq")
        return None

    def get_by_session(self, session_id, limit=100000, min_seq=None):
        rs = [r for r in self.records if min_seq is None or r.get("seq", 0) >= min_seq]
        return rs[-limit:]

    def get_tail_records(self, session_id, limit=100, before_seq=None):
        rs = [r for r in self.records if before_seq is None or r.get("seq", 0) < before_seq]
        return rs[-limit:]


# ---------------------------------------------------------------- 1. 触发判定

class TestTrigger(unittest.TestCase):

    def test_threshold_uses_effective_window(self):
        llm = SimpleNamespace(context_window=1000000, effective_context_window=1000000)
        c = SessionCompactor(llm_engine=llm, compact_ratio=0.8)
        self.assertEqual(c.compact_threshold(), 800000)
        self.assertFalse(c.should_compact_tokens(799999))
        self.assertTrue(c.should_compact_tokens(800000))
        self.assertFalse(c.is_over_window_tokens(1000000))
        self.assertTrue(c.is_over_window_tokens(1000001))

    def test_cooldown_blocks_trigger(self):
        llm = SimpleNamespace(context_window=100000, effective_context_window=100000)
        c = SessionCompactor(llm_engine=llm, compact_ratio=0.8)
        c._compact_cooldown_until = 10 ** 12
        self.assertFalse(c.should_compact_tokens(999999))


# ---------------------------------------------------------------- 2. 切点计算

class TestTailCutIndex(unittest.TestCase):

    def test_respects_token_budget(self):
        c = make_compactor()
        msgs = []
        for i in range(60):
            msgs += tool_turn(i)
        cut = c._tail_cut_index(msgs, 15000)
        kept = msgs[cut:]
        self.assertGreater(cut, 0)
        self.assertLess(len(kept), len(msgs))
        self.assertLessEqual(_estimate_messages_tokens(kept), 15000 * 1.5)

    def test_kept_segment_never_starts_with_orphan_tool(self):
        c = make_compactor()
        msgs = [{"role": "user", "content": "任务"}]
        for i in range(60):
            msgs += tool_turn(i)
        cut = c._tail_cut_index(msgs, 15000)
        kept = msgs[cut:]
        if kept:
            self.assertNotEqual(kept[0]["role"], "tool", "保留段不能以孤立 tool 开头")
            if kept[0]["role"] == "assistant":
                self.assertTrue(kept[0].get("tool_calls"), "assistant 应带 tool_calls")

    def test_all_tool_messages_not_cut_to_empty(self):
        c = make_compactor()
        msgs = [{"role": "user", "content": "任务"}] + [
            {"role": "tool", "content": "z" * 4000, "tool_call_id": "c%d" % i}
            for i in range(60)
        ]
        cut = c._tail_cut_index(msgs, 15000)
        self.assertGreaterEqual(cut, 0)
        self.assertLessEqual(cut, len(msgs))
        self.assertGreater(len(msgs[cut:]), 0, "不能把消息全部切空")

    def test_single_message(self):
        c = make_compactor()
        self.assertEqual(c._tail_cut_index([{"role": "user", "content": "hi"}], 15000), 0)
        self.assertEqual(c._tail_cut_index([], 15000), 0)


# ---------------------------------------------------------------- 3. 巨型截断

class TestGiantTruncation(unittest.TestCase):

    def _tokens(self, msg):
        return _estimate_messages_tokens([msg])

    def test_assistant_content_and_reasoning(self):
        msg = {
            "role": "assistant",
            "content": "y" * 900000,
            "reasoning_content": "z" * 200000,
        }
        before = self._tokens(msg)
        out = SessionCompactor._truncate_giant_message(msg)
        after = self._tokens(out)
        self.assertGreater(before, 100000)
        self.assertLess(after, before / 10)

    def test_user_and_tool(self):
        for role in ("user", "tool"):
            msg = {"role": role, "content": "x" * 200000}
            before = self._tokens(msg)
            out = SessionCompactor._truncate_giant_message(msg)
            self.assertLess(self._tokens(out), before / 10, role)

    def test_tool_calls_preserved(self):
        tc = [{"id": "c1", "type": "function", "function": {"name": "shell", "arguments": "{}"}}]
        msg = {
            "role": "assistant",
            "content": None,
            "reasoning_content": "z" * 300000,
            "tool_calls": tc,
        }
        out = SessionCompactor._truncate_giant_message(msg)
        self.assertEqual(out.get("tool_calls"), tc)
        self.assertLess(len(out.get("reasoning_content") or ""), 300000)

    def test_short_message_untouched(self):
        msg = {"role": "assistant", "content": "ok"}
        self.assertIs(SessionCompactor._truncate_giant_message(msg), msg)

    def test_compacted_notes_not_truncated(self):
        msg = {"role": "user", "content": "x" * 900000, "_is_compacted_notes": True}
        self.assertIs(SessionCompactor._truncate_giant_message(msg), msg)


# ---------------------------------------------------------------- 4. 用户任务保留

class TestCurrentTaskPreserved(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cellium_loop_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_task_kept_when_cut(self):
        mem = MemoryManager()
        mem.messages = [{"role": "user", "content": "任务A：打包 whl，版本 1.4.5.post15"}]
        for i in range(40):
            mem.messages += tool_turn(i)
        c = make_compactor()
        notes = SessionNotes("task-keep", notes_dir=self.tmp)
        cut = c._tail_cut_index(mem.messages, 15000)
        self.assertGreater(cut, 0, "样本应切到任务A之后")
        c._replace_old_messages(mem, notes, "摘要", cut=cut, keep_tokens=15000)

        self.assertTrue(mem.messages[0].get("_is_compacted_notes"))
        joined = [str(m.get("content") or "") for m in mem.messages]
        self.assertTrue(any("任务A" in s for s in joined), "当前用户任务必须保留")
        self.assertEqual(sum(1 for s in joined if "任务A" in s), 1, "不应重复插入")

    def test_task_not_duplicated_when_in_kept_segment(self):
        mem = MemoryManager()
        mem.messages = [{"role": "user", "content": "旧任务"}]
        for i in range(40):
            mem.messages += tool_turn(i)
        mem.messages.append({"role": "user", "content": "新任务：继续"})
        mem.messages.append({"role": "assistant", "content": "收到"})

        c = make_compactor()
        notes = SessionNotes("task-dup", notes_dir=self.tmp)
        cut = c._tail_cut_index(mem.messages, 15000)
        c._replace_old_messages(mem, notes, "摘要", cut=cut, keep_tokens=15000)

        contents = [str(m.get("content") or "") for m in mem.messages]
        self.assertEqual(sum(1 for s in contents if "新任务" in s), 1)


# ---------------------------------------------------------------- 5. 解析

class TestSummaryParsing(unittest.TestCase):

    def test_blocks(self):
        raw = (
            "<summary>这是摘要</summary>\n"
            '<notes>{"goal": "目标", "actions": ["a"], "findings": ["f"], '
            '"errors": [], "pending": ["p"]}</notes>'
        )
        p = SessionCompactor._parse_summary_block(raw)
        self.assertEqual(p["summary"], "这是摘要")
        self.assertEqual(p["goal"], "目标")
        self.assertEqual(p["actions"], ["a"])
        self.assertEqual(p["pending"], ["p"])

    def test_truncated_notes_field_recovery(self):
        raw = '<summary>摘要</summary>\n<notes>{"goal": "目标", "actions": ["a"], "findings": ["f"'
        p = SessionCompactor._parse_summary_block(raw)
        self.assertEqual(p["summary"], "摘要")
        self.assertEqual(p["goal"], "目标")
        self.assertEqual(p["actions"], ["a"])

    def test_summary_only(self):
        p = SessionCompactor._parse_summary_block("<summary>只有摘要</summary>")
        self.assertEqual(p["summary"], "只有摘要")
        self.assertEqual(p.get("goal"), None)

    def test_legacy_json_still_supported(self):
        c = make_compactor()
        raw = '{"goal": "g", "summary": "旧格式摘要"}'
        self.assertEqual(c._parse_summary_block(raw), {})
        self.assertEqual(c._parse_summary_json(raw)["summary"], "旧格式摘要")


# ---------------------------------------------------------------- 6. 归档恢复闭环

class TestRestoreClosedLoop(unittest.TestCase):

    def _manager(self, records):
        sm = SessionManager()
        sm.three_layer_memory = SimpleNamespace(archive=FakeArchive(records))
        return sm

    def _snapshot_record(self, seq, summary="压缩摘要"):
        return {
            "id": "snap-%d" % seq,
            "seq": seq,
            "messages": [
                {"role": "user", "content": "[系统压缩] " + summary,
                 "_is_compacted_notes": True},
                {"role": "assistant", "content": "继续"},
            ],
        }

    def _normal_record(self, seq, chars=4000):
        return {
            "id": "r-%d" % seq,
            "seq": seq,
            "messages": [
                {"role": "assistant", "content": "",
                 "tool_calls": [{"id": "c%d" % seq, "type": "function",
                                 "function": {"name": "shell", "arguments": "{}"}}]},
                {"role": "tool", "content": json.dumps({"output": "x" * chars}),
                 "tool_call_id": "c%d" % seq},
            ],
        }

    def test_restore_from_last_snapshot(self):
        records = [self._normal_record(i) for i in range(10)]
        records.append(self._snapshot_record(100))
        records += [self._normal_record(i) for i in range(101, 106)]
        sm = self._manager(records)
        mem = MemoryManager()
        sm._restore_from_archive("s1", mem)

        self.assertGreaterEqual(len(mem.messages), 2)
        self.assertTrue(any(m.get("_is_compacted_notes") for m in mem.messages))
        joined = "".join(str(m.get("content") or "") for m in mem.messages)
        self.assertNotIn("x" * 4000, joined[: 4000], "快照之前的记录不应恢复")

    def test_restore_without_snapshot_uses_budget(self):
        records = [self._normal_record(i) for i in range(60)]
        sm = self._manager(records)
        sm._restore_token_budget = lambda: 20000
        mem = MemoryManager()
        sm._restore_from_archive("s2", mem)

        self.assertGreater(len(mem.messages), 0)
        self.assertLess(len(mem.messages), 60 * 2, "应按预裁剪，不是全量")

    def test_lazy_and_full_paths_agree(self):
        records = [self._normal_record(i) for i in range(40)]
        records.append(self._snapshot_record(200))
        records += [self._normal_record(i) for i in range(201, 221)]

        sm_a = self._manager(records)
        mem_a = MemoryManager()
        sm_a._restore_from_archive("s3", mem_a)

        # 禁用快照快捷路径，强制走惰性加载
        class NoSnapArchive(FakeArchive):
            def find_last_snapshot_seq(self, session_id):
                return None

        sm_b = SessionManager()
        sm_b.three_layer_memory = SimpleNamespace(archive=NoSnapArchive(records))
        sm_b._restore_token_budget = lambda: 10 ** 9
        mem_b = MemoryManager()
        sm_b._restore_from_archive("s4", mem_b)

        a = [(m.get("role"), str(m.get("content") or "")[:80]) for m in mem_a.messages]
        b = [(m.get("role"), str(m.get("content") or "")[:80]) for m in mem_b.messages]
        self.assertEqual(a, b, "快照路径与惰性加载路径应得到相同消息序列")


# ---------------------------------------------------------------- 7. 工具入口

class TestCompactTool(unittest.TestCase):

    def test_mutates_runtime_memory_instance(self):
        mem = MemoryManager()
        mem.messages = [{"role": "user", "content": "任务A"}]
        for i in range(40):
            mem.messages += tool_turn(i)
        before = len(mem.messages)

        tool = MemoryTool(three_layer_memory=None)
        tool.set_session_id("tool-test")
        tool.set_runtime_memory(mem)

        res = tool.execute({
            "command": "compact",
            "summary": "用户要求打包 whl",
            "goal": "打包 whl",
            "actions": ["验证链路"],
            "pending": ["提交"],
        })
        self.assertTrue(res.get("success"), res)
        self.assertEqual(res.get("before"), before)
        self.assertEqual(len(mem.messages), res.get("after"))
        self.assertTrue(mem.messages[0].get("_is_compacted_notes"))

    def test_requires_summary(self):
        tool = MemoryTool(three_layer_memory=None)
        tool.set_runtime_memory(MemoryManager())
        res = tool.execute({"command": "compact"})
        self.assertFalse(res.get("success"))
        self.assertIn("summary", res.get("error", ""))

    def test_empty_session_rejected(self):
        tool = MemoryTool(three_layer_memory=None)
        tool.set_runtime_memory(MemoryManager())
        res = tool.execute({"command": "compact", "summary": "x"})
        self.assertFalse(res.get("success"))


# ---------------------------------------------------------------- 8. 缓存与窗口校准

class TestTokenCacheAndWindow(unittest.TestCase):

    def test_cache_consistent(self):
        msgs = [{"role": "tool", "content": "q" * 500, "tool_call_id": "c%d" % i}
                for i in range(50)]
        _MSG_TOKEN_MEMO.clear()
        v1 = _estimate_messages_tokens(msgs)
        v2 = _estimate_messages_tokens(msgs)
        self.assertEqual(v1, v2)

    def test_incremental_growth(self):
        msgs = [{"role": "tool", "content": "q" * 500, "tool_call_id": "c%d" % i}
                for i in range(50)]
        v1 = _estimate_messages_tokens(msgs)
        msgs.append({"role": "tool", "content": "n" * 500, "tool_call_id": "new"})
        v2 = _estimate_messages_tokens(msgs)
        self.assertGreater(v2, v1)

    def test_effective_window_keeps_peak_after_reset(self):
        e = OpenAICompatibleEngine.__new__(OpenAICompatibleEngine)
        e._model_info = SimpleNamespace(context_window=200000)
        e._last_prompt_tokens = 0
        e._max_prompt_tokens = 0
        self.assertEqual(e.effective_context_window, 200000)

        e._record_prompt_tokens(605300)
        peak = int(605300 * 1.25)
        self.assertEqual(e.effective_context_window, peak)

        e.reset_last_prompt_tokens()
        self.assertEqual(e.effective_context_window, peak, "reset 不应让窗口回落")

    def test_effective_window_no_growth_when_config_larger(self):
        e = OpenAICompatibleEngine.__new__(OpenAICompatibleEngine)
        e._model_info = SimpleNamespace(context_window=1000000)
        e._last_prompt_tokens = 0
        e._max_prompt_tokens = 0
        e._record_prompt_tokens(400000)
        self.assertEqual(e.effective_context_window, 1000000)


class TestNotesMaxLength(unittest.TestCase):
    def test_adaptive_by_context_window(self):
        big = SessionCompactor(llm_engine=SimpleNamespace(context_window=1_000_000))
        self.assertEqual(big._notes_max_length(), 20000)

        mid = SessionCompactor(llm_engine=SimpleNamespace(context_window=200_000))
        self.assertEqual(mid._notes_max_length(), 4000)

        none = SessionCompactor(llm_engine=None)
        self.assertEqual(none._notes_max_length(), 8000)

    def test_explicit_value_wins(self):
        comp = SessionCompactor(
            llm_engine=SimpleNamespace(context_window=1_000_000),
            max_notes_length=2000,
        )
        self.assertEqual(comp._notes_max_length(), 2000)

    def test_long_notes_survive_adaptive_limit(self):
        notes = SessionNotes("long-notes", notes_dir=tempfile.mkdtemp())
        notes.set_completed([f"完成项 {i} " + "x" * 700 for i in range(10)])
        notes.save()

        mem = MemoryManager()
        for i in range(20):
            mem.add_user_message(f"Q{i}")
            mem.add_assistant_message(f"A{i}")

        comp = SessionCompactor(llm_engine=SimpleNamespace(context_window=1_000_000))
        comp._replace_old_messages(mem, notes, summary="摘要")

        head = mem.messages[0]["content"]
        self.assertNotIn("[笔记已截断]", head)
        self.assertGreater(len(head), 2000)

    def test_long_notes_truncated_when_explicit_small(self):
        notes = SessionNotes("small-notes", notes_dir=tempfile.mkdtemp())
        notes.set_completed([f"完成项 {i} " + "x" * 700 for i in range(10)])
        notes.save()

        mem = MemoryManager()
        for i in range(20):
            mem.add_user_message(f"Q{i}")
            mem.add_assistant_message(f"A{i}")

        comp = SessionCompactor(
            llm_engine=SimpleNamespace(context_window=1_000_000),
            max_notes_length=2000,
        )
        comp._replace_old_messages(mem, notes, summary="摘要")

        self.assertIn("[笔记已截断]", mem.messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
