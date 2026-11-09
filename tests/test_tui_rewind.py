# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
                memory.add_user_message("第二轮")
                memory.add_assistant_message("回复二")
                self.assertEqual(len(memory.messages), 4)
                await app._rewind_last_round()
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

    def test_click_user_message_opens_confirm(self):
        from app.tui.app import CelliumTUI
        from app.tui.widgets import UserMessage
        from app.tui.session_picker import DeleteConfirmScreen

        async def run():
            app = CelliumTUI(bootstrap=None, session_id="tui_click_mount")
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                widget = UserMessage("你好", "m_test")
                await app.chat.mount(widget)
                await pilot.pause()
                await pilot.click(widget)
                await pilot.pause()
                self.assertIsInstance(app.screen, DeleteConfirmScreen)
                await app.pop_screen()

        asyncio.run(run())

    def test_history_plan_carries_user_id(self):
        from app.tui.history_range import plan_slice
        plan = plan_slice([{"id": "m1", "role": "user", "content": "hi"}], 0, 1)
        self.assertEqual(plan[0]["id"], "m1")


if __name__ == "__main__":
    unittest.main()
