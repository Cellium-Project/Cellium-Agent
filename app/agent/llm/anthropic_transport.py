# -*- coding: utf-8 -*-

import json
import logging
import re
from typing import AsyncIterator, Dict, List, Optional

import httpx

from .models import ChatResponse, ToolCall
from .transport import OpenAICompatTransport

logger = logging.getLogger(__name__)


class AnthropicTransport:

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.anthropic.com",
        timeout: int = 120,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._async_client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None
        self._async_client_loop: object = None

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _ensure_async_client(self) -> httpx.AsyncClient:
        import asyncio
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._async_client is not None and self._async_client_loop is not None and self._async_client_loop is not current_loop:
            try:
                if not self._async_client_loop.is_closed():
                    fut = asyncio.run_coroutine_threadsafe(
                        self._async_client.aclose(), self._async_client_loop
                    )
                    fut.result(timeout=5)
            except Exception:
                pass
            self._async_client = None
            self._async_client_loop = None
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.timeout)
            self._async_client_loop = current_loop
        return self._async_client

    def _ensure_sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(timeout=self.timeout)
        return self._sync_client

    @staticmethod
    def _raise_for_status(resp):
        if resp.status_code < 400:
            return
        try:
            body = resp.text
        except Exception:
            body = ""
        raise RuntimeError(f"Anthropic API 返回 {resp.status_code}: {body[:500]}")

    @staticmethod
    def _parse_json(resp):
        try:
            return resp.json()
        except Exception as e:
            try:
                preview = resp.text[:300]
            except Exception:
                preview = "<无法读取响应体>"
            raise RuntimeError(f"Anthropic API 返回非 JSON 响应: {preview}") from e

    @classmethod
    def _convert_tools(cls, tools: List[Dict]) -> List[Dict]:
        anthropic_tools = []
        for tool in tools or []:
            fn = tool.get("function") or {}
            anthropic_tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools

    @classmethod
    def build_request_body(cls, body: Dict) -> Dict:
        messages = body.get("messages", [])
        system = "\n\n".join(
            m["content"] for m in messages if m["role"] == "system" and m.get("content")
        )

        anthropic_messages = []
        for m in messages:
            if m["role"] == "system":
                continue
            content = m.get("content", "")
            role = "assistant" if m["role"] == "assistant" else "user"
            anthropic_messages.append({"role": role, "content": content})

        result: Dict = {
            "model": body.get("model", ""),
            "messages": anthropic_messages,
            "max_tokens": body.get("max_tokens", 8192),
        }
        if system:
            result["system"] = system

        tools = body.get("tools")
        if tools:
            result["tools"] = cls._convert_tools(tools)

        temperature = body.get("temperature")
        if temperature is not None:
            result["temperature"] = temperature

        thinking = body.get("thinking")
        if thinking:
            result["thinking"] = thinking

        return result

    @classmethod
    def parse_response(cls, raw: dict) -> ChatResponse:
        content = ""
        tool_calls: List[ToolCall] = []
        reasoning_content = None
        stop_reason = raw.get("stop_reason", "")

        for block in raw.get("content", []) or []:
            btype = block.get("type")
            if btype == "text":
                content += block.get("text", "")
            elif btype == "thinking":
                reasoning_content = (reasoning_content or "") + block.get("thinking", "")
            elif btype == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", "") or "",
                    name=block.get("name", "") or "",
                    arguments=block.get("input", {}) or {},
                ))

        usage = {}
        u = raw.get("usage") or {}
        if u:
            usage = {
                "prompt_tokens": u.get("input_tokens", 0),
                "completion_tokens": u.get("output_tokens", 0),
                "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0),
            }

        finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls"}.get(stop_reason, stop_reason or "stop")

        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            model=raw.get("model", ""),
            finish_reason=finish,
            usage=usage,
            reasoning_content=reasoning_content,
        )

    @classmethod
    def parse_stream_event(cls, item: dict, content_parts, reasoning_parts, tool_calls_map, delta_reasoning_frag):
        evt_type = item.get("type")

        if evt_type == "message_start":
            return delta_reasoning_frag
        if evt_type == "message_delta":
            return delta_reasoning_frag
        if evt_type == "message_stop":
            return delta_reasoning_frag

        if evt_type == "content_block_delta":
            delta = item.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                content_parts.append(delta.get("text", ""))
            elif dtype == "thinking_delta":
                reasoning_parts.append(delta.get("thinking", ""))
            return delta_reasoning_frag

        if evt_type == "content_block_start":
            block = item.get("content_block") or {}
            if block.get("type") == "tool_use":
                idx = item.get("index", 0)
                tool_calls_map.setdefault(idx, {
                    "id": block.get("id", "") or "",
                    "name": block.get("name", "") or "",
                    "arguments": "",
                })
            return delta_reasoning_frag

        if evt_type == "content_block_stop":
            return delta_reasoning_frag

        if evt_type == "ping":
            return delta_reasoning_frag

        return delta_reasoning_frag

    async def chat(self, body: Dict) -> ChatResponse:
        client = self._ensure_async_client()
        resp = await client.post(
            f"{self.base_url}/v1/messages",
            headers=self._headers,
            json=self.build_request_body(body),
        )
        self._raise_for_status(resp)
        return self.parse_response(self._parse_json(resp))

    def chat_sync(self, body: Dict) -> ChatResponse:
        client = self._ensure_sync_client()
        resp = client.post(
            f"{self.base_url}/v1/messages",
            headers=self._headers,
            json=self.build_request_body(body),
        )
        self._raise_for_status(resp)
        return self.parse_response(self._parse_json(resp))

    async def chat_stream(self, body: Dict):
        client = self._ensure_async_client()
        request_body = self.build_request_body(body)
        request_body["stream"] = True
        async with client.stream(
            "POST",
            f"{self.base_url}/v1/messages",
            headers=self._headers,
            json=request_body,
        ) as resp:
            self._raise_for_status(resp)
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except Exception:
                    continue

    def list_models(self) -> List[str]:
        return []

    async def aclose(self):
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    @classmethod
    def aggregate_stream(cls, events) -> ChatResponse:
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls_map: Dict[int, dict] = {}
        finish_reason = ""
        usage = {}
        model_name = ""

        for item in events:
            evt_type = item.get("type")
            if evt_type == "message_start":
                msg = item.get("message") or {}
                model_name = msg.get("model", "") or model_name
            elif evt_type == "message_delta":
                finish_reason = item.get("delta", {}).get("stop_reason", "") or finish_reason
                u = item.get("usage") or {}
                if u:
                    usage = {
                        "prompt_tokens": u.get("input_tokens", 0),
                        "completion_tokens": u.get("output_tokens", 0),
                        "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0),
                    }
            elif evt_type == "content_block_start":
                block = item.get("content_block") or {}
                if block.get("type") == "tool_use":
                    idx = item.get("index", 0)
                    tool_calls_map.setdefault(idx, {
                        "id": block.get("id", "") or "",
                        "name": block.get("name", "") or "",
                        "arguments": "",
                    })
            elif evt_type == "content_block_delta":
                delta = item.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    content_parts.append(delta.get("text", ""))
                elif dtype == "thinking_delta":
                    reasoning_parts.append(delta.get("thinking", ""))

        calls = []
        for idx in sorted(tool_calls_map):
            entry = tool_calls_map[idx]
            args = {}
            if entry["arguments"]:
                try:
                    args = json.loads(entry["arguments"])
                except Exception:
                    args = {"raw": entry["arguments"]}
            calls.append(ToolCall(id=entry["id"], name=entry["name"], arguments=args))

        return ChatResponse(
            content="".join(content_parts),
            tool_calls=calls,
            model=model_name,
            finish_reason=finish_reason or "stop",
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )
