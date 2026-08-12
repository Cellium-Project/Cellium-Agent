import json
import logging
import re
from typing import Dict, List, Optional

import httpx

from .models import ChatResponse, ToolCall

logger = logging.getLogger(__name__)


class OpenAICompatTransport:

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 60,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._async_client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None
        self._async_client_loop: object = None  # AsyncClient 绑定的事件循环

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
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
        raise RuntimeError(f"LLM API 返回 {resp.status_code}: {body[:500]}")

    @staticmethod
    def _parse_json(resp):
        try:
            return resp.json()
        except Exception as e:
            try:
                preview = resp.text[:300]
            except Exception:
                preview = "<无法读取响应体>"
            raise RuntimeError(f"LLM API 返回非 JSON 响应: {preview}") from e

    async def chat(self, body: Dict) -> ChatResponse:
        client = self._ensure_async_client()
        resp = await client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json=body,
        )
        self._raise_for_status(resp)
        return self.parse_response(self._parse_json(resp))

    def chat_sync(self, body: Dict) -> ChatResponse:
        client = self._ensure_sync_client()
        resp = client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json=body,
        )
        self._raise_for_status(resp)
        return self.parse_response(self._parse_json(resp))

    async def chat_stream(self, body: Dict):
        client = self._ensure_async_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json={**body, "stream": True},
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
        client = self._ensure_sync_client()
        resp = client.get(f"{self.base_url}/models", headers=self._headers)
        self._raise_for_status(resp)
        data = self._parse_json(resp)
        if not isinstance(data, dict):
            return []
        return [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]

    async def aclose(self):
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    @staticmethod
    def parse_sse_text_response(raw_text: str) -> ChatResponse:
        lines = raw_text.splitlines()
        full_content = ""
        finish_reason = ""
        usage = {}
        model = ""

        for line in lines:
            raw = (line or "").strip()
            if not raw:
                continue
            if raw.startswith("data:"):
                raw = raw[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue

            if isinstance(item, dict):
                if not model:
                    model = item.get("model", "") or model
                choices = item.get("choices") or []
                if choices:
                    choice0 = choices[0] or {}
                    delta = choice0.get("delta") or {}
                    delta_content = delta.get("content") if isinstance(delta, dict) else None
                    if delta_content:
                        full_content += delta_content
                    if choice0.get("finish_reason"):
                        finish_reason = choice0.get("finish_reason") or finish_reason
                if item.get("usage"):
                    usage = item.get("usage") or usage

        return ChatResponse(
            content=full_content,
            tool_calls=[],
            model=model,
            finish_reason=finish_reason or "stop",
            usage=usage,
        )

    @classmethod
    def parse_response(cls, raw_response) -> ChatResponse:
        if isinstance(raw_response, str):
            raw = raw_response.strip()
            if raw.startswith("data:") and "\n" in raw_response:
                parsed_sse = cls.parse_sse_text_response(raw_response)
                logger.warning(
                    "[LLM] 收到整段 SSE 文本串响应，按兼容模式聚合解析 | content_len=%d | finish_reason=%s",
                    len(parsed_sse.content or ""),
                    parsed_sse.finish_reason,
                )
                return parsed_sse
            if raw.startswith("data:"):
                raw = raw[5:].strip()
            if raw == "[DONE]":
                return ChatResponse(content="", tool_calls=[], model="", finish_reason="stop", usage={})
            try:
                raw_response = json.loads(raw)
            except Exception:
                preview = raw[:300]
                logger.warning("[LLM] 收到字符串响应而非标准对象，按纯文本回复处理 | preview=%s", preview)
                return ChatResponse(
                    content=raw_response,
                    tool_calls=[],
                    model="",
                    finish_reason="stop",
                    usage={},
                )

        if isinstance(raw_response, dict):
            if isinstance(raw_response.get("choices"), list) and raw_response["choices"]:
                choice = raw_response["choices"][0] or {}
                message = choice.get("message", {}) or {}
                content = message.get("content", "")

                reasoning_content = None
                if message.get("reasoning_content"):
                    reasoning_content = message.get("reasoning_content")
                elif content:
                    think_match = re.search(r"<think>\s*(.*?)\s*</think>", content, re.DOTALL)
                    if think_match:
                        reasoning_content = think_match.group(1).strip()
                        before = content[:think_match.start()].strip()
                        after = content[think_match.end():].strip()
                        content = f"{before}\n{after}" if before and after else (before or after)

                tool_calls = []
                for tc in message.get("tool_calls") or []:
                    args = {}
                    args_text = (tc.get("function") or {}).get("arguments", "") or ""
                    if args_text:
                        try:
                            args = json.loads(args_text)
                        except Exception:
                            args = {"raw": args_text}
                    tool_calls.append(ToolCall(
                        id=tc.get("id", "") or "",
                        name=(tc.get("function") or {}).get("name", "") or "",
                        arguments=args,
                    ))

                usage = {}
                resp_usage = raw_response.get("usage") or {}
                if resp_usage:
                    usage = {
                        "prompt_tokens": resp_usage.get("prompt_tokens", 0),
                        "completion_tokens": resp_usage.get("completion_tokens", 0),
                        "total_tokens": resp_usage.get("total_tokens", 0),
                    }

                return ChatResponse(
                    content=content,
                    tool_calls=tool_calls,
                    model=raw_response.get("model", ""),
                    finish_reason=choice.get("finish_reason", "") or "",
                    usage=usage,
                    reasoning_content=reasoning_content,
                )

            if raw_response.get("choices") == [] and raw_response.get("usage"):
                return ChatResponse(
                    content="",
                    tool_calls=[],
                    model=raw_response.get("model", ""),
                    finish_reason="stop",
                    usage=raw_response.get("usage", {}) or {},
                )

            preview = str(raw_response)[:300]
            raise TypeError(f"LLM 返回 dict 但不包含标准 choices 结构: {preview}")

        preview = str(raw_response)[:300]
        raise TypeError(f"LLM 返回类型异常: {type(raw_response).__name__}, preview={preview}")
