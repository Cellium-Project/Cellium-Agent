import asyncio
import unittest
from unittest import mock
from unittest.mock import AsyncMock

import httpx

from app.agent.llm.engine import OpenAICompatibleEngine
from app.agent.llm.transport import OpenAICompatTransport, RetryableAPIError


def _chunk(text):
    return {"choices": [{"delta": {"content": text}}]}


class _FakeTransport:

    def __init__(self, items_or_error):
        self._items_or_error = items_or_error
        self.calls = 0

    def chat_stream(self, params):
        self.calls += 1
        spec = self._items_or_error
        if callable(spec):
            spec = spec(self.calls)
        if isinstance(spec, Exception):
            return self._fail(spec)
        return self._stream(spec)

    @staticmethod
    async def _fail(error):
        raise error
        yield None

    @staticmethod
    async def _stream(items):
        for item in items:
            if isinstance(item, Exception):
                raise item
            yield item


def _engine(fake):
    engine = OpenAICompatibleEngine(
        api_key="k", base_url="https://x/v1", model="m", verify_model=False
    )
    engine._transport = fake
    return engine


async def _collect(engine):
    events = []
    async for event in engine.chat_stream([{"role": "user", "content": "hi"}]):
        events.append(event)
    return events


def _content_text(events):
    return "".join(e.get("text", "") for e in events if e.get("type") == "content")


class TestStreamInterrupt(unittest.TestCase):

    def test_mid_stream_read_timeout_keeps_content(self):
        fake = _FakeTransport([_chunk("你"), _chunk("好"), httpx.ReadTimeout("x")])
        events = asyncio.run(_collect(_engine(fake)))
        self.assertEqual(_content_text(events), "你好")
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(events[-1]["response"].interrupted)
        self.assertEqual(events[-1]["response"].finish_reason, "interrupted")
        self.assertEqual(fake.calls, 1)

    def test_first_chunk_read_timeout_raises(self):
        fake = _FakeTransport(httpx.ReadTimeout("x"))
        with mock.patch("app.agent.llm.engine.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(httpx.ReadTimeout):
                asyncio.run(_collect(_engine(fake)))

    def test_mid_stream_read_error_keeps_content(self):
        fake = _FakeTransport([_chunk("半"), _chunk("截"), httpx.ReadError("reset")])
        events = asyncio.run(_collect(_engine(fake)))
        self.assertEqual(_content_text(events), "半截")
        self.assertTrue(events[-1]["response"].interrupted)
        self.assertEqual(fake.calls, 1)

    def test_retry_on_429(self):
        fake = _FakeTransport(
            lambda n: RetryableAPIError(429) if n == 1 else [_chunk("OK")]
        )
        with mock.patch("app.agent.llm.engine.asyncio.sleep", new=AsyncMock()):
            events = asyncio.run(_collect(_engine(fake)))
        self.assertEqual(_content_text(events), "OK")
        self.assertEqual(fake.calls, 2)

    def test_three_retryable_errors_raises(self):
        fake = _FakeTransport(RetryableAPIError(503))
        with mock.patch("app.agent.llm.engine.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(RetryableAPIError):
                asyncio.run(_collect(_engine(fake)))
        self.assertEqual(fake.calls, 3)

    def test_interrupted_partial_tool_call_is_dropped(self):
        fake = _FakeTransport([
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "c1",
                "function": {"name": "shell", "arguments": "{\"cmd\":"},
            }]}}]},
            httpx.ReadTimeout("x"),
        ])
        events = asyncio.run(_collect(_engine(fake)))
        response = events[-1]["response"]
        self.assertTrue(response.interrupted)
        self.assertEqual(response.tool_calls, [])
        self.assertFalse(any(e.get("type") == "tool_calls" for e in events))


class TestRaiseForStatus(unittest.TestCase):

    def test_429_is_retryable_with_retry_after(self):
        resp = httpx.Response(429, headers={"retry-after": "3"}, text="busy")
        with self.assertRaises(RetryableAPIError) as ctx:
            OpenAICompatTransport._raise_for_status(resp)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.retry_after, 3.0)

    def test_503_is_retryable(self):
        with self.assertRaises(RetryableAPIError) as ctx:
            OpenAICompatTransport._raise_for_status(httpx.Response(503))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_401_is_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            OpenAICompatTransport._raise_for_status(httpx.Response(401, text="no"))
        self.assertNotIsInstance(ctx.exception, RetryableAPIError)


if __name__ == "__main__":
    unittest.main()
