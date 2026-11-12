# -*- coding: utf-8 -*-
"""TUI 工具卡片：预显示占位后，tool_start 只在原行补全，不产生重复条目"""
import asyncio
import time
import unittest

from textual.app import App, ComposeResult

from app.tui.widgets import ToolCallCard


class _CardApp(App):
    def tr(self, key, *args):
        return key.format(*args) if args else key

    def compose(self) -> ComposeResult:
        yield ToolCallCard()


class TestToolCardPending(unittest.TestCase):
    def test_pending_call_updated_in_place(self):
        async def run():
            app = _CardApp()
            async with app.run_test(size=(80, 24)) as pilot:
                card = app.query_one(ToolCallCard)
                card.add_call("c1", "shell", description="正在执行 shell 命令")
                await pilot.pause()
                card.add_call("c1", "shell", description="正在执行命令", arguments={"cmd": "ls"})
                await pilot.pause()
                return card

        card = asyncio.run(run())
        self.assertEqual(card._order, ["c1"])
        self.assertEqual(card._calls["c1"]["description"], "正在执行命令")
        self.assertEqual(card._calls["c1"]["arguments"], {"cmd": "ls"})
        self.assertEqual(card._calls["c1"]["status"], "pending")


class TestShellCardFooter(unittest.TestCase):
    """shell 行尾状态：未完成不能显示 completed，完成后必须是绿色"""

    def test_footer_states(self):
        async def run():
            app = _CardApp()
            async with app.run_test(size=(100, 30)) as pilot:
                card = app.query_one(ToolCallCard)
                card.add_call("c1", "shell", description="正在执行 shell 命令")
                await pilot.pause()
                pending_styles = [s.style for s in card._build(done=False).spans]
                pending_plain = card._build(done=False).plain

                card.update_call("c1", {"success": True, "output": "A", "exit_code": 0}, 10)
                card.add_call("c2", "shell", arguments={"cmd": "echo B"})
                card.update_call("c2", {"success": True, "message": "ok"}, 8)
                await pilot.pause()
                done_text = card._build(done=False)

                card.add_call("c3", "shell", description="正在执行 shell 命令")
                card.finish()
                final_text = card._build(done=True)
                return pending_plain, pending_styles, done_text, final_text

        pending_plain, pending_styles, done_text, final_text = asyncio.run(run())

        self.assertNotIn("shell completed", pending_plain)
        self.assertIn("shell running", pending_plain)
        self.assertNotIn("bold on", [s for s in pending_styles if s])

        green = [s for s in done_text.spans if "green" in str(s.style)]
        self.assertGreaterEqual(len(green), 2)  # 有/无 exit_code 的完成行都应是绿色
        self.assertNotIn("shell failed", done_text.plain)

        self.assertIn("shell 未执行", final_text.plain)
        self.assertNotIn("shell running", final_text.plain)


class TestReasoningFinishedOnToolCall(unittest.TestCase):
    """Reasoning 计时应止于工具调用开始，而不是等到工具执行"""

    def test_reasoning_finished_at_tool_call_pending(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_manager import get_session_manager

        sid = f"tui_reason_pending_{int(time.time())}"

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await app._handle_event({
                    "type": "reasoning",
                    "content": "先思考一下",
                    "start_time": time.time() - 2,
                })
                await pilot.pause()
                block = app._current_reasoning
                self.assertIsNotNone(block)
                self.assertFalse(block._finished)

                await app._handle_event({
                    "type": "tool_call_pending",
                    "tool": "shell",
                    "call_id": "c1",
                    "description": "正在执行 shell 命令",
                })
                await pilot.pause()

                self.assertIsNone(app._current_reasoning)
                self.assertTrue(block._finished)
                self.assertLess(block._duration, 5)

        try:
            asyncio.run(run())
        finally:
            get_session_manager().close_session(sid)


if __name__ == "__main__":
    unittest.main()
