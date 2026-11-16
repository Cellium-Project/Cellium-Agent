# -*- coding: utf-8 -*-
"""归档检索：压缩掉的原始消息要能按关键词召回"""
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.memory.archive_store import ArchiveStore
from app.agent.memory.archive_sqlite import SqliteArchiveStore
from app.agent.tools.memory_tool import MemoryTool

MSGS = [
    {"role": "user", "content": "帮我看下 aiotieba 装不上是为什么"},
    {"role": "assistant", "content": "报错是 ModuleNotFoundError: No module named 'aiotieba'"},
    {"role": "tool", "content": "pip install aiotieba>=4.8 输出 Successfully installed"},
    {"role": "assistant", "content": "已经装好了，可以重新加载组件"},
]


class TestArchiveStoreRecall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cellium_arc_")
        self.store = ArchiveStore(base_dir=self.tmp)
        self.store.append_messages(session_id="s1", messages=MSGS)
        self.store.append_messages(
            session_id="s2",
            messages=[{"role": "user", "content": "另一个会话里的无关内容"}],
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hit_by_keyword(self):
        hits = self.store.search_messages("aiotieba")
        self.assertGreaterEqual(len(hits), 2)
        self.assertTrue(all("aiotieba" in h["snippet"].lower() for h in hits[:2]))

    def test_session_filter(self):
        hits = self.store.search_messages("aiotieba", session_id="s2")
        self.assertEqual(hits, [])

    def test_context_included(self):
        hits = self.store.search_messages("ModuleNotFoundError", session_id="s1", context=1)
        self.assertTrue(hits)
        self.assertGreaterEqual(len(hits[0]["context"]), 2)

    def test_empty_query(self):
        self.assertEqual(self.store.search_messages(""), [])


class TestSqliteArchiveRecall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cellium_arcdb_")
        self.store = SqliteArchiveStore(
            db_path=os.path.join(self.tmp, "archive.db"),
            jsonl_dir=None,
            dual_write=False,
        )
        self.store.append_messages(session_id="s1", messages=MSGS)
        self.store.flush(min_interval=0)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hit_by_keyword(self):
        hits = self.store.search_messages("aiotieba")
        self.assertTrue(hits, "SQLite 归档应能按关键词召回")
        self.assertEqual(hits[0]["session_id"], "s1")

    def test_session_filter(self):
        self.assertEqual(self.store.search_messages("aiotieba", session_id="nope"), [])

    def test_context_included(self):
        hits = self.store.search_messages("ModuleNotFoundError", context=1)
        self.assertTrue(hits)
        self.assertTrue(hits[0]["context"])


class _FakeMemory:
    def __init__(self, archive):
        self.archive = archive


class TestMemoryToolRecall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cellium_rectool_")
        store = ArchiveStore(base_dir=self.tmp)
        store.append_messages(session_id="s1", messages=MSGS)
        self.tool = MemoryTool(_FakeMemory(store))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recall_command_registered(self):
        enum = self.tool.definition["function"]["parameters"]["properties"]["command"]["enum"]
        self.assertIn("recall", enum)

    def test_recall_returns_hits(self):
        out = self.tool.execute({"command": "recall", "query": "aiotieba"})
        self.assertTrue(out["success"])
        self.assertGreaterEqual(out["count"], 1)
        self.assertIn("aiotieba", out["results"][0]["snippet"].lower())

    def test_recall_requires_query(self):
        out = self.tool.execute({"command": "recall"})
        self.assertFalse(out["success"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
