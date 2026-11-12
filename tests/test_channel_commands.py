# -*- coding: utf-8 -*-
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.channels.base import UnifiedMessage
from app.channels.channel_manager import ChannelManager
from app.server import session_mode


@pytest.fixture
def manager(monkeypatch):
    cm = ChannelManager()
    sent = []

    async def fake_send(platform, target_id, content, message_type, **kwargs):
        sent.append({
            "platform": platform,
            "target": target_id,
            "content": content,
            "type": message_type,
            "kwargs": kwargs,
        })

    monkeypatch.setattr(cm, "send_message", fake_send)
    cm.sent = sent
    return cm


def _msg(content, message_type="c2c", group_id=None):
    return UnifiedMessage(
        platform="telegram",
        user_id="u1",
        content=content,
        message_type=message_type,
        group_id=group_id,
    )


def test_plan_sets_session_mode(manager):
    msg = _msg("/plan")
    assert asyncio.run(manager._handle_mode_command(msg)) is True
    assert session_mode.get_mode(msg.session_id) == "plan"
    assert "Plan" in manager.sent[-1]["content"]


def test_run_and_delegate_set_mode(manager):
    msg = _msg("/delegate")
    assert asyncio.run(manager._handle_mode_command(msg)) is True
    assert session_mode.get_mode(msg.session_id) == "delegate"
    assert "编排" in manager.sent[-1]["content"]

    assert asyncio.run(manager._handle_mode_command(_msg("/run"))) is True
    assert session_mode.get_mode(msg.session_id) == "run"
    assert "Run" in manager.sent[-1]["content"]


def test_mention_suffix_and_case(manager):
    msg = _msg("/PLAN@cellium_bot")
    assert asyncio.run(manager._handle_mode_command(msg)) is True
    assert session_mode.get_mode(msg.session_id) == "plan"


def test_help_lists_all_commands(manager):
    msg = _msg("/help")
    assert asyncio.run(manager._handle_mode_command(msg)) is True
    text = manager.sent[-1]["content"]
    for cmd in ("/plan", "/run", "/delegate", "/stop", "/help"):
        assert cmd in text


def test_group_session_mode_isolated(manager):
    a = _msg("/plan", message_type="group", group_id="g1")
    b = _msg("hi", message_type="group", group_id="g2")
    assert asyncio.run(manager._handle_mode_command(a)) is True
    assert session_mode.get_mode(a.session_id) == "plan"
    assert session_mode.get_mode(b.session_id) == "run"


def test_normal_message_not_handled(manager):
    assert asyncio.run(manager._handle_mode_command(_msg("你好"))) is False
    assert asyncio.run(manager._handle_mode_command(_msg("/unknown"))) is False
    assert manager.sent == []


def test_channel_task_uses_session_mode(monkeypatch):
    import app.channels.channel_manager as cm_mod

    captured = {}

    class FakeTaskMgr:
        async def start_task(self, **kwargs):
            captured.update(kwargs)
            return True

    class FakeLoop:
        class _PB:
            def inject(self, *args, **kwargs):
                pass

        _prompt_builder = _PB()

    class FakeAGM:
        async def get_loop(self, session_id):
            return FakeLoop()

    class FakeBroker:
        def subscribe(self, session_id):
            return None

    monkeypatch.setattr("app.server.task_manager.get_task_manager", lambda: FakeTaskMgr())
    monkeypatch.setattr(cm_mod, "get_message_broker", lambda: FakeBroker())

    cm = ChannelManager()
    cm._agent_loop_manager = FakeAGM()
    cm._ensure_channel_task_consumer = lambda *args, **kwargs: None

    msg = _msg("做个计划")
    session_mode.set_mode(msg.session_id, "plan", broadcast=False)
    asyncio.run(cm._start_channel_task(msg, msg.session_id, "做个计划", object()))

    assert captured["mode"] == "plan"
    assert captured["session_id"] == msg.session_id
