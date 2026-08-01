import asyncio
import json
import unittest

import httpx

from app.agent.llm.engine import OpenAICompatibleEngine, _VERIFY_CACHE
from app.agent.llm.transport import OpenAICompatTransport


def _mock_async_transport(handler):
    t = OpenAICompatTransport(api_key="test-key", base_url="https://api.example.com/v1", timeout=30)
    t._async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=30)
    return t


def _mock_sync_transport(handler):
    t = OpenAICompatTransport(api_key="test-key", base_url="https://api.example.com/v1", timeout=30)
    t._sync_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=30)
    return t


def _chat_completion(content="你好", message_extra=None, finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    if message_extra:
        message.update(message_extra)
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class TestTransportChat(unittest.TestCase):

    def _assert_sent_body(self, request, expect_tool=False):
        body = json.loads(request.content)
        self.assertEqual(body["model"], "test-model")
        if body["messages"]:
            self.assertEqual(body["messages"][0]["content"], "你好")
        self.assertEqual(request.url.path, "/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer test-key")
        if expect_tool:
            self.assertIn("tools", body)

    def test_chat_basic(self):
        def handler(request):
            self._assert_sent_body(request)
            return httpx.Response(200, json=_chat_completion(content="收到"))

        t = _mock_async_transport(handler)
        resp = asyncio.run(t.chat({"model": "test-model", "messages": [{"role": "user", "content": "你好"}]}))
        self.assertEqual(resp.content, "收到")
        self.assertEqual(resp.model, "test-model")
        self.assertEqual(resp.finish_reason, "stop")
        self.assertEqual(resp.usage["total_tokens"], 15)

    def test_chat_with_tool_calls(self):
        def handler(request):
            self._assert_sent_body(request, expect_tool=True)
            tool_calls = [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell_tool", "arguments": '{"command": "ls"}'},
            }]
            return httpx.Response(200, json=_chat_completion(message_extra={"tool_calls": tool_calls}))

        t = _mock_async_transport(handler)
        resp = asyncio.run(t.chat({"model": "test-model", "messages": [], "tools": [{"type": "function"}]}))
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(resp.tool_calls[0].name, "shell_tool")
        self.assertEqual(resp.tool_calls[0].arguments, {"command": "ls"})

    def test_chat_reasoning_content(self):
        def handler(request):
            return httpx.Response(200, json=_chat_completion(message_extra={"reasoning_content": "思考中"}))

        t = _mock_async_transport(handler)
        resp = asyncio.run(t.chat({"model": "test-model", "messages": []}))
        self.assertEqual(resp.reasoning_content, "思考中")

    def test_chat_think_tags_stripped(self):
        def handler(request):
            return httpx.Response(200, json=_chat_completion(content="<think>内部推理</think>最终答案"))

        t = _mock_async_transport(handler)
        resp = asyncio.run(t.chat({"model": "test-model", "messages": []}))
        self.assertEqual(resp.reasoning_content, "内部推理")
        self.assertEqual(resp.content, "最终答案")

    def test_chat_sync(self):
        def handler(request):
            self._assert_sent_body(request)
            return httpx.Response(200, json=_chat_completion(content="同步回复"))

        t = _mock_sync_transport(handler)
        resp = t.chat_sync({"model": "test-model", "messages": [{"role": "user", "content": "你好"}]})
        self.assertEqual(resp.content, "同步回复")

    def test_chat_http_error_raises(self):
        def handler(request):
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})

        t = _mock_async_transport(handler)
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(t.chat({"model": "test-model", "messages": []}))
        self.assertIn("401", str(ctx.exception))

    def test_chat_empty_choices_with_usage(self):
        def handler(request):
            return httpx.Response(200, json={"id": "x", "model": "m", "choices": [], "usage": {"total_tokens": 3}})

        t = _mock_async_transport(handler)
        resp = asyncio.run(t.chat({"model": "test-model", "messages": []}))
        self.assertEqual(resp.content, "")
        self.assertEqual(resp.finish_reason, "stop")
        self.assertEqual(resp.usage["total_tokens"], 3)


class TestTransportStream(unittest.TestCase):

    def test_stream_yields_chunks(self):
        sse = (
            'data: {"id":"1","model":"m","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            'data: {"id":"2","model":"m","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}\n\n'
            'data: {"id":"3","model":"m","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}\n\n'
            'data: {"id":"4","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )

        def handler(request):
            self.assertIn("stream", json.loads(request.content))
            return httpx.Response(200, content=sse.encode(), headers={"Content-Type": "text/event-stream"})

        t = _mock_async_transport(handler)

        async def collect():
            return [c async for c in t.chat_stream({"model": "test-model", "messages": []})]

        chunks = asyncio.run(collect())
        self.assertEqual(len(chunks), 4)
        contents = [
            c["choices"][0]["delta"].get("content")
            for c in chunks
            if c["choices"] and (c["choices"][0].get("delta") or {}).get("content")
        ]
        self.assertEqual(contents, ["你", "好"])

    def test_stream_http_error(self):
        def handler(request):
            return httpx.Response(500, json={"error": {}})

        t = _mock_async_transport(handler)

        async def collect():
            return [c async for c in t.chat_stream({"model": "test-model", "messages": []})]

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(collect())
        self.assertIn("500", str(ctx.exception))


class TestTransportModels(unittest.TestCase):

    def test_list_models(self):
        def handler(request):
            self.assertEqual(request.url.path, "/v1/models")
            return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})

        t = _mock_sync_transport(handler)
        ids = t.list_models()
        self.assertEqual(ids, ["deepseek-chat", "deepseek-reasoner"])

    def test_list_models_filters_invalid_entries(self):
        def handler(request):
            return httpx.Response(200, json={"data": [{"id": "ok"}, {"no_id": True}, None, "str", {}]})

        t = _mock_sync_transport(handler)
        ids = t.list_models()
        self.assertEqual(ids, ["ok"])

    def test_list_models_non_dict_response(self):
        def handler(request):
            return httpx.Response(200, json=["not", "a", "dict"])

        t = _mock_sync_transport(handler)
        self.assertEqual(t.list_models(), [])

    def test_chat_non_json_response_raises(self):
        def handler(request):
            return httpx.Response(200, content=b"<html>Bad Gateway</html>", headers={"Content-Type": "text/html"})

        t = _mock_async_transport(handler)
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(t.chat({"model": "test-model", "messages": []}))
        self.assertIn("非 JSON", str(ctx.exception))


class TestParseResponse(unittest.TestCase):

    def test_plain_json_str(self):
        resp = OpenAICompatTransport.parse_response(json.dumps(_chat_completion(content="字符串JSON")))
        self.assertEqual(resp.content, "字符串JSON")

    def test_whole_sse_text(self):
        sse = (
            'data: {"model":"m","choices":[{"delta":{"content":"你"}}]}\n'
            'data: {"model":"m","choices":[{"delta":{"content":"好"}}]}\n'
            'data: [DONE]\n'
        )
        resp = OpenAICompatTransport.parse_response(sse)
        self.assertEqual(resp.content, "你好")
        self.assertEqual(resp.finish_reason, "stop")

    def test_done_only(self):
        resp = OpenAICompatTransport.parse_response("data: [DONE]")
        self.assertEqual(resp.content, "")
        self.assertEqual(resp.finish_reason, "stop")

    def test_plain_text(self):
        resp = OpenAICompatTransport.parse_response("纯文本回复")
        self.assertEqual(resp.content, "纯文本回复")

    def test_invalid_dict_raises(self):
        with self.assertRaises(TypeError):
            OpenAICompatTransport.parse_response({"foo": "bar"})

    def test_invalid_type_raises(self):
        with self.assertRaises(TypeError):
            OpenAICompatTransport.parse_response(12345)


class TestEngineIntegration(unittest.TestCase):

    def _make_engine(self):
        engine = OpenAICompatibleEngine(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="test-model",
            verify_model=False,
        )
        return engine

    def test_engine_chat_params(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_completion(content="你好"))

        engine = self._make_engine()
        engine._transport = _mock_async_transport(handler)

        resp = asyncio.run(engine.chat([{"role": "user", "content": "你好"}]))
        self.assertEqual(resp.content, "你好")
        body = captured["body"]
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["temperature"], 0.7)

    def test_engine_chat_thinking_merged(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_completion(content="ok"))

        engine = OpenAICompatibleEngine(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="qwen3-max",
            thinking=True,
            thinking_budget=2000,
            verify_model=False,
        )
        engine._transport = _mock_async_transport(handler)

        asyncio.run(engine.chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(captured["body"]["thinking"], {"type": "enabled", "budget_tokens": 2000})

    def test_engine_chat_no_api_key(self):
        engine = OpenAICompatibleEngine(api_key="", base_url="https://x/v1", model="m", verify_model=False)
        with self.assertRaises(ValueError):
            asyncio.run(engine.chat([{"role": "user", "content": "hi"}]))

    def test_engine_stream(self):
        sse = (
            'data: {"id":"1","model":"m","choices":[{"index":0,"delta":{"content":"流"},"finish_reason":null}]}\n\n'
            'data: {"id":"2","model":"m","choices":[{"index":0,"delta":{"content":"式"},"finish_reason":null}]}\n\n'
            'data: {"id":"3","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )

        def handler(request):
            return httpx.Response(200, content=sse.encode(), headers={"Content-Type": "text/event-stream"})

        engine = self._make_engine()
        engine._transport = _mock_async_transport(handler)

        async def _collect():
            return [e async for e in engine.chat_stream([{"role": "user", "content": "hi"}])]

        events = asyncio.run(_collect())
        types = [e["type"] for e in events]
        self.assertEqual(types, ["chunk", "chunk", "done"])
        self.assertEqual(events[-1]["full_content"], "流式")

    def test_engine_stream_omits_max_tokens(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            sse = 'data: {"id":"1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, content=sse.encode(), headers={"Content-Type": "text/event-stream"})

        engine = OpenAICompatibleEngine(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="test-model",
            omit_max_tokens=True,
            verify_model=False,
        )
        engine._transport = _mock_async_transport(handler)

        async def _collect():
            return [e async for e in engine.chat_stream([{"role": "user", "content": "hi"}])]

        asyncio.run(_collect())
        self.assertNotIn("max_tokens", captured["body"])

    def test_engine_stream_empty_no_raise(self):
        sse = 'data: {"id":"1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

        def handler(request):
            return httpx.Response(200, content=sse.encode(), headers={"Content-Type": "text/event-stream"})

        engine = self._make_engine()
        engine._transport = _mock_async_transport(handler)

        async def _collect():
            return [e async for e in engine.chat_stream([{"role": "user", "content": "hi"}])]

        events = asyncio.run(_collect())
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["full_content"], "")

    def test_engine_stream_tool_calls_accumulated(self):
        sse = (
            'data: {"id":"1","model":"m","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            'data: {"id":"2","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"shell_tool","arguments":""}}]},"finish_reason":null}]}\n\n'
            'data: {"id":"3","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\""}}]},"finish_reason":null}]}\n\n'
            'data: {"id":"4","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":": \\"ls\\"}"}}]},"finish_reason":null}]}\n\n'
            'data: {"id":"5","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            "data: [DONE]\n\n"
        )

        def handler(request):
            return httpx.Response(200, content=sse.encode(), headers={"Content-Type": "text/event-stream"})

        engine = self._make_engine()
        engine._transport = _mock_async_transport(handler)

        async def _collect():
            return [e async for e in engine.chat_stream([{"role": "user", "content": "hi"}])]

        events = asyncio.run(_collect())
        self.assertEqual(events[-1]["type"], "done")
        tool_calls = events[-1]["tool_calls"]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].id, "call_1")
        self.assertEqual(tool_calls[0].name, "shell_tool")
        self.assertEqual(tool_calls[0].arguments, {"command": "ls"})

    def test_engine_verify_model_exists(self):
        def handler(request):
            return httpx.Response(200, json={"data": [{"id": "test-model"}, {"id": "other-model"}]})

        engine = self._make_engine()
        engine._transport = _mock_sync_transport(handler)
        engine._verify_model = True
        _VERIFY_CACHE.clear()

        self.assertTrue(engine.verify_model_exists())

    def test_engine_verify_model_missing(self):
        def handler(request):
            return httpx.Response(200, json={"data": [{"id": "other-model"}]})

        engine = self._make_engine()
        engine._transport = _mock_sync_transport(handler)
        engine._verify_model = True
        _VERIFY_CACHE.clear()

        self.assertFalse(engine.verify_model_exists())


if __name__ == "__main__":
    unittest.main()
