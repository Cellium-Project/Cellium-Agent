# -*- coding: utf-8 -*-
"""TUI 分块渲染的空白行必须带基背景色，避免浅色主题露出终端默认黑底"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.geometry import Region

from app.tui.widgets import AssistantMessage

_PARA = "这是用于触发分块流式渲染的长文本，需要超过一千字符。" * 20


class _Probe(App):
    CSS = "Screen { background: #f5f5f5; } #chat { height: 1fr; }"

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat"):
            yield AssistantMessage("")


class TestBlockBlankBackground(unittest.TestCase):

    def test_block_separator_has_background(self):
        async def _run():
            app = _Probe()
            async with app.run_test(size=(60, 40)) as pilot:
                w = app.query_one(AssistantMessage)
                w.update(f"{_PARA}\n\n{_PARA}\n")
                await pilot.pause(0.3)
                self.assertTrue(w._blocks, "长文本应走分块渲染路径")
                width = app.query_one("#chat").size.width
                strips = w.render_lines(Region(0, 0, width, w.size.height))
                return [
                    s for s in strips
                    if not s.text.strip() and s.cell_length > 0
                ]

        blanks = asyncio.run(_run())
        self.assertTrue(blanks)
        for strip in blanks:
            for _, style, _ in strip:
                self.assertIsNotNone(style, "空白行 segment 不能没有样式")
                self.assertIsNotNone(style.bgcolor, "空白行必须有背景色")


class TestLogoPlanColor(unittest.TestCase):

    def test_green_becomes_dark_gold(self):
        from app.tui.widgets import LogoImage
        out = LogoImage._plan_color((36, 236, 170, 250))
        self.assertTrue(out[0] > out[1] > out[2], f"应为暗金色系: {out}")
        self.assertLess(max(out[:3]), 220, f"亮度应偏暗: {out}")
        self.assertEqual(out[3], 250)

    def test_blue_and_transparent_untouched(self):
        from app.tui.widgets import LogoImage
        self.assertEqual(LogoImage._plan_color((0, 161, 255, 200)), (0, 161, 255, 200))
        self.assertEqual(LogoImage._plan_color((0, 0, 0, 0)), (0, 0, 0, 0))

    def test_sweep_from_center_outward(self):
        from app.tui.widgets import LogoImage
        width = 18
        center = LogoImage._sweep_factor(0.3, width // 2, width)
        left = LogoImage._sweep_factor(0.3, 0, width)
        right = LogoImage._sweep_factor(0.3, width - 1, width)
        self.assertGreater(center, 0)
        self.assertLess(center, 1)
        self.assertEqual(left, right, "左右两侧对称")
        self.assertLess(left, center, "中心先完成过渡，边缘更晚")
        self.assertEqual(LogoImage._sweep_factor(0.0, width // 2, width), 0.0)
        self.assertEqual(LogoImage._sweep_factor(1.0, 0, width), 1.0)

    def test_blend_endpoints(self):
        from app.tui.widgets import LogoImage
        a, b = (10, 200, 100, 255), (120, 80, 220, 255)
        self.assertEqual(LogoImage._blend(a, b, 0.0), a)
        self.assertEqual(LogoImage._blend(a, b, 1.0), b)
        self.assertEqual(LogoImage._blend(a, b, 0.5)[3], 255)

    def test_rim_peaks_middle(self):
        from app.tui.widgets import LogoImage
        self.assertEqual(LogoImage._rim(0.0), 0.0)
        self.assertEqual(LogoImage._rim(1.0), 0.0)
        self.assertEqual(LogoImage._rim(0.5), 1.0)
        self.assertGreater(LogoImage._rim(0.2), 0.0)

    def test_brighten_caps_white(self):
        from app.tui.widgets import LogoImage
        out = LogoImage._brighten((100, 50, 20, 255), 0.5)
        self.assertEqual(out[3], 255)
        self.assertGreater(out[0], 100)
        self.assertEqual(LogoImage._brighten((255, 255, 255, 255), 0.5), (255, 255, 255, 255))


class TestCommandMatch(unittest.TestCase):

    def _slashes(self, q):
        from app.tui.commands import match_commands
        return [c["slash"] for c in match_commands(q)]

    def test_single_letter_only_prefix(self):
        self.assertEqual(self._slashes("r"), ["run"])
        self.assertEqual(self._slashes("p"), ["plan", "plan-habit"])
        self.assertEqual(self._slashes("t"), ["theme"])

    def test_alias_needs_full_prefix(self):
        self.assertEqual(self._slashes("rm"), ["delete"])
        self.assertEqual(self._slashes("run"), ["run"])
        self.assertEqual(self._slashes("plan"), ["plan"])

    def test_multi_prefix(self):
        self.assertEqual(self._slashes("se"), ["session", "settings"])
        self.assertEqual(self._slashes("s"), ["settings"])

    def test_plan_habit_command(self):
        self.assertEqual(self._slashes("plan-h"), ["plan-habit"])
        self.assertEqual(self._slashes("plan"), ["plan"])


class TestModeBadge(unittest.TestCase):

    def test_plan_badge_gold(self):
        from app.tui.app import mode_badge_markup
        self.assertIn("#b8860b", mode_badge_markup("plan"))
        self.assertIn("#8a6d1f", mode_badge_markup("plan", dark=False))

    def test_run_badge_green(self):
        from app.tui.app import mode_badge_markup
        self.assertIn("RUN", mode_badge_markup("run"))
        self.assertIn("#2ff0ab", mode_badge_markup("run"))
        self.assertIn("#0b8a63", mode_badge_markup("run", dark=False))


class TestSubagentCard(unittest.TestCase):

    def test_progress_and_done_render(self):
        from app.tui.app import CelliumTUI

        async def run():
            app = CelliumTUI(bootstrap=None)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                await app._update_subagent_card({
                    "name": "a", "status": "running", "current_tool": "read",
                    "tool_calls": 1, "elapsed_s": 2, "task": "调研",
                })
                await pilot.pause()
                self.assertIn("subagent:a", app._subagent_cards)
                await app._finish_subagent_card({
                    "name": "a", "status": "done", "success": True,
                    "duration_s": 1.2, "content": "结论",
                })
                await pilot.pause()
                self.assertNotIn("subagent:a", app._subagent_cards)

        asyncio.run(run())

    def test_three_parallel_cards_are_independent(self):
        from app.tui.app import CelliumTUI

        async def run():
            app = CelliumTUI(bootstrap=None)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                for name in ("a", "b", "c"):
                    await app._update_subagent_card({
                        "name": name, "status": "running",
                        "current_tool": "read", "tool_calls": 1, "elapsed_s": 1,
                    })
                await pilot.pause()
                self.assertEqual(sorted(app._subagent_cards.keys()),
                                 ["subagent:a", "subagent:b", "subagent:c"])
                cards = [app._subagent_cards[k] for k in ("subagent:a", "subagent:b", "subagent:c")]
                self.assertEqual(len({id(c) for c in cards}), 3, "每个子 Agent 应独立一张卡")
                await app._finish_subagent_card({
                    "name": "b", "status": "done", "success": True,
                    "duration_s": 1.0, "content": "B 完成",
                })
                await pilot.pause()
                self.assertNotIn("subagent:b", app._subagent_cards)
                self.assertIn("subagent:a", app._subagent_cards)
                self.assertIn("subagent:c", app._subagent_cards)

        asyncio.run(run())


    def test_label_shows_tools_and_elapsed_when_done(self):
        from app.tui.app import CelliumTUI
        label = CelliumTUI._subagent_label({
            "name": "x", "status": "done", "tools_used": ["read", "grep"],
            "tool_calls": 3, "duration_s": 18, "error": None,
        })
        self.assertIn("工具 read, grep", label)
        self.assertIn("第 3 次", label)
        self.assertIn("18s", label)

    def test_sync_fallback_description_is_rich(self):
        import time
        import components.sub_agent as sa
        from app.tui.app import CelliumTUI

        async def run():
            app = CelliumTUI(bootstrap=None)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                await app._update_subagent_card({"name": "s3", "status": "running"})
                card = app._subagent_cards["subagent:s3"]
                key = sa._sub_key(app.session_id, "s3")
                now = time.time()
                sa._SUBAGENTS[key] = {
                    "name": "s3", "status": "done", "result_content": "ok",
                    "last_tool": "grep", "tool_calls": 2, "tools_used": ["read", "grep"],
                    "started_at": now - 5, "finished_at": now - 2,
                }
                try:
                    app._sync_subagent_cards()
                finally:
                    sa._SUBAGENTS.pop(key, None)
                await pilot.pause()
                self.assertNotIn("subagent:s3", app._subagent_cards)
                self.assertIn("工具 grep", card._calls["subagent:s3"]["description"])
                self.assertIn("第 2 次", card._calls["subagent:s3"]["description"])

        asyncio.run(run())

    def test_sync_finishes_stale_running_cards(self):
        import components.sub_agent as sa
        from app.tui.app import CelliumTUI

        async def run():
            app = CelliumTUI(bootstrap=None)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                names = ("s1", "s2")
                for name in names:
                    await app._update_subagent_card({
                        "name": name, "status": "running", "current_tool": "read",
                    })
                keys = [sa._sub_key(app.session_id, n) for n in names]
                for name in names:
                    sa._SUBAGENTS[sa._sub_key(app.session_id, name)] = {
                        "name": name, "status": "done", "result_content": "",
                    }
                try:
                    app._sync_subagent_cards()
                finally:
                    for key in keys:
                        sa._SUBAGENTS.pop(key, None)
                await pilot.pause()
                self.assertEqual(app._subagent_cards, {}, "注册表已结束的卡片应兜底收尾")

        asyncio.run(run())


class TestLogoAnimation(unittest.TestCase):

    def test_animation_completes_to_plan(self):
        from textual.app import App, ComposeResult
        from app.tui.widgets import LogoImage

        class _A(App):
            def compose(self) -> ComposeResult:
                yield LogoImage()

        async def _run():
            app = _A()
            async with app.run_test(size=(40, 20)) as pilot:
                logo = app.query_one(LogoImage)
                logo.set_plan(True)
                immediate = logo._mix
                await pilot.pause(0.1)
                mid = logo._mix
                await pilot.pause(0.8)
                return immediate, mid, logo._mix, logo._anim_timer

        immediate, mid, final, timer = asyncio.run(_run())
        self.assertGreater(immediate, 0.0, "按键后应立刻有一帧反馈")
        self.assertGreater(mid, immediate)
        self.assertLess(mid, 1.0, "切换应为渐变而非瞬变")
        self.assertEqual(final, 1.0)
        self.assertIsNone(timer, "动画结束必须停表")


class TestTuiKeySafety(unittest.TestCase):

    def test_exit_on_ctrl_d(self):
        from app.tui.app import CelliumTUI
        bindings = {b.key: b for b in CelliumTUI.BINDINGS}
        self.assertEqual(bindings["ctrl+d"].action, "stop_or_exit")
        self.assertTrue(bindings["ctrl+d"].priority, "需 priority 才能压过输入框的 delete_right")
        self.assertEqual(bindings["ctrl+q"].action, "stop_or_exit")

    def test_no_delete_session_binding(self):
        from app.tui.app import CelliumTUI
        self.assertNotIn("delete_session", {b.action for b in CelliumTUI.BINDINGS})

    def test_picker_delete_key_removed(self):
        from app.tui.session_picker import SessionPickerScreen
        self.assertFalse(hasattr(SessionPickerScreen, "key_delete"))

    def test_delete_confirm_screen(self):
        from app.tui.session_picker import DeleteConfirmScreen
        keys = {b[0] for b in DeleteConfirmScreen.BINDINGS}
        self.assertIn("y", keys)
        self.assertIn("n", keys)
        self.assertIn("escape", keys)

    def test_i18n_no_delete_hint(self):
        from app.tui.i18n import STRINGS
        for lang in STRINGS:
            self.assertNotIn("Delete", STRINGS[lang]["session.picker_footer"])
            self.assertIn("session.delete_confirm", STRINGS[lang])
            self.assertIn("common.copied_selection", STRINGS[lang])
            self.assertIn("Ctrl+D", STRINGS[lang]["hint"])
            self.assertNotIn("Ctrl+Q", STRINGS[lang]["hint"])

    def test_priority_beats_input_delete_right(self):
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.widgets import Input

        hits = []

        class _A(App):
            BINDINGS = [Binding("ctrl+d", "mark", "mark", priority=True)]

            def compose(self) -> ComposeResult:
                yield Input("abc")

            def action_mark(self):
                hits.append(True)

        async def _run():
            app = _A()
            async with app.run_test() as pilot:
                inp = app.query_one(Input)
                inp.focus()
                inp.cursor_position = 1
                await pilot.press("ctrl+d")
                await pilot.pause()
                return inp.value, len(hits)

        value, count = asyncio.run(_run())
        self.assertEqual(count, 1, "priority 绑定应触发 App 动作")
        self.assertEqual(value, "abc", "输入框不应再吃掉 ctrl+d")

    def test_confirm_screen_yes_and_cancel(self):
        from textual.app import App
        from app.tui.session_picker import DeleteConfirmScreen

        async def _run(key):
            result = []
            app = App()
            async with app.run_test() as pilot:
                app.push_screen(DeleteConfirmScreen("确认删除？", "删除", "取消"), callback=result.append)
                await pilot.pause()
                await pilot.press(key)
                await pilot.pause()
            return result

        self.assertEqual(asyncio.run(_run("y")), [True])
        self.assertEqual(asyncio.run(_run("escape")), [False])


class TestRightClickCopy(unittest.TestCase):

    class _Screen:
        def __init__(self, text):
            self.text = text
            self.cleared = False

        def get_selected_text(self):
            return self.text

        def clear_selection(self):
            self.cleared = True

    class _App:
        def __init__(self, text):
            self.screen = TestRightClickCopy._Screen(text)
            self.copied = None
            self.notes = []

        def copy_to_clipboard(self, text):
            self.copied = text

        def tr(self, key, *args):
            return key

        def notify(self, message, **kwargs):
            self.notes.append((message, kwargs))

    class _Event:
        def __init__(self, button):
            self.button = button
            self.stopped = False

        def stop(self):
            self.stopped = True

    def test_copy_selection(self):
        from app.tui.app import copy_selection_if_any
        app = self._App("hello")
        self.assertTrue(copy_selection_if_any(app))
        self.assertEqual(app.copied, "hello")
        self.assertTrue(app.screen.cleared)
        self.assertEqual(app.notes[0][0], "common.copied_selection")

    def test_empty_selection(self):
        from app.tui.app import copy_selection_if_any
        app = self._App("")
        self.assertFalse(copy_selection_if_any(app))
        self.assertIsNone(app.copied)
        self.assertEqual(app.notes, [])

    def test_right_click_copies(self):
        from app.tui.app import CelliumTUI
        app = self._App("hello")
        event = self._Event(3)
        CelliumTUI.on_mouse_down(app, event)
        self.assertEqual(app.copied, "hello")
        self.assertTrue(event.stopped)

    def test_left_click_no_copy(self):
        from app.tui.app import CelliumTUI
        app = self._App("hello")
        event = self._Event(1)
        CelliumTUI.on_mouse_down(app, event)
        self.assertIsNone(app.copied)
        self.assertFalse(event.stopped)


class TestSelectionReflow(unittest.TestCase):

    @staticmethod
    def _strip(text, bg="#121212"):
        from rich.segment import Segment
        from rich.style import Style
        from textual.strip import Strip
        return Strip([Segment(text, Style(bgcolor=bg))], len(text))

    def test_code_wrap_joins_without_padding(self):
        from textual.selection import Selection
        from app.tui.widgets import _reflow_selection
        strips = [self._strip(" abcde", "#272822"), self._strip(" fghij", "#272822")]
        text = _reflow_selection(strips, Selection(None, None), 7, "#121212")
        self.assertEqual(text, "abcdefghij")

    def test_hard_newline_kept(self):
        from textual.selection import Selection
        from app.tui.widgets import _reflow_selection
        strips = [self._strip("abc"), self._strip("def")]
        text = _reflow_selection(strips, Selection(None, None), 20, "#121212")
        self.assertEqual(text, "abc\ndef")

    def test_partial_last_row_not_joined(self):
        from textual.selection import Selection
        from app.tui.widgets import _reflow_selection
        strips = [
            self._strip(" abcdefgh", "#272822"),
            self._strip(" abcdefgh", "#272822"),
            self._strip(" ijk", "#272822"),
        ]
        text = _reflow_selection(strips, Selection(None, None), 10, "#121212")
        self.assertEqual(text, "abcdefghabcdefghijk")

    def test_prose_wrap_restores_space(self):
        from textual.selection import Selection
        from app.tui.widgets import _reflow_selection
        strips = [self._strip("a" * 20), self._strip("b" * 10)]
        text = _reflow_selection(strips, Selection(None, None), 20, "#121212")
        self.assertEqual(text, "a" * 20 + " " + "b" * 10)

    def test_out_of_range_selection_returns_empty(self):
        from textual.geometry import Offset
        from textual.selection import Selection
        from app.tui.widgets import _reflow_selection
        sel = Selection(Offset(5, 5), Offset(6, 6))
        self.assertEqual(_reflow_selection([self._strip("abc")], sel, 20, "#121212"), "")

    def test_widget_copy_keeps_code_line(self):
        from textual.app import App, ComposeResult
        from textual.selection import Selection
        from app.tui.widgets import HistoryMarkdown

        code = "print('" + "x" * 60 + "')"
        md = "```python\n" + code + "\n```"

        class _A(App):
            CSS = "HistoryMarkdown { width: 20; height: auto; }"

            def compose(self) -> ComposeResult:
                yield HistoryMarkdown(md)

        async def _run():
            app = _A()
            async with app.run_test(size=(30, 30)) as pilot:
                await pilot.pause()
                widget = app.query_one(HistoryMarkdown)
                text, _ = widget.get_selection(Selection(None, None))
                return text

        text = asyncio.run(_run())
        self.assertIn(code, text, "复制应保持代码原始长度，不被折行打断")

    def test_copy_ignores_selection_styling(self):
        from unittest.mock import PropertyMock, patch
        from textual.app import App, ComposeResult
        from textual.selection import Selection
        from textual.widget import Widget
        from app.tui.widgets import HistoryMarkdown

        code = "print('" + "x" * 60 + "')"
        md = "```python\n" + code + "\n```"

        class _A(App):
            CSS = "HistoryMarkdown { width: 20; height: auto; }"

            def compose(self) -> ComposeResult:
                yield HistoryMarkdown(md)

        async def _run():
            app = _A()
            async with app.run_test(size=(30, 30)) as pilot:
                await pilot.pause()
                widget = app.query_one(HistoryMarkdown)
                with patch.object(Widget, "text_selection", new_callable=PropertyMock) as mocked:
                    mocked.return_value = Selection(None, None)
                    text, _ = widget.get_selection(Selection(None, None))
                return text

        text = asyncio.run(_run())
        self.assertIn(code, text, "选中高亮不应干扰复制内容")


class TestSourceMatch(unittest.TestCase):

    def test_code_block_restores_original(self):
        from app.tui.widgets import _source_code_selection
        src = "```python\n    x = 1  \n    y = 2\n```"
        text = "    x = 1\n    y = 2"
        self.assertEqual(_source_code_selection(src, text), "    x = 1  \n    y = 2")

    def test_partial_line(self):
        from app.tui.widgets import _source_code_selection
        src = "```python\n    x = 1  \n    y = 2\n```"
        self.assertEqual(_source_code_selection(src, "x = 1"), "x = 1")

    def test_no_block_returns_none(self):
        from app.tui.widgets import _source_code_selection
        self.assertIsNone(_source_code_selection("普通文本", "普通"))

    def test_mismatch_returns_none(self):
        from app.tui.widgets import _source_code_selection
        self.assertIsNone(_source_code_selection("```\nabc\n```", "xyz"))

    def test_widget_copy_from_source(self):
        from textual.app import App, ComposeResult
        from textual.selection import Selection
        from app.tui.widgets import HistoryMarkdown

        md = "```python\nfoo  \nbar\n```"

        class _A(App):
            CSS = "HistoryMarkdown { width: 20; height: auto; }"

            def compose(self) -> ComposeResult:
                yield HistoryMarkdown(md)

        async def _run():
            app = _A()
            async with app.run_test(size=(30, 30)) as pilot:
                await pilot.pause()
                widget = app.query_one(HistoryMarkdown)
                text, _ = widget.get_selection(Selection(None, None))
                return text

        text = asyncio.run(_run())
        self.assertIn("foo  ", text, "应以源码为准，保留行尾空格")
        self.assertIn("bar", text)


if __name__ == "__main__":
    unittest.main()
