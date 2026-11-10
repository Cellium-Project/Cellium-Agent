# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.ports import PORTS
from app.core.util import session_status


class TestSessionStatus(unittest.TestCase):

    def test_task_running(self):
        fake = SimpleNamespace(has_running_task=lambda sid: sid == "s_run")
        with patch.object(PORTS, "task", fake):
            self.assertTrue(session_status.is_session_running("s_run"))
            self.assertFalse(session_status.is_session_running("s_idle"))

    def test_delegated_counts_as_running(self):
        fake = SimpleNamespace(has_running_task=lambda sid: False)
        delegated = SimpleNamespace(is_delegated=lambda sid: True)
        with patch.object(PORTS, "task", fake), patch.object(PORTS, "delegated", delegated):
            self.assertTrue(session_status.is_session_running("s_dlg"))

    def test_errors_are_not_running(self):
        class _Boom:
            def has_running_task(self, sid):
                raise RuntimeError("no task manager")

            def is_delegated(self, sid):
                raise RuntimeError("no delegated")

        with patch.object(PORTS, "task", _Boom()), patch.object(PORTS, "delegated", _Boom()):
            self.assertFalse(session_status.is_session_running("s_x"))

    def test_empty_session(self):
        self.assertFalse(session_status.is_session_running(""))


class TestSessionListApi(unittest.TestCase):

    def test_list_all_sessions_marks_running(self):
        from app.server.routes import chat

        metas = [
            SimpleNamespace(
                session_id="a", created_at="t", last_active="t", message_count=0, title=None,
                to_dict=lambda s="a": {
                    "session_id": s, "created_at": "t", "last_active": "t",
                    "message_count": 0, "title": None,
                },
            ),
            SimpleNamespace(
                session_id="b", created_at="t", last_active="t", message_count=0, title="会话B",
                to_dict=lambda s="b": {
                    "session_id": s, "created_at": "t", "last_active": "t",
                    "message_count": 0, "title": "会话B",
                },
            ),
        ]
        store = SimpleNamespace(list_sessions=lambda: metas)
        with patch.object(chat, "get_session_store", return_value=store):
            with patch("app.core.util.session_status.is_session_running",
                       side_effect=lambda sid: sid == "b"):
                data = asyncio.run(chat.list_all_sessions())
        self.assertEqual(data["total"], 2)
        self.assertFalse(data["sessions"][0]["running"])
        self.assertTrue(data["sessions"][1]["running"])


class TestClipCells(unittest.TestCase):

    def test_short_text_untouched(self):
        from app.tui.widgets import clip_cells
        self.assertEqual(clip_cells("abc", 10), "abc")

    def test_cjk_truncated_with_ellipsis(self):
        from app.tui.widgets import clip_cells
        from rich.cells import cell_len
        out = clip_cells("这是一个很长的会话名称测试", 8)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(cell_len(out), 8)

    def test_zero_width(self):
        from app.tui.widgets import clip_cells
        self.assertEqual(clip_cells("abc", 0), "")


class TestTuiDelegationBar(unittest.TestCase):

    def test_bar_shows_running_reasoning_and_done(self):
        from app.tui.app import CelliumTUI

        async def run():
            app = CelliumTUI(bootstrap=None, session_id="tui_delegation_bar")
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                app._update_delegation_bar({
                    "task_id": "t1", "target": "B", "target_title": "构建会话",
                    "status": "running", "current_tool": "edit", "tool_calls": 2,
                    "elapsed_s": 5, "last_reasoning": "先看一下现有目录结构",
                })
                await pilot.pause()
                bar = app.delegation_bar
                self.assertTrue(bar.has_class("visible"))
                text = app._delegation_text
                self.assertIn("运行中", text)
                self.assertIn("Reasoning", text)
                self.assertIn("edit", text)

                app._update_delegation_bar({
                    "task_id": "t1", "target": "B", "target_title": "构建会话",
                    "status": "done", "success": True, "elapsed_s": 9,
                })
                await pilot.pause()
                self.assertIn("完成", app._delegation_text)

                app._remove_delegation("t1")
                await pilot.pause()
                self.assertFalse(bar.has_class("visible"))

        try:
            asyncio.run(run())
        finally:
            from app.agent.loop.session_manager import get_session_manager
            get_session_manager().close_session("tui_delegation_bar")

    def test_bar_folds_more_than_three(self):
        from app.tui.app import CelliumTUI

        async def run():
            app = CelliumTUI(bootstrap=None, session_id="tui_delegation_more")
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                for i in range(5):
                    app._update_delegation_bar({
                        "task_id": f"t{i}", "target": f"S{i}",
                        "target_title": f"会话{i}", "status": "running",
                        "current_tool": "edit", "tool_calls": 1, "elapsed_s": 3,
                    })
                await pilot.pause()
                text = app._delegation_text
                self.assertIn("还有 2 个", text)
                # 折叠行不能被 max-height 裁掉：每条一行，控件高度要够
                lines = text.splitlines()
                self.assertLessEqual(len(lines), 5)
                bar = app.delegation_bar
                await pilot.pause()
                self.assertGreaterEqual(bar.size.height, len(lines))

        try:
            asyncio.run(run())
        finally:
            from app.agent.loop.session_manager import get_session_manager
            get_session_manager().close_session("tui_delegation_more")


class TestTuiSessionDot(unittest.TestCase):

    def test_sidebar_prompt_blinks_for_running(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_store import get_session_store
        from app.agent.loop.session_manager import get_session_manager

        sid_a = f"tui_dot_a_{int(time.time())}"
        sid_b = f"tui_dot_b_{int(time.time())}"
        store = get_session_store()
        store.get_or_create_session(sid_a)
        store.get_or_create_session(sid_b)

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid_a)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                with patch("app.core.util.session_status.is_session_running",
                           side_effect=lambda sid: sid == sid_b):
                    await app._load_sessions()
                    await pilot.pause()
                    idx = app.session_list.get_option_index(sid_b)
                    prompt = str(app.session_list.get_option_at_index(idx).prompt)
                    self.assertIn("◐", prompt)
                    idx_a = app.session_list.get_option_index(sid_a)
                    self.assertIn("●", str(app.session_list.get_option_at_index(idx_a).prompt))

                    with patch.object(app, "_global_blink_counter", app._global_blink_counter + 1):
                        app._refresh_session_running()
                    prompt = str(app.session_list.get_option_at_index(idx).prompt)
                    self.assertIn("◓", prompt)

                app._refresh_session_running()
                idx = app.session_list.get_option_index(sid_b)
                self.assertIn("○", str(app.session_list.get_option_at_index(idx).prompt))

        try:
            asyncio.run(run())
        finally:
            get_session_manager().close_session(sid_a)
            get_session_manager().close_session(sid_b)
            store.delete_session(sid_a)
            store.delete_session(sid_b)

    def test_sidebar_long_title_ellipsis(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_store import get_session_store
        from app.agent.loop.session_manager import get_session_manager

        sid = f"tui_long_{int(time.time())}"
        long_title = "这是一个非常非常长的会话名称\n用于测试省略号显示效果"
        store = get_session_store()
        store.get_or_create_session(sid)
        store.set_session_title(sid, long_title)
        store.update_message_count(sid, delta=123)

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                with patch("app.core.util.session_status.is_session_running",
                           side_effect=lambda _sid: False):
                    await app._load_sessions()
                    await pilot.pause()
                    app._reload_session_prompts()
                    await pilot.pause()
                    idx = app.session_list.get_option_index(sid)
                    prompt = str(app.session_list.get_option_at_index(idx).prompt)
                    self.assertIn("…", prompt)
                    self.assertNotIn(long_title, prompt)
                    # 换行必须被压成空格，且渲染只有一行
                    self.assertNotIn("\n", prompt)
                    style = app.session_list.get_visual_style("option-list--option")
                    strips = app.session_list._get_option_render(
                        app.session_list.get_option_at_index(idx), style
                    )
                    self.assertEqual(len(strips), 1, f"会话名换行了: {prompt!r}")
                    # 总显示宽度（含前缀点和计数）不能超过列表内容宽度
                    from textual.content import Content
                    plain = Content.from_markup(prompt).plain
                    from rich.cells import cell_len
                    width = app.session_list.content_size.width
                    self.assertGreater(width, 0)
                    self.assertLessEqual(cell_len(plain), width, f"prompt 超宽会换行: {plain!r}")

        try:
            asyncio.run(run())
        finally:
            get_session_manager().close_session(sid)
            store.delete_session(sid)

    def test_sidebar_bracket_title_no_markup_crash(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_store import get_session_store
        from app.agent.loop.session_manager import get_session_manager
        from textual.content import Content

        sid = f"tui_bracket_{int(time.time())}"
        store = get_session_store()
        store.get_or_create_session(sid)
        store.set_session_title(sid, "带方括号[提示]与[/异常 的会话名称")
        store.update_message_count(sid, delta=42)

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                with patch("app.core.util.session_status.is_session_running",
                           side_effect=lambda _sid: False):
                    await app._load_sessions()
                    await pilot.pause()
                    app._reload_session_prompts()
                    await pilot.pause()
                idx = app.session_list.get_option_index(sid)
                prompt = str(app.session_list.get_option_at_index(idx).prompt)
                # Textual 解析不应抛 MarkupError
                self.assertIn("[", Content.from_markup(prompt).plain)
                self.assertNotIn("\n", prompt)

        try:
            asyncio.run(run())
        finally:
            get_session_manager().close_session(sid)
            store.delete_session(sid)

    def test_switch_session_refreshes_model(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_store import get_session_store
        from app.agent.loop.session_manager import get_session_manager

        sid_a = f"tui_model_a_{int(time.time())}"
        sid_b = f"tui_model_b_{int(time.time())}"
        store = get_session_store()
        store.get_or_create_session(sid_a)
        store.get_or_create_session(sid_b)

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid_a)
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                app.model_name = "全局模型"
                with patch("app.server.session_llm.get_model",
                           side_effect=lambda sid: "独立模型" if sid == sid_b else ""):
                    await app._switch_session(sid_b)
                    await pilot.pause()
                self.assertEqual(app.model_name, "独立模型")

        try:
            asyncio.run(run())
        finally:
            get_session_manager().close_session(sid_a)
            get_session_manager().close_session(sid_b)
            store.delete_session(sid_a)
            store.delete_session(sid_b)

    def test_mode_toggle_cycles_three_states(self):
        from app.tui.app import CelliumTUI
        from app.agent.loop.session_manager import get_session_manager
        from app.server.session_mode import set_mode

        sid = f"tui_mode_{int(time.time())}"
        set_mode(sid, "run", broadcast=False)

        async def run():
            app = CelliumTUI(bootstrap=None, session_id=sid)
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                app._run_mode = "run"
                modes = []
                for _ in range(3):
                    app.action_toggle_run_mode()
                    await pilot.pause()
                    modes.append(app._run_mode)
                self.assertEqual(modes, ["plan", "delegate", "run"])

        try:
            asyncio.run(run())
        finally:
            get_session_manager().close_session(sid)


if __name__ == "__main__":
    unittest.main()
