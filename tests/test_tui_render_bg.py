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


if __name__ == "__main__":
    unittest.main()
