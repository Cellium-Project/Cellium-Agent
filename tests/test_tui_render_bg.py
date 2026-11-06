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

    def test_green_becomes_violet(self):
        from app.tui.widgets import LogoImage
        out = LogoImage._plan_color((36, 236, 170, 250))
        self.assertTrue(out[2] > out[0] > out[1], f"应为紫罗兰色系: {out}")
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
        self.assertEqual(self._slashes("p"), ["plan"])
        self.assertEqual(self._slashes("t"), ["theme"])

    def test_alias_needs_full_prefix(self):
        self.assertEqual(self._slashes("rm"), ["delete"])
        self.assertEqual(self._slashes("run"), ["run"])
        self.assertEqual(self._slashes("plan"), ["plan"])

    def test_multi_prefix(self):
        self.assertEqual(self._slashes("se"), ["session", "settings"])
        self.assertEqual(self._slashes("s"), ["settings"])


class TestModeBadge(unittest.TestCase):

    def test_plan_badge_violet(self):
        from app.tui.app import mode_badge_markup
        self.assertIn("#a78bfa", mode_badge_markup("plan"))
        self.assertIn("#7c5cff", mode_badge_markup("plan", dark=False))

    def test_run_badge_green(self):
        from app.tui.app import mode_badge_markup
        self.assertIn("RUN", mode_badge_markup("run"))
        self.assertIn("#2ff0ab", mode_badge_markup("run"))
        self.assertIn("#0b8a63", mode_badge_markup("run", dark=False))


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


if __name__ == "__main__":
    unittest.main()
