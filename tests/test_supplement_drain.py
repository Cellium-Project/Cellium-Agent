# -*- coding: utf-8 -*-
import asyncio
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")

from app.server.task_manager import BackgroundTaskManager, TaskStatus


def _fake_info(status=TaskStatus.RUNNING):
    return type("I", (), {"status": status, "supplement_count": 0})()


class TestSupplementLeftover(unittest.TestCase):

    def setUp(self):
        self.tm = BackgroundTaskManager()

        async def _noop(**_kw):
            return None
        self.tm._run_agent_loop = _noop

    def _start(self, session_id, user_input="hi"):
        return asyncio.run(self.tm.start_task(
            session_id=session_id, agent_loop=None, user_input=user_input, memory=None))

    def test_start_task_preserves_leftover(self):
        s = "sess_leftover"
        info = _fake_info()
        self.tm._info[s] = info
        self.assertEqual(self.tm.enqueue_supplement_message(s, {"content": "会话收尾时到的消息"}), True)
        info.status = TaskStatus.COMPLETED

        self.assertTrue(self._start(s))
        self.assertEqual(self.tm.drain_supplement_messages(s), [{"content": "会话收尾时到的消息"}])

    def test_start_task_no_leftover_initializes_empty(self):
        s = "sess_fresh"
        self.assertTrue(self._start(s))
        self.assertEqual(self.tm.drain_supplement_messages(s), [])

    def test_drain_clears(self):
        s = "sess_drain"
        self.tm._info[s] = _fake_info()
        self.tm.enqueue_supplement_message(s, {"content": "a"})
        self.tm.enqueue_supplement_message(s, {"content": "b"})
        self.assertEqual(len(self.tm.drain_supplement_messages(s)), 2)
        self.assertEqual(self.tm.drain_supplement_messages(s), [])

    def test_enqueue_requires_running(self):
        s = "sess_stopped"
        self.tm._info[s] = _fake_info(TaskStatus.COMPLETED)
        self.assertFalse(self.tm.enqueue_supplement_message(s, {"content": "x"}))

    def test_cleanup_task_keeps_unconsumed(self):
        s = "sess_cleanup"
        self.tm._info[s] = _fake_info()
        self.tm.enqueue_supplement_message(s, {"content": "未消费"})
        self.tm.cleanup_task(s)
        self.assertEqual(len(self.tm.drain_supplement_messages(s)), 1)


class TestPreFinalDrainLimit(unittest.TestCase):
    """收尾 drain：达到最大迭代时不消费队列，消息留给下次任务"""

    def test_limit_round_does_not_drain(self):
        import inspect
        from app.agent.loop.agent_loop import AgentLoop
        src = inspect.getsource(AgentLoop.run_stream)
        tail = src[src.index("# 收尾前消费补充消息"):]
        limit_idx = tail.index("iteration < self.max_iterations")
        drain_idx = tail.index("drain_supplement_messages")
        self.assertLess(limit_idx, drain_idx, "上限判断必须先于 drain，否则消息被丢弃")


class TestSupplementRouteFallback(unittest.TestCase):
    """任务结束后发补充消息：兜底起新任务，不丢消息"""

    def _call(self, tm, running):
        import app.server.routes.chat as chat_mod

        captured = {}

        async def _fake_start(**kwargs):
            captured.update(kwargs)
            return True

        async def _fake_loop(_sid):
            return object()

        if running:
            tm._info["s1"] = _fake_info()
        else:
            tm.start_task = _fake_start

        with mock.patch.object(chat_mod, "get_task_manager", lambda: tm), \
                mock.patch.object(chat_mod, "_get_session_loop", _fake_loop), \
                mock.patch.object(chat_mod, "get_session_manager",
                                  lambda: mock.Mock(get_or_create=lambda sid: mock.Mock(memory=None))):
            req = chat_mod.SupplementRequest(message="补发消息", session_id="s1")
            return asyncio.run(chat_mod.supplement_chat(req)), captured

    def test_running_task_queues(self):
        tm = BackgroundTaskManager()
        res, captured = self._call(tm, running=True)
        self.assertEqual(res["status"], "queued")
        self.assertNotIn("user_input", captured)

    def test_not_running_starts_new_task(self):
        tm = BackgroundTaskManager()
        res, captured = self._call(tm, running=False)
        self.assertEqual(res["status"], "started")
        self.assertEqual(res["mode"], "new_task")
        self.assertEqual(captured.get("user_input"), "补发消息")
        self.assertEqual(captured.get("session_id"), "s1")

    def test_empty_message_ignored(self):
        tm = BackgroundTaskManager()
        import app.server.routes.chat as chat_mod
        req = chat_mod.SupplementRequest(message="   ", session_id="s1")
        with mock.patch.object(chat_mod, "get_task_manager", lambda: tm):
            res = asyncio.run(chat_mod.supplement_chat(req))
        self.assertEqual(res["status"], "ignored")


if __name__ == "__main__":
    unittest.main()
