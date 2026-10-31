# -*- coding: utf-8 -*-
import unittest
from unittest import mock

from app.channels import ChannelManager
from app.core.interface.base_cell import BaseCell
from app.core.util.component_tool_registry import ComponentToolRegistry


class _ProbeCell(BaseCell):
    cell_name = "probe_tool"
    required_channel = "qq"

    def _cmd_ping(self, text: str = "") -> dict:
        return {"pong": text}


class _PlainCell(BaseCell):
    cell_name = "plain_tool"

    def _cmd_ping(self) -> dict:
        return {"ok": True}


class _WeixinProbeCell(BaseCell):
    cell_name = "weixin_probe_tool"
    required_channel = "weixin"

    def _cmd_ping(self) -> dict:
        return {"ok": True}


class _FakeAdapter:

    def __init__(self, running=True):
        self._running = running


class _FakeChannelManager:

    def __init__(self, platforms=(), running=True):
        self._platforms = set(platforms)
        self._running = running

    def get_adapter(self, platform):
        return _FakeAdapter(self._running) if platform in self._platforms else None


class ComponentChannelGateTest(unittest.TestCase):

    def _tools(self, platforms, running=True, weixin=False):
        registry = ComponentToolRegistry()
        registry.register(_ProbeCell())
        registry.register(_PlainCell())
        if weixin:
            registry.register(_WeixinProbeCell())
        fake = _FakeChannelManager(platforms, running)
        with mock.patch.object(ChannelManager, "get_instance", return_value=fake):
            return registry.get_component_tools()

    def test_hidden_without_channel(self):
        tools = self._tools(())
        self.assertNotIn("probe_tool", tools)
        self.assertIn("plain_tool", tools)

    def test_shown_after_channel_registered(self):
        tools = self._tools({"qq"})
        self.assertIn("probe_tool", tools)
        self.assertIn("plain_tool", tools)

    def test_weixin_hidden_until_logged_in(self):
        tools = self._tools({"weixin"}, running=False, weixin=True)
        self.assertNotIn("weixin_probe_tool", tools)

    def test_weixin_shown_after_running(self):
        tools = self._tools({"weixin"}, running=True, weixin=True)
        self.assertIn("weixin_probe_tool", tools)

    def test_platform_components_declare_channel(self):
        from components.feishu_files import FeishuFiles
        from components.qq_files import QQFiles
        from components.telegram_files import TelegramFiles
        from components.weixin_files import WeixinFiles

        self.assertEqual(QQFiles.required_channel, "qq")
        self.assertEqual(TelegramFiles.required_channel, "telegram")
        self.assertEqual(FeishuFiles.required_channel, "feishu")
        self.assertEqual(WeixinFiles.required_channel, "weixin")


if __name__ == "__main__":
    unittest.main()
