# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_tracking(sid, turn_id):
    from app.core.util.undo_store import get_undo_store
    store = get_undo_store()
    store.begin_turn(sid, turn_id)
    probe = os.path.join(tempfile.gettempdir(), f"cellium_probe_{sid}_{turn_id}")
    store.checkpoint_paths(sid, turn_id, [probe])


class TestRewindCommand(unittest.TestCase):

    def test_undo_removes_last_round(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_manager import get_session_manager
        from app.agent.loop.session_store import get_session_store

        sid = f"tui_undo_{int(time.time())}"
        manager = get_session_manager()

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                memory = manager.get_or_create(sid).memory
                memory.messages.clear()
                memory.add_user_message("第一轮")
                memory.add_assistant_message("回复一")
                second = memory.add_user_message("第二轮")
                memory.add_assistant_message("回复二")
                self.assertEqual(len(memory.messages), 4)
                _seed_tracking(sid, second)
                await app._rewind_last_round()
                await pilot.pause()
                from app.tui.session_picker import RewindConfirmScreen
                self.assertIsInstance(app.screen, RewindConfirmScreen)
                await pilot.press("y")
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(len(memory.messages), 2)
                self.assertEqual(memory.messages[0]["content"], "第一轮")
                self.assertEqual(memory.messages[1]["content"], "回复一")

        try:
            asyncio.run(run())
        finally:
            manager.get_or_create(sid).memory.messages.clear()
            manager.close_session(sid)
            get_session_store().delete_session(sid)
            from app.core.util.undo_store import get_undo_store
            get_undo_store().drop_session(sid)

    def test_click_resolves_message_id_by_content(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_manager import get_session_manager
        from app.agent.loop.session_store import get_session_store

        sid = f"tui_click_{int(time.time())}"
        manager = get_session_manager()

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                memory = manager.get_or_create(sid).memory
                memory.messages.clear()
                first = memory.add_user_message("第一轮")
                memory.add_assistant_message("回复一")
                second = memory.add_user_message("第二轮")
                _seed_tracking(sid, second)
                app.action_rewind_to(None, "第二轮")
                await pilot.pause()
                self.assertIsNotNone(app.screen)
                self.assertEqual(app._find_user_msg_id("第二轮"), second)
                self.assertEqual(app._find_user_msg_id(), second)
                self.assertEqual(app._find_user_msg_id("第一轮"), first)
                self.assertEqual(app._find_user_msg_id("不存在"), None)

        try:
            asyncio.run(run())
        finally:
            manager.get_or_create(sid).memory.messages.clear()
            manager.close_session(sid)
            get_session_store().delete_session(sid)
            from app.core.util.undo_store import get_undo_store
            get_undo_store().drop_session(sid)

    def test_click_user_message_opens_confirm(self):
        from app.tui.app import CelliumTUI
        from app.tui.widgets import UserMessage
        from app.tui.session_picker import RewindConfirmScreen
        from app.agent.loop.session_manager import get_session_manager
        from app.agent.loop.session_store import get_session_store

        sid = f"tui_click_mount_{int(time.time())}"
        manager = get_session_manager()

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                memory = manager.get_or_create(sid).memory
                memory.messages.clear()
                mid = memory.add_user_message("你好")
                _seed_tracking(sid, mid)
                widget = UserMessage("你好", mid, True)
                await app.chat.mount(widget)
                await pilot.pause()
                await pilot.click(widget)
                await pilot.pause()
                await pilot.pause()
                self.assertIsInstance(app.screen, RewindConfirmScreen)
                await app.pop_screen()

        try:
            asyncio.run(run())
        finally:
            manager.get_or_create(sid).memory.messages.clear()
            manager.close_session(sid)
            get_session_store().delete_session(sid)
            from app.core.util.undo_store import get_undo_store
            get_undo_store().drop_session(sid)

    def test_untracked_message_click_no_confirm(self):
        from app.tui.app import CelliumTUI
        from app.tui.widgets import UserMessage
        from app.tui.session_picker import RewindConfirmScreen
        from app.agent.loop.session_manager import get_session_manager
        from app.agent.loop.session_store import get_session_store

        sid = f"tui_untracked_{int(time.time())}"
        manager = get_session_manager()

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                memory = manager.get_or_create(sid).memory
                memory.messages.clear()
                mid = memory.add_user_message("你好")
                widget = UserMessage("你好", mid)
                await app.chat.mount(widget)
                await pilot.pause()
                await pilot.click(widget)
                await pilot.pause()
                self.assertNotIsInstance(app.screen, RewindConfirmScreen)

        try:
            asyncio.run(run())
        finally:
            manager.get_or_create(sid).memory.messages.clear()
            manager.close_session(sid)
            get_session_store().delete_session(sid)
            from app.core.util.undo_store import get_undo_store
            get_undo_store().drop_session(sid)

    def test_history_spec_rewindable_flag(self):
        from app.tui.app import CelliumTUI
        app = object.__new__(CelliumTUI)
        app._tracked_ids = {"m1"}
        tracked = app._build_spec_widget({"kind": "user", "content": "hi", "id": "m1"})
        untracked = app._build_spec_widget({"kind": "user", "content": "hi", "id": "m2"})
        self.assertTrue(tracked.rewindable)
        self.assertFalse(untracked.rewindable)

    def test_format_rewind_files(self):
        from app.tui.session_picker import format_rewind_files
        files = [
            {"path": "src/agent.py", "added": 12, "removed": 5, "kind": "modified"},
            {"path": "README.md", "added": 0, "removed": 0, "kind": "restored"},
            {"path": "tmp/notes.txt", "added": 0, "removed": 0, "kind": "deleted"},
        ]
        text = format_rewind_files(files)
        self.assertIn("+12", text)
        self.assertIn("-5", text)
        self.assertIn("还原", text)
        self.assertIn("删除", text)
        self.assertIn("src/agent.py", text)

    def test_format_rewind_files_empty_and_more(self):
        from app.tui.session_picker import format_rewind_files
        self.assertIn("无文件改动", format_rewind_files([]))
        many = [{"path": f"d/f{i}.txt", "added": i, "removed": 0} for i in range(12)]
        text = format_rewind_files(many, limit=8)
        self.assertIn("还有 4 个文件", text)
        skipped = format_rewind_files(many[:1], ["a", "b"], limit=8)
        self.assertIn("2 个文件无法回滚", skipped)

    def test_format_rewind_files_binary(self):
        from app.tui.session_picker import format_rewind_files
        files = [{"path": "assets/logo.png", "added": 0, "removed": 0,
                  "kind": "modified", "binary": True}]
        text = format_rewind_files(files)
        self.assertIn("二进制", text)
        self.assertNotIn("+0", text)

    def test_history_plan_carries_user_id(self):
        from app.tui.history_range import plan_slice
        plan = plan_slice([{"id": "m1", "role": "user", "content": "hi"}], 0, 1)
        self.assertEqual(plan[0]["id"], "m1")


if __name__ == "__main__":
    unittest.main()
