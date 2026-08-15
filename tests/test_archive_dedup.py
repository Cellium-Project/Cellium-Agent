# -*- coding: utf-8 -*-
"""
压缩归档回归测试（方案 C）

覆盖：
  - ArchiveStore 消息级去重（每条原始消息只存一次）
  - _persist_conversation 增量保存（交互收尾增量 / 压缩基准 / 快照过滤）
  - 压缩补写 old_messages 只新增未归档消息
  - _restore_from_archive 恢复（摘要占位 + 压缩后增量，旧消息不进上下文）
  - WebUI 历史完整展示（读全部记录去重）
  - engine 与 stream_normalizer 的 _think_fragment_tail 一致性
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.memory.archive_store import ArchiveStore
from app.agent.loop.agent_loop import AgentLoop
from app.agent.loop.memory import MemoryManager
from app.agent.loop.session_manager import SessionManager


class FakeTLM:
    """最小 ThreeLayerMemory 替身：只提供 archive 与 persist_session"""

    def __init__(self, archive):
        self.archive = archive
        self.last_messages = None

    def persist_session(self, user_input, response, session_id="default", messages=None):
        self.last_messages = list(messages) if messages else None
        return self.archive.append_messages(session_id=session_id, messages=messages or [])


def make_loop(archive, session_id="s1"):
    """构造最小 AgentLoop（绕过 __init__，仅注入归档所需依赖）"""
    loop = AgentLoop.__new__(AgentLoop)
    loop.session_id = session_id
    loop.three_layer_memory = FakeTLM(archive)
    loop._event_publisher = None
    return loop


def snapshot(content="[系统压缩] 摘要..."):
    return {"role": "user", "content": content, "_is_compacted_notes": True}


def all_archived_messages(archive, session_id):
    records = archive.get_by_session(session_id, limit=1000)
    return [m for rec in records for m in rec.get("messages", [])]


def unique_keys(messages):
    return {json.dumps(m, sort_keys=True, ensure_ascii=False) for m in messages}


class ArchiveDirTest(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.archive_dir = os.path.join(self.test_dir, "archive")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _archive(self):
        return ArchiveStore(base_dir=self.archive_dir)


class TestArchiveMessageDedup(ArchiveDirTest):
    """ArchiveStore 消息级去重：每条原始消息只归档一次"""

    def test_identical_messages_dedup(self):
        arch = self._archive()
        msgs = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}]
        id1 = arch.append_messages("s1", msgs)
        id2 = arch.append_messages("s1", msgs)
        self.assertEqual(id1, id2, "相同消息应幂等返回同一 id")
        self.assertEqual(len(arch.get_by_session("s1", 100)), 1, "相同消息不应重复归档")

    def test_same_content_different_timestamp_dedup(self):
        arch = self._archive()
        arch.append_messages("s1", [{"role": "user", "content": "你好", "timestamp": "t1"}])
        arch.append_messages("s1", [{"role": "user", "content": "你好", "timestamp": "t2"}])
        self.assertEqual(len(arch.get_by_session("s1", 100)), 1,
                         "去重键不含 timestamp，相同内容应合并")

    def test_different_messages_kept(self):
        arch = self._archive()
        arch.append_messages("s1", [{"role": "user", "content": "u1"}])
        arch.append_messages("s1", [{"role": "user", "content": "u2"}])
        self.assertEqual(len(arch.get_by_session("s1", 100)), 2, "不同消息都应保留")

    def test_different_session_kept(self):
        arch = self._archive()
        msg = [{"role": "user", "content": "u1"}]
        arch.append_messages("s1", msg)
        arch.append_messages("s2", msg)
        self.assertEqual(len(arch.get_by_session("s1", 100)), 1)
        self.assertEqual(len(arch.get_by_session("s2", 100)), 1, "不同 session 不应互相去重")

    def test_dedup_survives_new_instance(self):
        arch1 = self._archive()
        msgs = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}]
        arch1.append_messages("s1", msgs)
        arch2 = self._archive()  # 模拟进程重启：重新扫描文件
        arch2.append_messages("s1", msgs)
        self.assertEqual(len(arch2.get_by_session("s1", 100)), 1, "重启后去重键应重建且仍生效")

    def test_compact_backfill_dedup(self):
        arch = self._archive()
        # 交互1 已归档
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
        # 压缩补写 old（含已归档的 u1,a1 + 新 u2,a2）
        old = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        arch.append_messages("s1", old)
        records = arch.get_by_session("s1", 100)
        self.assertEqual(len(records), 2, "补写应产生一条新记录")
        last = records[-1]["messages"]
        contents = [m["content"] for m in last]
        self.assertEqual(contents, ["u2", "a2"], "补写应只新增未归档消息")


class TestPersistConversationIncremental(ArchiveDirTest):
    """真实 _persist_conversation 的增量保存逻辑"""

    def test_interaction_incremental_no_dup(self):
        arch = self._archive()
        loop = make_loop(arch)
        m = MemoryManager()
        m.add_user_message("u1")
        m.add_assistant_message("a1")
        loop._persist_conversation("u1", "a1", "s1", m)
        loop._persist_conversation("u1", "a1", "s1", m)  # 同轮重复持久化应幂等
        self.assertEqual(len(arch.get_by_session("s1", 100)), 1, "同轮重复持久化不应重复归档")

    def test_multiple_interactions_no_dup(self):
        arch = self._archive()
        loop = make_loop(arch)
        for i in range(3):
            m = MemoryManager()
            m.add_user_message(f"u{i}")
            m.add_assistant_message(f"a{i}")
            loop._persist_conversation(f"u{i}", f"a{i}", "s1", m)
        records = arch.get_by_session("s1", 100)
        self.assertEqual(len(records), 3, "每交互一条增量记录")
        total = sum(len(r["messages"]) for r in records)
        self.assertEqual(total, len(unique_keys(all_archived_messages(arch, "s1"))),
                         "多交互归档不应有重复消息")

    def test_compact_flow_no_dup_and_no_snapshot_leak(self):
        arch = self._archive()
        loop = make_loop(arch)
        # 交互1
        m1 = MemoryManager()
        m1.add_user_message("u1")
        m1.add_assistant_message("a1")
        loop._persist_conversation("u1", "a1", "s1", m1)

        # 交互2（累积触发压缩）
        m2 = MemoryManager()
        m2.messages = list(m1.get_messages()) + [
            {"role": "user", "content": "u2", "timestamp": "t"},
            {"role": "assistant", "content": "a2", "timestamp": "t"},
            {"role": "assistant", "content": "a3", "timestamp": "t"},
            {"role": "assistant", "content": "a4", "timestamp": "t"},
        ]
        # 压缩补写将被替换的原文
        old = m2.get_messages()[:-2]
        arch.append_messages("s1", old)
        # 压缩替换 memory 为 [快照 + 保留原文]
        m2.messages = [snapshot()] + m2.get_messages()[-2:]
        loop._persist_conversation("", None, "s1", m2)  # 压缩基准（唯一含快照）
        m2.add_assistant_message("a5")
        loop._persist_conversation("u2", None, "s1", m2)  # 交互2 收尾增量

        # 交互3
        m3 = MemoryManager()
        m3.messages = list(m2.get_messages()) + [
            {"role": "user", "content": "u3", "timestamp": "t"},
            {"role": "assistant", "content": "a6", "timestamp": "t"},
        ]
        loop._persist_conversation("u3", None, "s1", m3)

        records = arch.get_by_session("s1", 100)
        total = sum(len(r["messages"]) for r in records)
        self.assertEqual(total, len(unique_keys(all_archived_messages(arch, "s1"))),
                         "压缩流程归档不应有重复存储")

        # 快照只出现在压缩基准记录（最多一条含快照）
        snapshot_records = [r for r in records if any(
            m.get("_is_compacted_notes") for m in r["messages"])]
        self.assertEqual(len(snapshot_records), 1, "快照只应归档一次（压缩基准）")

    def test_regular_round_excludes_snapshot_and_takes_last_user(self):
        arch = self._archive()
        tlm = FakeTLM(arch)
        loop = AgentLoop.__new__(AgentLoop)
        loop.session_id = "s1"
        loop.three_layer_memory = tlm
        loop._event_publisher = None
        # 压缩后 memory：[快照 + 保留原文 + 新交互]
        m = MemoryManager()
        m.messages = [
            snapshot(),
            {"role": "assistant", "content": "a3", "timestamp": "t"},
            {"role": "assistant", "content": "a4", "timestamp": "t"},
            {"role": "user", "content": "u3", "timestamp": "t"},
            {"role": "assistant", "content": "a6", "timestamp": "t"},
        ]
        loop._persist_conversation("u3", None, "s1", m)
        sent = tlm.last_messages
        self.assertIsNotNone(sent)
        self.assertFalse(any(msg.get("_is_compacted_notes") for msg in sent),
                         "普通轮次不应把快照传给归档")
        self.assertEqual([msg["content"] for msg in sent], ["u3", "a6"],
                         "普通轮次应只取最后非快照 user 之后的新增消息")

    def test_compact_baseline_includes_snapshot(self):
        arch = self._archive()
        tlm = FakeTLM(arch)
        loop = AgentLoop.__new__(AgentLoop)
        loop.session_id = "s1"
        loop.three_layer_memory = tlm
        loop._event_publisher = None
        m = MemoryManager()
        m.messages = [snapshot(), {"role": "assistant", "content": "a3", "timestamp": "t"}]
        loop._persist_conversation("", None, "s1", m)
        sent = tlm.last_messages
        self.assertIsNotNone(sent)
        self.assertTrue(any(msg.get("_is_compacted_notes") for msg in sent),
                        "压缩基准应包含快照")


class TestArchiveRestore(ArchiveDirTest):
    """真实 _restore_from_archive 恢复逻辑"""

    def _manager(self, archive):
        return SessionManager(three_layer_memory=FakeTLM(archive))

    def test_restore_full_without_compact(self):
        arch = self._archive()
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
        arch.append_messages("s1", [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}])
        mem = MemoryManager()
        self._manager(arch)._restore_from_archive("s1", mem)
        ctx = [m["content"] for m in mem.get_messages()]
        self.assertEqual(ctx, ["u1", "a1", "u2", "a2"], "无压缩时应恢复完整历史")

    def test_restore_compact_context(self):
        arch = self._archive()
        # 交互1
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
        # 压缩补写 old
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"},
                                    {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}])
        # 压缩基准（含快照 + 保留原文）
        arch.append_messages("s1", [snapshot(),
                                    {"role": "assistant", "content": "a3"},
                                    {"role": "assistant", "content": "a4"}])
        # 压缩后增量
        arch.append_messages("s1", [{"role": "assistant", "content": "a5"}])
        arch.append_messages("s1", [{"role": "user", "content": "u3"}, {"role": "assistant", "content": "a6"}])

        mem = MemoryManager()
        self._manager(arch)._restore_from_archive("s1", mem)
        ctx = [m["content"] for m in mem.get_messages()]
        self.assertTrue(ctx[0].startswith("[系统压缩]"), "上下文应以摘要占位开头")
        self.assertNotIn("u1", ctx, "压缩前旧消息不应进上下文")
        self.assertNotIn("a2", ctx, "压缩前旧消息不应进上下文")
        self.assertIn("a3", ctx, "压缩保留原文应在上下文")
        self.assertIn("a5", ctx, "压缩后增量应在上下文")
        self.assertIn("u3", ctx, "后续交互应在上下文")
        self.assertEqual(len(ctx), len(set(ctx)), "恢复上下文不应有重复消息")

    def test_restore_dedup_duplicate_records(self):
        arch = self._archive()
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
        mem = MemoryManager()
        self._manager(arch)._restore_from_archive("s1", mem)
        ctx = [m["content"] for m in mem.get_messages()]
        self.assertEqual(ctx, ["u1", "a1"], "恢复应去重，不重复注入相同消息")

    def test_webui_history_complete(self):
        arch = self._archive()
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
        arch.append_messages("s1", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"},
                                    {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}])
        arch.append_messages("s1", [snapshot(),
                                    {"role": "assistant", "content": "a3"}])
        arch.append_messages("s1", [{"role": "user", "content": "u3"}, {"role": "assistant", "content": "a4"}])

        records = arch.get_by_session("s1", 1000)
        webui = []
        seen = set()
        for rec in reversed(records):
            filtered = [m for m in rec["messages"]
                        if not (m.get("role") == "user" and m.get("_is_compacted_notes"))]
            for msg in reversed(filtered):
                k = json.dumps(msg, sort_keys=True, ensure_ascii=False)
                if k not in seen:
                    seen.add(k)
                    webui.append(msg)
        hist = [m["content"] for m in reversed(webui)]
        self.assertEqual(hist, ["u1", "a1", "u2", "a2", "a3", "u3", "a4"],
                         "WebUI 应展示完整历史（含压缩前原文）")


class TestThinkFragmentConsistency(unittest.TestCase):
    """engine 与 stream_normalizer 复用同一 _think_fragment_tail，防止分叉"""

    def test_same_function_object(self):
        from app.messaging.stream_normalizer import _think_fragment_tail as sn
        from app.agent.llm.engine import _think_fragment_tail as eng
        self.assertIs(sn, eng, "应导入同一函数，避免行为分叉")

    def test_longest_match(self):
        from app.agent.llm.engine import _think_fragment_tail as eng
        self.assertEqual(eng("xxx</think"), "</think")
        self.assertEqual(eng("xxx<think"), "<think")
        self.assertEqual(eng("plain text"), "")


if __name__ == "__main__":
    unittest.main()
