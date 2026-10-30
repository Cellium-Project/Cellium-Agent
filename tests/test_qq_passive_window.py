import asyncio
import json
import unittest
from unittest import mock

import httpx

from app.channels.qq import qq_adapter as qq_mod
from app.channels.qq.qq_adapter import QQAdapter


class FakeGroupServer:

    def __init__(self):
        self.requests = []
        self.status = 200
        self.err_code = None
        self.status_sequence = []

    def handler(self, request):
        self.requests.append(request)
        if self.status_sequence:
            status = self.status_sequence.pop(0)
        else:
            status = self.status
        if status != 200:
            body = {"err_code": self.err_code} if self.err_code is not None else {"message": "failed"}
            return httpx.Response(status, json=body)
        return httpx.Response(200, json={"id": "SENT_ID"})

    def payloads(self):
        return [json.loads(r.content.decode("utf-8")) for r in self.requests]

    def contents(self):
        return [
            p.get("content") or (p.get("markdown") or {}).get("content")
            for p in self.payloads()
        ]


def _patch_client(server):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(server.handler)

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    return mock.patch.object(qq_mod.httpx, "AsyncClient", factory)


def _make_adapter():
    adapter = QQAdapter(app_id="", app_secret="")
    adapter._min_send_interval = 0
    adapter._last_send_time = 0

    async def _fake_token():
        return "t"

    adapter._get_access_token = _fake_token
    return adapter


class PassiveWindowTrackerTest(unittest.TestCase):

    def test_window_send_success_marks_used(self):
        server = FakeGroupServer()
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", "hello", "group", msg_id="M1"))

        self.assertTrue(ok)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(server.payloads()[0]["msg_id"], "M1")
        self.assertEqual(adapter._passive.remaining("G"), 4)

    def test_sixth_reply_sends_no_request(self):
        server = FakeGroupServer()
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        for _ in range(qq_mod.MAX_PASSIVE_REPLIES):
            adapter._passive.mark_used("M1")

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", "overflow", "group", msg_id="M1"))

        self.assertFalse(ok)
        self.assertEqual(len(server.requests), 0)
        self.assertTrue(adapter._passive.has_pending("G"))

    def test_expired_window_sends_no_request(self):
        server = FakeGroupServer()
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        adapter._passive._windows["G"]["received_at"] -= 300

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", "late", "group", msg_id="M1"))

        self.assertFalse(ok)
        self.assertEqual(len(server.requests), 0)
        self.assertTrue(adapter._passive.has_pending("G"))

    def test_expired_codes_mark_exhausted_without_markdown_fallback(self):
        for code in (40034005, 40034031, 40034128):
            with self.subTest(code=code):
                server = FakeGroupServer()
                server.status = 400
                server.err_code = code
                adapter = _make_adapter()
                adapter._passive.note_inbound("G", "M1")

                with _patch_client(server):
                    ok = asyncio.run(adapter.send_message(
                        "G", "text", "group", msg_id="M1", markdown=True
                    ))

                self.assertFalse(ok)
                self.assertEqual(len(server.requests), 1)
                self.assertEqual(adapter._passive.remaining("G"), 0)
                self.assertFalse(adapter._md_unavailable)
                self.assertTrue(adapter._passive.has_pending("G"))

    def test_markdown_failure_still_falls_back_to_plain(self):
        server = FakeGroupServer()
        server.status_sequence = [400, 200]
        server.err_code = 40034008
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message(
                "G", "text", "group", msg_id="M1", markdown=True
            ))

        self.assertTrue(ok)
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(server.payloads()[1]["msg_type"], 0)
        self.assertEqual(adapter._passive.remaining("G"), 4)

    def test_empty_msg_id_without_window_enqueues(self):
        server = FakeGroupServer()
        adapter = _make_adapter()

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", "queued", "group", msg_id=""))

        self.assertFalse(ok)
        self.assertEqual(len(server.requests), 0)
        self.assertEqual(adapter._passive.peek_pending("G"), ["queued"])

    def test_new_inbound_flushes_pending_before_new_content(self):
        server = FakeGroupServer()
        adapter = _make_adapter()

        with _patch_client(server):
            asyncio.run(adapter.send_message("G", "A", "group", msg_id=""))
            adapter._passive.note_inbound("G", "M1")
            ok = asyncio.run(adapter.send_message("G", "B", "group", msg_id="M1"))

        self.assertTrue(ok)
        self.assertEqual(adapter._passive.resolve("G"), "M1")
        self.assertEqual(server.contents(), ["A", "B"])
        self.assertEqual([p["msg_id"] for p in server.payloads()], ["M1", "M1"])
        self.assertFalse(adapter._passive.has_pending("G"))

    def test_flush_reserves_one_slot(self):
        server = FakeGroupServer()
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        for _ in range(3):
            adapter._passive.mark_used("M1")
        adapter._passive.enqueue("G", "OLD")
        adapter._passive.note_inbound("G", "M1")

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", "NEW", "group", msg_id="M1"))

        self.assertTrue(ok)
        self.assertEqual(server.contents(), ["OLD", "NEW"])
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(adapter._passive.remaining("G"), 0)

    def test_flush_exception_keeps_pending(self):
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        adapter._passive.enqueue("G", "KEEP")
        adapter._passive.note_inbound("G", "M1")

        async def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        with mock.patch.object(adapter, "_send_group_message", _boom):
            ok = asyncio.run(adapter.send_message("G", "NEW", "group", msg_id="M1"))

        self.assertFalse(ok)
        self.assertEqual(adapter._passive.peek_pending("G"), ["KEEP", "NEW"])

    def test_pending_purged_after_ttl(self):
        adapter = _make_adapter()
        adapter._passive.enqueue("G", "stale")
        adapter._passive._pending["G"][0]["created_at"] -= 2000
        adapter._passive.purge()
        self.assertFalse(adapter._passive.has_pending("G"))
        self.assertEqual(adapter._passive.peek_pending("G"), [])

    def test_queue_mode_enqueues_without_request(self):
        server = FakeGroupServer()
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        adapter._passive.set_queue_mode("G")

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", "blocked", "group", msg_id="M1"))

        self.assertFalse(ok)
        self.assertEqual(len(server.requests), 0)
        self.assertTrue(adapter._passive.has_pending("G"))

    def test_synthetic_msg_id_not_treated_as_window(self):
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "scheduler_1700000000")
        self.assertEqual(adapter._passive.resolve("G"), "")
        self.assertEqual(adapter._passive.remaining("G"), 0)

    def test_empty_state_returns_false_without_raising(self):
        server = FakeGroupServer()
        adapter = _make_adapter()

        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", "nothing", "group", msg_id=""))

        self.assertFalse(ok)
        self.assertEqual(len(server.requests), 0)
        self.assertEqual(adapter._passive.resolve("G"), "")

    def test_media_sends_no_upload_without_window(self):
        adapter = _make_adapter()
        adapter.upload_media = mock.AsyncMock()

        result = asyncio.run(adapter.send_media_message(
            "G", "any.png", 1, is_group=True, msg_id="M1"
        ))

        self.assertFalse(result["success"])
        self.assertIn("暂无可用被动回复窗口", result["error"])
        adapter.upload_media.assert_not_awaited()

    def test_send_exception_keeps_content(self):
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")

        async def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        with mock.patch.object(adapter, "_send_group_message", _boom):
            ok = asyncio.run(adapter.send_message("G", "SAFE", "group", msg_id="M1"))

        self.assertFalse(ok)
        self.assertEqual(adapter._passive.peek_pending("G"), ["SAFE"])

    def test_media_marks_window_exhausted_on_expired_code(self):
        server = FakeGroupServer()
        server.status = 400
        server.err_code = 40034005
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        adapter.upload_media = mock.AsyncMock(return_value={"file_info": "FI"})

        with _patch_client(server):
            result = asyncio.run(adapter.send_media_message(
                "G", "a.png", 1, is_group=True, msg_id="M1"
            ))

        self.assertFalse(result["success"])
        self.assertEqual(adapter._passive.remaining("G"), 0)
        adapter.upload_media.assert_awaited_once()

    def test_purge_expired_window_clears_remaining(self):
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        adapter._passive._windows["G"]["received_at"] -= 700
        adapter._passive.purge()
        self.assertEqual(adapter._passive.remaining("G"), 0)
        self.assertEqual(adapter._passive.resolve("G"), "")

    def test_long_text_fits_one_chunk_and_one_reply(self):
        from app.channels.base import split_markdown

        server = FakeGroupServer()
        adapter = _make_adapter()
        adapter._passive.note_inbound("G", "M1")
        text = "a" * adapter.max_text_chunk

        self.assertEqual(len(split_markdown(text, max_len=adapter.max_text_chunk)), 1)
        with _patch_client(server):
            ok = asyncio.run(adapter.send_message("G", text, "group", msg_id="M1"))

        self.assertTrue(ok)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(adapter._passive.remaining("G"), 4)


if __name__ == "__main__":
    unittest.main()
