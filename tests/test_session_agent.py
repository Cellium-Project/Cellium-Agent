# -*- coding: utf-8 -*-
"""session_agent 会话委托组件测试"""

import asyncio
import inspect
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import components.session_agent as sa_mod
from app.core.util import delegation_state
from components.session_agent import SessionAgent


class FakeStore:
    def __init__(self, existing=(), sessions=None):
        self.existing = set(existing)
        self.created = []
        self.title_calls = []
        self.titles = {}
        self.sessions = list(sessions or [])

    def session_exists(self, session_id):
        return session_id in self.existing

    def get_or_create_session(self, session_id=None):
        self.existing.add(session_id)
        self.created.append(session_id)
        return SimpleNamespace(session_id=session_id, title=None)

    def list_sessions(self, limit=50):
        return self.sessions[:limit]

    def set_session_title(self, session_id, title):
        self.title_calls.append((session_id, title))
        self.titles[session_id] = title
        for meta in self.sessions:
            if meta.session_id == session_id:
                meta.title = title


def _meta(session_id, title=None, message_count=0, last_active="2026-01-01T00:00:00"):
    return SimpleNamespace(
        session_id=session_id,
        title=title,
        message_count=message_count,
        last_active=last_active,
        created_at=last_active,
    )


class FakeMemory:
    def __init__(self, messages=None):
        self._messages = messages or []

    def get_messages(self):
        return list(self._messages)


class FakeSessionManager:
    def get_or_create(self, session_id, flash_mode=None):
        memory = FakeMemory([{"role": "user", "id": "m_fallback", "content": "hi"}])
        return SimpleNamespace(memory=memory)


class FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeLoop:
    def __init__(self, events=None, delay=0.0):
        self.events = events or []
        self.delay = delay
        self.stop_calls = 0
        self.calls = []

    def stop(self):
        self.stop_calls += 1

    async def run_stream(self, user_input, memory=None, session_id=None,
                         system_injection=None, mode=None, delegated_from=None):
        self.calls.append({
            "user_input": user_input,
            "session_id": session_id,
            "delegated_from": delegated_from,
            "system_injection": system_injection,
            "mode": mode,
        })
        if self.delay:
            await asyncio.sleep(self.delay)
        for event in self.events:
            yield event


class FakeManager:
    def __init__(self, loop):
        self.loop = loop

    async def get_lock(self, session_id):
        return FakeLock()

    async def get_loop(self, session_id):
        return self.loop

    def get_loop_sync(self, session_id):
        return self.loop


class FakeBroker:
    def __init__(self):
        self.published = []

    def publish(self, session_id, event):
        self.published.append({"session_id": session_id, "event": event})


class SessionAgentTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._old_store = delegation_state.STORE_PATH
        delegation_state.STORE_PATH = os.path.join(self._tmpdir, "delegations.json")
        self.sa = SessionAgent()
        self._reset_state()

    def tearDown(self):
        self._reset_state()
        delegation_state.STORE_PATH = self._old_store
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @staticmethod
    def _reset_state():
        with sa_mod._ACTIVE_LOCK:
            sa_mod._DELEGATIONS.clear()
            sa_mod._DELEGATED_ACTIVE.clear()
            sa_mod._PENDING_NOTIFY.clear()
            sa_mod._NOTIFY_WORKERS.clear()


def _patches(store, manager, broker, loop=None):
    return [
        patch("app.agent.loop.session_store.get_session_store", return_value=store),
        patch.object(sa_mod, "_get_main_loop", side_effect=asyncio.get_running_loop) if loop is None
        else patch.object(sa_mod, "_get_main_loop", return_value=loop),
        patch("app.agent.loop.agent_loop_manager.AgentLoopManager.get_instance", return_value=manager),
        patch("app.agent.loop.session_manager.get_session_manager", return_value=FakeSessionManager()),
        patch("app.server.session_mode.get_mode", return_value="run"),
        patch("app.messaging.message_broker.get_message_broker", return_value=broker),
    ]


class TestValidation(SessionAgentTestBase):
    def test_invalid_targets(self):
        r = self.sa._cmd_send(session_id="A")
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "target_required")

        r = self.sa._cmd_send(target="A", message="x", session_id="A")
        self.assertEqual(r["reason"], "self_target")

        r = self.sa._cmd_send(target="subagent:worker:abc", message="x", session_id="A")
        self.assertEqual(r["reason"], "subagent_target")

    def test_target_not_found_and_create(self):
        store = FakeStore()
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            r = self.sa._cmd_send(target="B", message="x", session_id="A")
            self.assertEqual(r["reason"], "target_not_found")
            self.assertIn("create=true", r["error"])

            with patch.object(sa_mod, "_get_main_loop", return_value=None):
                r = self.sa._cmd_send(target="B", message="x", create=True, session_id="A")
            self.assertEqual(r["reason"], "no_event_loop")
            self.assertIn("B", store.created)

    def test_nested_delegation_rejected(self):
        with sa_mod._ACTIVE_LOCK:
            sa_mod._DELEGATED_ACTIVE.add("A")
        try:
            with patch("app.agent.loop.session_store.get_session_store",
                       return_value=FakeStore({"B"})):
                r = self.sa._cmd_send(target="B", message="x", session_id="A")
            self.assertEqual(r["reason"], "nested_delegation")
            self.assertIn("禁止再委托", r["error"])
        finally:
            with sa_mod._ACTIVE_LOCK:
                sa_mod._DELEGATED_ACTIVE.discard("A")

    def test_plan_mode_rejected(self):
        from app.server.session_mode import set_mode
        set_mode("plan_scope", "plan", broadcast=False)
        try:
            with patch("app.agent.loop.session_store.get_session_store",
                       return_value=FakeStore({"B"})):
                r = self.sa._cmd_send(target="B", message="x", session_id="plan_scope")
            self.assertEqual(r["reason"], "plan_mode_forbidden")
        finally:
            set_mode("plan_scope", "run", broadcast=False)

    def test_subagent_rejected(self):
        with patch("app.agent.loop.session_store.get_session_store",
                   return_value=FakeStore({"B"})):
            r = self.sa._cmd_send(target="B", message="x", session_id="subagent:worker:1")
        self.assertEqual(r["reason"], "subagent_forbidden")

    def test_too_many_active(self):
        now = time.time()
        for i in range(4):
            sa_mod._DELEGATIONS[f"A::dlg_x{i}"] = {
                "task_id": f"dlg_x{i}", "target": "B", "status": "running", "started_at": now,
            }
        with patch("app.agent.loop.session_store.get_session_store",
                   return_value=FakeStore({"B"})):
            r = self.sa._cmd_send(target="B", message="x", session_id="A")
        self.assertEqual(r["reason"], "too_many_active")


class TestDelegationFlow(SessionAgentTestBase):
    def test_send_run_and_results(self):
        async def _scenario():
            broker = FakeBroker()
            loop = FakeLoop([
                {"type": "tool_start", "tool": "read", "arguments": {}},
                {"type": "done", "content": "结果", "tracked_turn_id": "m_b1"},
            ])
            store = FakeStore({"B"})
            for p in _patches(store, FakeManager(loop), broker):
                p.start()
            try:
                r = self.sa._cmd_send(
                    target="B", message="做点事", purpose="验证", session_id="A"
                )
                self.assertTrue(r["success"])
                self.assertTrue(r["task_id"].startswith("dlg_"))
                key = f"A::{r['task_id']}"
                self.assertEqual(sa_mod._DELEGATIONS[key]["status"], "queued")

                await asyncio.sleep(0.1)

                self.assertEqual(sa_mod._DELEGATIONS[key]["status"], "done")
                res = self.sa._cmd_results(target="B", session_id="A")
                self.assertEqual(res["content"], "结果")
                self.assertEqual(res["turn_id"], "m_b1")
                self.assertFalse(res["running"])
                self.assertIn("turn_id", res["files_note"])
                self.assertEqual(loop.calls[0]["delegated_from"], "A")
                self.assertEqual(loop.calls[0]["session_id"], "B")
            finally:
                patch.stopall()

        asyncio.run(_scenario())

    def test_timeout_stops_target_loop(self):
        async def _scenario():
            broker = FakeBroker()
            loop = FakeLoop([
                {"type": "done", "content": "迟到", "tracked_turn_id": "m_late"},
            ], delay=1.0)
            for p in _patches(FakeStore({"B"}), FakeManager(loop), broker):
                p.start()
            try:
                r = self.sa._cmd_send(
                    target="B", message="慢任务", timeout=0.1, session_id="A"
                )
                self.assertTrue(r["success"])
                await asyncio.sleep(0.3)
                rec = sa_mod._DELEGATIONS[f"A::{r['task_id']}"]
                self.assertEqual(rec["status"], "timeout")
                self.assertGreaterEqual(loop.stop_calls, 1)
                self.assertIn("0.1", rec["error"])
            finally:
                patch.stopall()

        asyncio.run(_scenario())

    def test_done_event_contract(self):
        async def _scenario():
            broker = FakeBroker()
            loop = FakeLoop([
                {"type": "tool_start", "tool": "read", "arguments": {}},
                {"type": "tool_result", "tool": "read", "result": "ok"},
                {"type": "done", "content": "结论", "tracked_turn_id": "m_9"},
            ])
            for p in _patches(FakeStore({"B"}), FakeManager(loop), broker):
                p.start()
            try:
                r = self.sa._cmd_send(
                    target="B", message="x", purpose="验证契约", session_id="A"
                )
                self.assertTrue(r["success"])
                await asyncio.sleep(0.1)

                dones = [
                    e["event"] for e in broker.published
                    if e["event"].get("type") == "subagent_done"
                ]
                self.assertEqual(len(dones), 1)
                event = dones[0]
                self.assertEqual(event["kind"], "session")
                self.assertEqual(event["name"], "session:B")
                self.assertEqual(event["target"], "B")
                self.assertEqual(event["purpose"], "验证契约")
                self.assertEqual(event["status"], "done")
                self.assertTrue(event["success"])
                self.assertEqual(event["turn_id"], "m_9")
                self.assertEqual(broker.published[0]["session_id"], "A")
            finally:
                patch.stopall()

        asyncio.run(_scenario())

    def test_injection_forbids_nested(self):
        async def _scenario():
            loop = FakeLoop([{"type": "done", "content": "ok", "tracked_turn_id": "m_1"}])
            for p in _patches(FakeStore({"B"}), FakeManager(loop), FakeBroker()):
                p.start()
            try:
                self.sa._cmd_send(target="B", message="x", purpose="整理数据", session_id="A")
                await asyncio.sleep(0.1)
                injection = loop.calls[0]["system_injection"]
                self.assertIn("会话委托", injection)
                self.assertIn("「A」", injection)
                self.assertIn("整理数据", injection)
                self.assertIn("禁止调用 session_agent", injection)
            finally:
                patch.stopall()

        asyncio.run(_scenario())

    def test_cancel_running_delegation(self):
        async def _scenario():
            loop = FakeLoop([{"type": "done", "content": "迟到"}], delay=1.0)
            for p in _patches(FakeStore({"B"}), FakeManager(loop), FakeBroker()):
                p.start()
            try:
                r = self.sa._cmd_send(target="B", message="长任务", session_id="A")
                await asyncio.sleep(0.05)
                c = self.sa._cmd_cancel(target="B", session_id="A")
                self.assertTrue(c["success"])
                await asyncio.sleep(0.2)
                rec = sa_mod._DELEGATIONS[f"A::{r['task_id']}"]
                self.assertEqual(rec["status"], "cancelled")
                self.assertGreaterEqual(loop.stop_calls, 1)
            finally:
                patch.stopall()

        asyncio.run(_scenario())


class TestTargetResolution(SessionAgentTestBase):
    def test_exact_id_beats_same_title(self):
        store = FakeStore(
            {"sess_a", "sess_b"},
            sessions=[_meta("sess_b", title="sess_a"), _meta("sess_a", title="甲")],
        )
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            resolved, err = self.sa._resolve_target("sess_a")
        self.assertIsNone(err)
        self.assertEqual(resolved, "sess_a")

    def test_unique_title_delegates_to_real_id(self):
        async def _scenario():
            broker = FakeBroker()
            loop = FakeLoop([{"type": "done", "content": "ok", "tracked_turn_id": "m_1"}])
            store = FakeStore({"sess_b"}, sessions=[_meta("sess_b", title="调研任务")])
            for p in _patches(store, FakeManager(loop), broker):
                p.start()
            try:
                r = self.sa._cmd_send(target="调研任务", message="x", session_id="A")
                self.assertTrue(r["success"])
                rec = sa_mod._DELEGATIONS[f"A::{r['task_id']}"]
                self.assertEqual(rec["target"], "sess_b")
                await asyncio.sleep(0.1)
                self.assertEqual(loop.calls[0]["session_id"], "sess_b")
            finally:
                patch.stopall()

        asyncio.run(_scenario())

    def test_duplicate_title_ambiguous(self):
        store = FakeStore(
            {"sess_a", "sess_b"},
            sessions=[_meta("sess_a", title="同名"), _meta("sess_b", title="同名")],
        )
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            r = self.sa._cmd_send(target="同名", message="x", session_id="A")
        self.assertEqual(r["reason"], "ambiguous_target")
        self.assertGreaterEqual(len(r["candidates"]), 2)
        self.assertEqual({c["session_id"] for c in r["candidates"]}, {"sess_a", "sess_b"})

    def test_fuzzy_case_insensitive_unique(self):
        store = FakeStore({"sess_b"}, sessions=[_meta("sess_b", title="Daily Report")])
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            resolved, err = self.sa._resolve_target("daily report")
        self.assertIsNone(err)
        self.assertEqual(resolved, "sess_b")

    def test_not_found_with_recent_hint(self):
        store = FakeStore(sessions=[_meta("sess_x", title="最近会话")])
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            r = self.sa._cmd_send(target="不存在", message="x", session_id="A")
        self.assertEqual(r["reason"], "target_not_found")
        self.assertIn("最近会话", r["error"])
        self.assertIn("sess_x", r["error"])

    def test_resolve_failed_on_store_error(self):
        class BrokenStore:
            def session_exists(self, session_id):
                raise RuntimeError("boom")

        with patch("app.agent.loop.session_store.get_session_store", return_value=BrokenStore()):
            r = self.sa._cmd_send(target="x", message="y", session_id="A")
        self.assertEqual(r["reason"], "resolve_failed")


class TestSessionsAndRename(SessionAgentTestBase):
    def test_sessions_command(self):
        store = FakeStore(
            sessions=[
                _meta("sess_a", title="甲会话", message_count=3, last_active="2026-02-01"),
                _meta("sess_b", title="乙会话", message_count=1, last_active="2026-01-01"),
                _meta("sess_c", title=None),
            ]
        )
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            r = self.sa._cmd_sessions(session_id="sess_b")
            self.assertTrue(r["success"])
            self.assertEqual(r["count"], 3)
            self.assertTrue(all(not s["is_current"] for s in r["sessions"] if s["session_id"] != "sess_b"))
            self.assertTrue(next(s for s in r["sessions"] if s["session_id"] == "sess_b")["is_current"])
            self.assertEqual(
                next(s for s in r["sessions"] if s["session_id"] == "sess_c")["title"],
                "未命名-sess_c",
            )

            r = self.sa._cmd_sessions(keyword="甲")
            self.assertEqual(r["count"], 1)
            self.assertEqual(r["sessions"][0]["session_id"], "sess_a")

            r = self.sa._cmd_sessions(keyword="SESS_B")
            self.assertEqual(r["count"], 1)
            self.assertEqual(r["sessions"][0]["session_id"], "sess_b")

    def test_rename_ok_and_invalid(self):
        store = FakeStore({"sess_a"}, sessions=[_meta("sess_a", title="旧名")])
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            r = self.sa._cmd_rename(target="旧名", title="新\n名字", session_id="A")
            self.assertTrue(r["success"])
            self.assertEqual(r["session_id"], "sess_a")
            self.assertEqual(r["title"], "新 名字")
            self.assertEqual(r["previous_title"], "旧名")
            self.assertEqual(store.title_calls[-1], ("sess_a", "新 名字"))

            self.assertEqual(self.sa._cmd_rename(target="sess_a", title="  "), self.sa._fail(
                "title 必填且长度需在 1..60 之间", "invalid_title"))
            r = self.sa._cmd_rename(target="sess_a", title="x" * 61)
            self.assertEqual(r["reason"], "invalid_title")

    def test_send_create_with_title(self):
        store = FakeStore()
        with patch("app.agent.loop.session_store.get_session_store", return_value=store):
            with patch.object(sa_mod, "_get_main_loop", return_value=None):
                r = self.sa._cmd_send(
                    target="sess_new", message="x", create=True, title="新会话名", session_id="A"
                )
        self.assertEqual(r["reason"], "no_event_loop")
        self.assertEqual(store.title_calls, [("sess_new", "新会话名")])
        self.assertIn("sess_new", store.created)


class TestPermissions(SessionAgentTestBase):

    @staticmethod
    def _ctx(platform=None):
        info = SimpleNamespace(platform_context={"platform": platform, "user_id": "u"} if platform else {})
        mgr = SimpleNamespace(get=lambda sid: info)
        return patch("app.agent.loop.session_manager.get_session_manager", return_value=mgr)

    def test_external_channel_cannot_rename(self):
        store = FakeStore({"sess_a"}, sessions=[_meta("sess_a", title="旧名")])
        with self._ctx("qq"):
            with patch("app.agent.loop.session_store.get_session_store", return_value=store):
                r = self.sa._cmd_rename(target="sess_a", title="新名", session_id="qq:u1")
        self.assertEqual(r["reason"], "forbidden_scope")
        self.assertEqual(store.title_calls, [])

    def test_external_channel_cannot_send_or_list(self):
        with self._ctx("feishu"):
            with patch("app.agent.loop.session_store.get_session_store",
                       return_value=FakeStore({"B"})):
                r = self.sa._cmd_send(target="B", message="x", session_id="feishu:u1")
                self.assertEqual(r["reason"], "forbidden_scope")
                r = self.sa._cmd_sessions(session_id="feishu:u1")
                self.assertEqual(r["reason"], "forbidden_scope")

    def test_delegated_caller_cannot_rename(self):
        with sa_mod._ACTIVE_LOCK:
            sa_mod._DELEGATED_ACTIVE.add("A")
        try:
            store = FakeStore({"sess_a"}, sessions=[_meta("sess_a", title="旧名")])
            with patch("app.agent.loop.session_store.get_session_store", return_value=store):
                r = self.sa._cmd_rename(target="sess_a", title="新名", session_id="A")
            self.assertEqual(r["reason"], "nested_delegation")
            self.assertEqual(store.title_calls, [])
        finally:
            with sa_mod._ACTIVE_LOCK:
                sa_mod._DELEGATED_ACTIVE.discard("A")

    def test_local_caller_can_rename(self):
        store = FakeStore({"sess_a"}, sessions=[_meta("sess_a", title="旧名")])
        with self._ctx(None):
            with patch("app.agent.loop.session_store.get_session_store", return_value=store):
                r = self.sa._cmd_rename(target="sess_a", title="新名", session_id="A")
        self.assertTrue(r["success"])
        self.assertEqual(store.title_calls[-1], ("sess_a", "新名"))


class TestResultClip(SessionAgentTestBase):

    def test_long_result_truncated(self):
        from components.session_agent import _clip_result, _MAX_RESULT_CHARS
        text, cut = _clip_result("x" * 5000)
        self.assertTrue(cut)
        self.assertIn("[截断]", text)
        self.assertEqual(len(text), _MAX_RESULT_CHARS + len("\n...[截断]"))
        short, cut2 = _clip_result("ok")
        self.assertFalse(cut2)
        self.assertEqual(short, "ok")

    def test_results_caps_content(self):
        sa_mod._DELEGATIONS["A::dlg_long"] = {
            "task_id": "dlg_long", "target": "B", "status": "done",
            "started_at": time.time(), "result_content": "y" * 5001,
        }
        r = self.sa._cmd_results(task_id="dlg_long", session_id="A")
        self.assertTrue(r["truncated"])
        self.assertLessEqual(len(r["content"]), 4000 + len("\n...[截断]"))


class TestDelegationPersistence(unittest.TestCase):

    def test_roundtrip_marks_running_as_interrupted(self):
        tmp = tempfile.mkdtemp()
        old_path = delegation_state.STORE_PATH
        snapshot = dict(delegation_state.DELEGATIONS)
        delegation_state.STORE_PATH = os.path.join(tmp, "delegations.json")
        try:
            with delegation_state.LOCK:
                delegation_state.DELEGATIONS.clear()
                delegation_state.DELEGATIONS["A::dlg_run"] = {
                    "task_id": "dlg_run", "parent": "A", "target": "B",
                    "status": "running", "started_at": 1, "updated_at": 2,
                }
                delegation_state.DELEGATIONS["A::dlg_done"] = {
                    "task_id": "dlg_done", "parent": "A", "target": "B",
                    "status": "done", "started_at": 1, "finished_at": 3,
                    "result_content": "已完成的结论", "turn_id": "m_1",
                }
            delegation_state.save()

            loaded = delegation_state.load()
            self.assertEqual(loaded["A::dlg_run"]["status"], "unknown")
            self.assertTrue(loaded["A::dlg_run"]["interrupted"])
            self.assertIn("重启", loaded["A::dlg_run"]["error"])
            self.assertEqual(loaded["A::dlg_done"]["status"], "done")
            self.assertEqual(loaded["A::dlg_done"]["result_content"], "已完成的结论")

            with delegation_state.LOCK:
                delegation_state.DELEGATIONS.clear()
                delegation_state.DELEGATIONS.update(loaded)
            r = SessionAgent()._cmd_results(task_id="dlg_run", session_id="A")
            self.assertEqual(r["status"], "unknown")
            self.assertTrue(r["running"] is False)
            self.assertIn("重启", r["error"])

            done = SessionAgent()._cmd_results(task_id="dlg_done", session_id="A")
            self.assertEqual(done["content"], "已完成的结论")
            self.assertEqual(done["turn_id"], "m_1")
        finally:
            delegation_state.STORE_PATH = old_path
            with delegation_state.LOCK:
                delegation_state.DELEGATIONS.clear()
                delegation_state.DELEGATIONS.update(snapshot)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_persisted_result_is_capped(self):
        tmp = tempfile.mkdtemp()
        old_path = delegation_state.STORE_PATH
        snapshot = dict(delegation_state.DELEGATIONS)
        delegation_state.STORE_PATH = os.path.join(tmp, "delegations.json")
        try:
            with delegation_state.LOCK:
                delegation_state.DELEGATIONS.clear()
                delegation_state.DELEGATIONS["A::dlg_big"] = {
                    "task_id": "dlg_big", "status": "done", "result_content": "z" * 5000,
                }
            delegation_state.save()
            loaded = delegation_state.load()
            self.assertLessEqual(len(loaded["A::dlg_big"]["result_content"]), 4000)
        finally:
            delegation_state.STORE_PATH = old_path
            with delegation_state.LOCK:
                delegation_state.DELEGATIONS.clear()
                delegation_state.DELEGATIONS.update(snapshot)
            shutil.rmtree(tmp, ignore_errors=True)


class FakeTaskManager:
    def __init__(self, running=False, start_ok=True):
        self.running = running
        self.start_ok = start_ok
        self.enqueued = []
        self.started = []
        self.pending = {}
        self.cancelled = False

    def has_running_task(self, session_id):
        return self.running

    def get_task_info(self, session_id):
        if not self.cancelled:
            return None
        status = type("S", (), {"value": "cancelled"})()
        return type("I", (), {"status": status})()

    def enqueue_supplement_message(self, session_id, payload):
        self.enqueued.append((session_id, payload))
        self.pending.setdefault(session_id, []).append(payload)
        return True

    def has_pending_supplement(self, session_id, msg_id=""):
        queue = self.pending.get(session_id) or []
        if not msg_id:
            return bool(queue)
        return any(m.get("msg_id") == msg_id for m in queue)

    def claim_supplement_message(self, session_id, msg_id):
        queue = self.pending.setdefault(session_id, [])
        for i, item in enumerate(queue):
            if item.get("msg_id") == msg_id:
                queue.pop(i)
                return True
        return False

    async def start_task(self, **kwargs):
        self.started.append(kwargs)
        return self.start_ok


class TestNotifyDelivery(SessionAgentTestBase):

    _INFO = {
        "target": "B", "purpose": "验证推送", "status": "done",
        "result_content": "结论内容", "turn_id": "m_b1",
        "started_at": 1, "finished_at": 2,
    }

    def test_parent_busy_enqueues_supplement(self):
        async def _scenario():
            tm = FakeTaskManager(running=True)
            with patch("app.server.task_manager.get_task_manager", return_value=tm):
                await self.sa._notify_parent("A", dict(self._INFO))
                await asyncio.sleep(0.05)
            self.assertEqual(len(tm.enqueued), 1)
            sid, payload = tm.enqueued[0]
            self.assertEqual(sid, "A")
            self.assertEqual(payload["source"], "session_agent")
            self.assertIn("结论内容", payload["content"])
            self.assertIn("turn_id=m_b1", payload["content"])
            self.assertEqual(tm.started, [])

        asyncio.run(_scenario())

    def test_parent_idle_starts_new_turn(self):
        async def _scenario():
            tm = FakeTaskManager(running=False)
            loop = FakeLoop([])
            with patch("app.server.task_manager.get_task_manager", return_value=tm):
                with patch("app.agent.loop.agent_loop_manager.AgentLoopManager.get_instance",
                           return_value=FakeManager(loop)):
                    with patch("app.agent.loop.session_manager.get_session_manager",
                               return_value=FakeSessionManager()):
                        with patch("app.server.session_mode.get_mode", return_value="run"):
                            await self.sa._notify_parent("A", dict(self._INFO))
                            await asyncio.sleep(0.05)
            self.assertEqual(len(tm.started), 1)
            self.assertEqual(tm.started[0]["session_id"], "A")
            self.assertIn("结论内容", tm.started[0]["user_input"])
            self.assertEqual(tm.enqueued, [])

        asyncio.run(_scenario())

    def test_failed_delivery_retries_until_ok(self):
        async def _scenario():
            self.sa._deliver_parent_message = AsyncMock(side_effect=["failed", "consumed"])
            with patch.object(sa_mod, "_NOTIFY_RETRY_INTERVAL", 0.01):
                await self.sa._notify_parent("A", dict(self._INFO))
                await asyncio.sleep(0.2)
            self.assertEqual(self.sa._deliver_parent_message.await_count, 2)
            self.assertEqual(len(sa_mod._PENDING_NOTIFY.get("A") or []), 0)
            self.assertNotIn("A", sa_mod._NOTIFY_WORKERS)

        asyncio.run(_scenario())

    def test_parent_finishing_after_enqueue_still_delivers(self):
        async def _scenario():
            tm = FakeTaskManager(running=True)
            loop = FakeLoop([])
            with patch("app.server.task_manager.get_task_manager", return_value=tm):
                with patch("app.agent.loop.agent_loop_manager.AgentLoopManager.get_instance",
                           return_value=FakeManager(loop)):
                    with patch("app.agent.loop.session_manager.get_session_manager",
                               return_value=FakeSessionManager()):
                        with patch("app.server.session_mode.get_mode", return_value="run"):
                            with patch.object(sa_mod, "_NOTIFY_RETRY_INTERVAL", 0.01):
                                await self.sa._notify_parent("A", dict(self._INFO))
                                await asyncio.sleep(0.05)
                                self.assertEqual(len(tm.enqueued), 1)
                                tm.running = False
                                await asyncio.sleep(0.15)
            self.assertEqual(len(tm.started), 1, "父会话结束后应接手消费而不是滞留")
            self.assertEqual(len(sa_mod._PENDING_NOTIFY.get("A") or []), 0)

        asyncio.run(_scenario())

    def test_consumed_message_not_requeued(self):
        async def _scenario():
            tm = FakeTaskManager(running=True)
            with patch("app.server.task_manager.get_task_manager", return_value=tm):
                with patch.object(sa_mod, "_NOTIFY_RETRY_INTERVAL", 0.01):
                    await self.sa._notify_parent("A", dict(self._INFO))
                    await asyncio.sleep(0.05)
                    self.assertEqual(len(tm.enqueued), 1)
                    tm.enqueued.clear()
                    tm.pending.clear()
                    await asyncio.sleep(0.15)
            self.assertEqual(tm.enqueued, [], "已消费的回执不得重复入队")
            self.assertEqual(len(sa_mod._PENDING_NOTIFY.get("A") or []), 0)

        asyncio.run(_scenario())

    def test_cancelled_parent_drops_notification(self):
        async def _scenario():
            tm = FakeTaskManager(running=True)
            tm.cancelled = True
            with patch("app.server.task_manager.get_task_manager", return_value=tm):
                with patch.object(sa_mod, "_NOTIFY_RETRY_INTERVAL", 0.01):
                    await self.sa._notify_parent("A", dict(self._INFO))
                    await asyncio.sleep(0.1)
            self.assertEqual(tm.enqueued, [], "父会话已取消不应再投递")
            self.assertEqual(len(sa_mod._PENDING_NOTIFY.get("A") or []), 0)

        asyncio.run(_scenario())

    def test_no_notify_no_queue(self):
        async def _scenario():
            # notify=False 时 _run_delegation 不会调用 _notify_parent
            self.assertEqual(sa_mod._PENDING_NOTIFY, {})

        asyncio.run(_scenario())

    def _send_with_mode(self, mode):
        class _Future:
            def add_done_callback(self, cb):
                pass

        def _run(coro, loop):
            coro.close()
            return _Future()

        before = set(sa_mod._DELEGATIONS)
        with patch.object(sa_mod, "_get_main_loop",
                          return_value=SimpleNamespace(is_running=lambda: True)), \
             patch("app.agent.loop.session_store.get_session_store",
                   return_value=FakeStore({"B"})), \
             patch("app.server.session_mode.get_mode", return_value=mode), \
             patch.object(sa_mod.asyncio, "run_coroutine_threadsafe", side_effect=_run):
            result = self.sa._cmd_send(target="B", message="x", session_id="A")
        key = next(k for k in sa_mod._DELEGATIONS if k not in before)
        info = sa_mod._DELEGATIONS.pop(key)
        return result, info

    def test_send_defaults_notify_true_in_delegate_mode(self):
        result, info = self._send_with_mode("delegate")
        self.assertTrue(result["success"])
        self.assertTrue(info["notify"])

    def test_send_defaults_notify_true_in_run_mode(self):
        result, info = self._send_with_mode("run")
        self.assertTrue(result["success"])
        self.assertTrue(info["notify"])

    def test_attach_channel_reply_for_platform_parent(self):
        calls = []

        class _ChannelManager:
            def start_scheduler_task_queue_consumer(self, *args, **kwargs):
                calls.append((args, kwargs))

        class _SessionManager:
            def get_or_create(self, session_id):
                return SimpleNamespace(platform_context={"platform": "qq"})

        with patch("app.agent.loop.session_manager.get_session_manager",
                   return_value=_SessionManager()), \
             patch("app.server.task_manager.get_task_manager",
                   return_value=SimpleNamespace(get_queue=lambda sid: object())), \
             patch("app.channels.ChannelManager.get_instance",
                   return_value=_ChannelManager()):
            sa_mod.SessionAgent._attach_channel_reply("A")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].get("trigger_type"), "component")

    def test_attach_channel_reply_skips_non_platform(self):
        class _SessionManager:
            def get_or_create(self, session_id):
                return SimpleNamespace(platform_context={})

        with patch("app.agent.loop.session_manager.get_session_manager",
                   return_value=_SessionManager()):
            sa_mod.SessionAgent._attach_channel_reply("A")


class TestConsumeStatus(SessionAgentTestBase):

    def _consume(self, events):
        async def _run():
            loop = FakeLoop(events)
            info = {"cancel_requested": False}
            memory = FakeMemory([{"role": "user", "id": "m_b1"}])
            return await self.sa._consume(loop, info, "x", memory, "B", "inj", "run", "A")

        return asyncio.run(_run())

    def test_normal_done(self):
        status, content, error, turn_id = self._consume(
            [{"type": "done", "content": "结论", "tracked_turn_id": "m_1"}]
        )
        self.assertEqual(status, "done")
        self.assertEqual(content, "结论")
        self.assertIsNone(error)
        self.assertEqual(turn_id, "m_1")

    def test_forced_stop_is_not_done(self):
        status, content, error, _ = self._consume([
            {"type": "stopped", "reason": "max_iterations_exceeded"},
            {"type": "done", "content": "", "completed": False, "reason": "max_iterations_exceeded"},
        ])
        self.assertEqual(status, "error")
        self.assertIn("最大迭代次数", error)

    def test_user_cancel_is_cancelled(self):
        status, _, error, _ = self._consume([
            {"type": "stopped", "reason": "user_cancelled"},
            {"type": "done", "content": "", "completed": False, "reason": "user_cancelled"},
        ])
        self.assertEqual(status, "cancelled")

    def test_empty_done_is_error(self):
        status, content, error, _ = self._consume(
            [{"type": "done", "content": "", "tracked_turn_id": "m_2"}]
        )
        self.assertEqual(status, "error")
        self.assertEqual(content, "")
        self.assertIn("没有返回结论", error)

    def test_incomplete_done_without_stop_event(self):
        status, _, error, _ = self._consume([
            {"type": "done", "content": "", "completed": False, "reason": "empty_response"},
        ])
        self.assertEqual(status, "error")
        self.assertIn("回复为空", error)

    def test_loop_detected_is_error(self):
        status, _, error, _ = self._consume([
            {"type": "stopped", "reason": "loop_detected"},
            {"type": "done", "content": "重复", "completed": False, "reason": "loop_detected"},
        ])
        self.assertEqual(status, "error")
        self.assertIn("重复输出", error)


class TestErrorMessages(unittest.TestCase):

    def test_read_error_is_readable(self):
        from components.session_agent import _describe_error
        ReadError = type("ReadError", (Exception,), {})
        text = _describe_error(ReadError(""))
        self.assertIn("网络读取中断", text)
        self.assertIn("ReadError", text)

    def test_empty_message_uses_repr(self):
        from components.session_agent import _describe_error
        self.assertEqual(_describe_error(Exception()), "Exception: Exception()")


class TestStateSurvival(unittest.TestCase):

    def test_state_survives_module_reload(self):
        import importlib
        import sys
        from app.core.util import delegation_state

        key = "P::dlg_reload"
        delegation_state.DELEGATIONS[key] = {
            "task_id": "dlg_reload", "status": "running", "target": "T", "started_at": 1,
        }
        with delegation_state.LOCK:
            delegation_state.DELEGATED_ACTIVE.add("T")
        try:
            mod = sys.modules.get("components.session_agent") or sa_mod
            sys.modules["components.session_agent"] = mod
            reloaded = importlib.reload(mod)
            self.assertIn(key, reloaded._DELEGATIONS)
            self.assertTrue(reloaded.is_delegated("T"))
        finally:
            delegation_state.DELEGATIONS.pop(key, None)
            with delegation_state.LOCK:
                delegation_state.DELEGATED_ACTIVE.discard("T")


class TestTargetTitleEvent(SessionAgentTestBase):
    def test_target_title_in_events(self):
        async def _scenario():
            broker = FakeBroker()
            loop = FakeLoop([
                {"type": "tool_start", "tool": "read", "arguments": {}},
                {"type": "done", "content": "结论", "tracked_turn_id": "m_9"},
            ])
            store = FakeStore({"sess_b"}, sessions=[_meta("sess_b", title="构建会话")])
            for p in _patches(store, FakeManager(loop), broker):
                p.start()
            try:
                r = self.sa._cmd_send(target="sess_b", message="x", session_id="A")
                self.assertTrue(r["success"])
                await asyncio.sleep(0.1)
                events = [e["event"] for e in broker.published]
                dones = [e for e in events if e["type"] == "subagent_done"]
                self.assertEqual(dones[0]["target_title"], "构建会话")
                self.assertEqual(dones[0]["name"], "session:sess_b")
                progresses = [e for e in events if e["type"] == "subagent_progress"]
                self.assertTrue(progresses)
                self.assertTrue(all(e["target_title"] == "构建会话" for e in progresses))
            finally:
                patch.stopall()

        asyncio.run(_scenario())


class TestDelegatedFromPassthrough(unittest.TestCase):
    def test_run_stream_signature(self):
        from app.agent.loop.agent_loop import AgentLoop
        sig = inspect.signature(AgentLoop.run_stream)
        self.assertIn("delegated_from", sig.parameters)
        self.assertIsNone(sig.parameters["delegated_from"].default)

    def test_publish_message_received_source_session(self):
        from app.agent.loop.loop_event_publisher import LoopEventPublisher

        class Bus:
            def __init__(self):
                self.events = []

            def publish(self, event_type, event):
                self.events.append(event)

        bus = Bus()
        LoopEventPublisher(bus).publish_message_received("B", "hello", source_session="A")
        self.assertEqual(len(bus.events), 1)
        self.assertEqual(bus.events[0].data.get("source_session"), "A")
        self.assertEqual(bus.events[0].message, "hello")


if __name__ == "__main__":
    unittest.main()
