# -*- coding: utf-8 -*-
"""TUI 启动优化（A 延迟 WebUI / B 分批挂载）的边界测试"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDeferWebApp(unittest.TestCase):
    """A：defer_web_app 只跳过 FastAPI app 创建，其余初始化照常"""

    def _run(self, enable_webapp, defer):
        from app.core.bootstrap import bootstrap_agent
        with patch("app.core.bootstrap._setup_logging"), \
             patch("app.core.bootstrap._setup_containers", return_value=MagicMock()), \
             patch("app.core.bootstrap._setup_session_manager"), \
             patch("app.core.bootstrap._setup_components"), \
             patch("app.core.bootstrap._setup_watcher"), \
             patch("app.core.bootstrap._setup_config_watcher"), \
             patch("app.core.bootstrap._setup_web_app", return_value=MagicMock()) as web, \
             patch("app.core.bootstrap._print_event_system_info"), \
             patch("app.core.bootstrap._ensure_available_port", return_value=18000), \
             patch("app.core.util.fork_guard.install"):
            r = bootstrap_agent(enable_webapp=enable_webapp, defer_web_app=defer,
                                auto_open_browser=False)
            return r, web

    def test_defer_skips_web_app_creation(self):
        r, web = self._run(True, True)
        web.assert_not_called()
        self.assertIsNone(r.fastapi_app)
        self.assertEqual(r.port, 18000)

    def test_normal_mode_creates_web_app(self):
        r, web = self._run(True, False)
        web.assert_called_once()
        self.assertIsNotNone(r.fastapi_app)

    def test_webapp_disabled_never_creates(self):
        r, web = self._run(False, False)
        web.assert_not_called()
        self.assertIsNone(r.fastapi_app)
        _, web2 = self._run(False, True)
        web2.assert_not_called()


class TestBatchMount(unittest.TestCase):
    """B：分批挂载后 widget 总数与一次性挂载一致"""

    def test_batched_mount_all_children(self):
        from textual.app import App, ComposeResult
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        class T(App):
            def compose(self) -> ComposeResult:
                yield VerticalScroll(id="chat")

        async def _run():
            app = T()
            async with app.run_test(size=(100, 30)):
                chat = app.query_one("#chat", VerticalScroll)
                widgets = [Static(f"w{i}") for i in range(80)]
                for i in range(0, len(widgets), 20):
                    await chat.mount(*widgets[i:i + 20])
                    await asyncio.sleep(0)
                return len(list(chat.children))

        self.assertEqual(asyncio.run(_run()), 80)

    def test_partial_batch_on_interrupt(self):
        """中途中断时只有已挂的批存在（新会话加载会整表重建，不会残留）"""
        from textual.app import App, ComposeResult
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        class T(App):
            def compose(self) -> ComposeResult:
                yield VerticalScroll(id="chat")

        async def _run():
            app = T()
            async with app.run_test(size=(100, 30)):
                chat = app.query_one("#chat", VerticalScroll)
                widgets = [Static(f"w{i}") for i in range(80)]
                for idx, i in enumerate(range(0, len(widgets), 20)):
                    await chat.mount(*widgets[i:i + 20])
                    await asyncio.sleep(0)
                    if idx == 1:
                        break
                return len(list(chat.children))

        self.assertEqual(asyncio.run(_run()), 40)


class TestRunnerWiring(unittest.TestCase):
    """runner 只在 enable_webapp 时启动后台补建"""

    def test_deferred_thread_only_when_webapp_enabled(self):
        import app.tui.runner as runner

        class FakeApp:
            def __init__(self):
                self.bootstrap = None
                self._bootstrap_error = None
                self._bootstrap_ready = MagicMock()

            def call_from_thread(self, fn, *a):
                pass

            def _refresh_status(self):
                pass

        fake_result = MagicMock()
        fake_result.fastapi_app = None

        with patch("app.core.bootstrap.bootstrap_agent", return_value=fake_result), \
             patch("app.tui.runner.threading.Thread") as th, \
             patch("app.tui.runner._silence_console_logging"), \
             patch("app.tui.runner._start_web_server"):
            fake_app = FakeApp()
            runner._bootstrap_in_thread(fake_app, None, None, enable_webapp=False)
            web_threads = [c for c in th.call_args_list
                           if c.kwargs.get("name") == "webui-deferred-init"]
            self.assertEqual(len(web_threads), 0)

            th.reset_mock()
            runner._bootstrap_in_thread(fake_app, None, None, enable_webapp=True)
            web_threads = [c for c in th.call_args_list
                           if c.kwargs.get("name") == "webui-deferred-init"]
            self.assertEqual(len(web_threads), 1)


if __name__ == "__main__":
    unittest.main()
