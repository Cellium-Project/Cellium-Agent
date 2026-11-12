# -*- coding: utf-8 -*-
"""渠道进度消息：每个用户回合只推首条执行计划，Thinking 正常输出"""
import asyncio
from unittest.mock import patch

from app.channels.channel_manager import ChannelManager
from app.channels.base import UnifiedMessage


class _FakeAdapter:
    platform_name = "telegram"
    minimal_output = False
    max_text_chunk = 1000

    def __init__(self):
        self.sent = []

    async def send_message(self, target_id, content, message_type, **kwargs):
        self.sent.append(content)
        return True


class _FakeTaskManager:
    def get_task_info(self, session_id):
        return None

    def cleanup_task(self, session_id):
        return None


class _FakeBroker:
    def unsubscribe(self, session_id, queue):
        return None


def _run(events):
    mgr = ChannelManager()
    adapter = _FakeAdapter()
    mgr._adapters["telegram"] = adapter

    msg = UnifiedMessage(
        platform="telegram",
        user_id="u1",
        content="hi",
        message_type="c2c",
    )
    queue = asyncio.Queue()
    for ev in events:
        queue.put_nowait(ev)
    queue.put_nowait(None)

    with patch.object(ChannelManager, "_should_show_thinking_plan", return_value=True), \
         patch.object(ChannelManager, "_update_session_message_count", return_value=None), \
         patch("app.channels.channel_manager.get_message_broker", return_value=_FakeBroker()), \
         patch("app.server.task_manager.get_task_manager", return_value=_FakeTaskManager()):
        asyncio.run(mgr._consume_channel_task_queue(msg, "telegram:u1", queue))
    return adapter.sent


def test_thinking_kept_but_plan_sent_once():
    sent = _run([
        {"type": "thinking_streaming", "content": "先分析需求", "final": True},
        {"type": "thinking_streaming", "content": "再查看文件", "final": True},
        {"type": "hybrid_phase", "phase": "execute", "message": "执行中", "description": "执行计划 (0/2)"},
        {"type": "hybrid_phase", "phase": "replan", "message": "重新规划", "description": "第 1 次调整计划"},
        {"type": "content", "content": "完成了"},
        {"type": "done", "content": "完成了"},
    ])

    assert any("先分析需求" in m for m in sent)
    assert any("再查看文件" in m for m in sent)
    assert sum("执行计划" in m for m in sent) == 1
    assert all("重新规划" not in m for m in sent)
    assert sum("完成了" in m for m in sent) == 1
