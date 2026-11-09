# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.loop.memory import MemoryManager
from app.agent.loop.rewind import RewindError, preview_rewind, rewind_session
from app.agent.memory.archive_store import ArchiveStore


class FakeStore:
    def __init__(self, count=0):
        self.count = count
        self.deltas = []

    def get_or_create_session(self, session_id):
        return SimpleNamespace(message_count=self.count)

    def update_message_count(self, session_id, delta=1):
        self.deltas.append(delta)
        self.count += delta


class FakeBroker:
    def __init__(self):
        self.events = []

    def publish(self, session_id, event):
        self.events.append((session_id, event))


class FakeUndo:
    def __init__(self):
        self.calls = []

    def rollback(self, session_id, keep_turn_ids):
        self.calls.append((session_id, keep_turn_ids))
        return {
            "restored": 1,
            "skipped": [],
            "files": [{"path": "/tmp/a.txt", "added": 2, "removed": 1}],
            "warning": None,
        }

    def preview(self, session_id, keep_turn_ids):
        self.calls.append((session_id, keep_turn_ids))
        return {
            "restored": 1,
            "skipped": [],
            "files": [{"path": "/tmp/a.txt", "added": 2, "removed": 1}],
            "warning": None,
        }


class FakeManager:
    three_layer_memory = None

    def __init__(self, memory):
        self.memory = memory

    def get_or_create(self, session_id):
        return SimpleNamespace(memory=self.memory)


def run_rewind(memory, message_id, count=0):
    manager = FakeManager(memory)
    store = FakeStore(count)
    broker = FakeBroker()
    undo = FakeUndo()
    with patch("app.agent.loop.rewind.get_session_manager", return_value=manager), \
            patch("app.agent.loop.rewind.get_session_store", return_value=store), \
            patch("app.agent.loop.rewind.get_message_broker", return_value=broker), \
            patch("app.core.util.undo_store.get_undo_store", return_value=undo):
        result = rewind_session("s-rewind", message_id)
    return result, store, broker, undo


class TestMemoryIds(unittest.TestCase):

    def test_ids_unique_and_user_returns_id(self):
        m = MemoryManager()
        uid = m.add_user_message("q")
        m.add_assistant_message("a")
        m.add_tool_result("call_1", {"ok": True})
        ids = [msg["id"] for msg in m.messages]
        self.assertEqual(ids[0], uid)
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(i.startswith("m_") for i in ids))

    def test_ephemeral_has_no_id(self):
        m = MemoryManager()
        m.add_ephemeral_message("user", "temp")
        self.assertNotIn("id", m._ephemeral_messages[0])


class TestRewindSession(unittest.TestCase):

    def _memory(self):
        m = MemoryManager()
        m.add_user_message("第一轮")
        m.add_assistant_message("答1")
        m.add_user_message("第二轮")
        m.add_assistant_message("答2")
        return m

    def test_truncates_target_and_after(self):
        m = self._memory()
        target = m.messages[2]["id"]
        result, store, broker, undo = run_rewind(m, target, count=4)
        self.assertEqual(len(m.messages), 2)
        self.assertEqual(m.messages[0]["role"], "user")
        self.assertEqual(result["removed_count"], 2)
        self.assertEqual(store.deltas, [-2])
        self.assertEqual(broker.events[0][1]["type"], "messages_rewound")
        self.assertEqual(broker.events[0][1]["removed_count"], 2)
        self.assertEqual(undo.calls[0][1], [m.messages[0]["id"]])
        self.assertEqual(result["files_restored"], 1)
        self.assertEqual(result["files"][0]["added"], 2)
        self.assertEqual(result["files"][0]["removed"], 1)

    def test_preview_does_not_truncate(self):
        m = self._memory()
        target = m.messages[2]["id"]
        manager = FakeManager(m)
        undo = FakeUndo()
        with patch("app.agent.loop.rewind.get_session_manager", return_value=manager), \
                patch("app.core.util.undo_store.get_undo_store", return_value=undo):
            result = preview_rewind("s-preview", target)
        self.assertEqual(len(m.messages), 4)
        self.assertEqual(result["removed_count"], 2)
        self.assertEqual(result["files"][0]["added"], 2)
        self.assertEqual(undo.calls[0][1], [m.messages[0]["id"]])

    def test_not_found(self):
        m = self._memory()
        with self.assertRaises(RewindError) as ctx:
            run_rewind(m, "m_missing")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_not_user(self):
        m = self._memory()
        with self.assertRaises(RewindError) as ctx:
            run_rewind(m, m.messages[1]["id"])
        self.assertEqual(ctx.exception.code, "not_user")

    def test_compacted_boundary(self):
        m = MemoryManager()
        m.add_user_message("摘要")
        m.messages[-1]["_is_compacted_notes"] = True
        m.add_user_message("新问题")
        with self.assertRaises(RewindError) as ctx:
            run_rewind(m, m.messages[0]["id"])
        self.assertEqual(ctx.exception.code, "compacted")


class TestArchiveDeleteAfter(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = ArchiveStore(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_deletes_target_and_later(self):
        self.store.append_messages("s1", [
            {"id": "m1", "role": "user", "content": "q1"},
            {"id": "m2", "role": "assistant", "content": "a1"},
        ])
        self.store.append_messages("s1", [
            {"id": "m3", "role": "user", "content": "q2"},
            {"id": "m4", "role": "assistant", "content": "a2"},
        ])
        self.store.append_messages("other", [
            {"id": "x1", "role": "user", "content": "other"},
        ])
        removed = self.store.delete_after("s1", "m3")
        self.assertEqual(removed, 2)
        ids = [msg["id"] for rec in self.store.get_by_session("s1", limit=50)
               for msg in rec.get("messages", [])]
        self.assertEqual(ids, ["m1", "m2"])
        other = [msg["id"] for rec in self.store.get_by_session("other", limit=50)
                 for msg in rec.get("messages", [])]
        self.assertEqual(other, ["x1"])

    def test_missing_target_is_noop(self):
        self.store.append_messages("s1", [{"id": "m1", "role": "user", "content": "q"}])
        before = open(os.path.join(self.tmp, os.listdir(self.tmp)[0]), "rb").read()
        self.assertEqual(self.store.delete_after("s1", "nope"), 0)
        after = open(os.path.join(self.tmp, os.listdir(self.tmp)[0]), "rb").read()
        self.assertEqual(before, after)


class TestRouteRegistered(unittest.TestCase):

    def test_rewind_route_exists(self):
        from app.server.routes.chat import router
        paths = {getattr(r, "path", "") for r in router.routes}
        self.assertIn("/api/chat/rewind", paths)
        self.assertIn("/api/chat/rewind/preview", paths)


class TestToolExecutorHook(unittest.TestCase):

    def test_edit_checkpoint_via_executor(self):
        import shutil
        from app.agent.loop.tool_executor import ToolExecutor
        from app.core.util import undo_store as us

        tmp = tempfile.mkdtemp()
        original_root = us._global_store
        try:
            store = us.UndoStore()
            store._root = os.path.join(tmp, "undo")
            os.makedirs(store._root, exist_ok=True)
            us._global_store = store

            path = os.path.join(tmp, "f.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("v1")

            executor = ToolExecutor({}, {})
            executor.set_turn_context("s-hook", "t1")
            executor._undo_checkpoint("edit", {"file_path": path})
            with open(path, "w", encoding="utf-8") as f:
                f.write("v2")

            executor.set_turn_context(None, None)
            store.rollback("s-hook", [])
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "v1")
        finally:
            us._global_store = original_root
            shutil.rmtree(tmp, ignore_errors=True)


class TestRenderableIds(unittest.TestCase):

    def test_id_passthrough(self):
        from app.server.routes.chat import _messages_to_renderable
        out = _messages_to_renderable([{"id": "m_1", "role": "user", "content": "hi"}])
        self.assertEqual(out[0]["id"], "m_1")

    def test_can_rewind_only_for_tracked_ids(self):
        from app.server.routes.chat import _messages_to_renderable
        raw = [
            {"id": "m_1", "role": "user", "content": "a"},
            {"id": "m_2", "role": "user", "content": "b"},
        ]
        out = _messages_to_renderable(raw, {"m_2"})
        self.assertFalse(out[0]["can_rewind"])
        self.assertTrue(out[1]["can_rewind"])

    def test_compacted_notes_never_rewindable(self):
        from app.server.routes.chat import _messages_to_renderable
        raw = [{"id": "m_1", "role": "user", "content": "a", "_is_compacted_notes": True}]
        out = _messages_to_renderable(raw, {"m_1"})
        self.assertFalse(out[0]["can_rewind"])


if __name__ == "__main__":
    unittest.main()
