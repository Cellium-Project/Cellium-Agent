# -*- coding: utf-8 -*-
"""LLM 网络异常描述与无输出重试"""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.util.error_text import describe_error
from app.agent.prompt.diff import PromptDiffTracker


class TestDescribeError(unittest.TestCase):

    def test_read_error_not_blank(self):
        text = describe_error(httpx.ReadError(""))
        self.assertIn("网络中断", text)
        self.assertIn("ReadError", text)
        self.assertIn("请稍后重试", text)

    def test_unknown_empty_error_falls_back_to_type(self):
        class Boom(Exception):
            pass
        self.assertEqual(describe_error(Boom()), "Boom")

    def test_normal_error_keeps_message(self):
        self.assertIn("bad input", describe_error(ValueError("bad input")))


class _FlakyEngine:
    def __init__(self, fail_times=2, fail_after_yield=False):
        self.calls = 0
        self.fail_times = fail_times
        self.fail_after_yield = fail_after_yield

    async def chat_stream(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            if self.fail_after_yield:
                yield {"type": "content", "text": "partial"}
            raise httpx.ReadError("")
        yield {"type": "content", "text": "ok"}
        yield {"type": "done", "response": SimpleNamespace(content="ok")}


class TestStreamRetry(unittest.TestCase):

    def _collect(self, engine):
        tracker = PromptDiffTracker()

        async def run():
            return [c async for c in tracker.chat_stream(
                llm_engine=engine, messages=[{"role": "user", "content": "hi"}]
            )]

        return asyncio.run(run())

    def test_retries_when_nothing_yielded(self):
        engine = _FlakyEngine(fail_times=2)
        with patch("app.agent.prompt.diff._STREAM_RETRY_BACKOFFS", (0, 0, 0)):
            chunks = self._collect(engine)
        self.assertEqual(engine.calls, 3)
        self.assertTrue(any(c.get("text") == "ok" for c in chunks))

    def test_no_retry_after_partial_output(self):
        engine = _FlakyEngine(fail_times=1, fail_after_yield=True)
        with patch("app.agent.prompt.diff._STREAM_RETRY_BACKOFFS", (0, 0, 0)):
            with self.assertRaises(httpx.ReadError):
                self._collect(engine)
        self.assertEqual(engine.calls, 1)


if __name__ == "__main__":
    unittest.main()
