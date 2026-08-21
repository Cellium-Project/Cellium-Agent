# -*- coding: utf-8 -*-
"""LLM 引擎"""

import json
import logging
import re
import time

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from .models import ChatResponse, ModelInfo, ToolCall
from .transport import OpenAICompatTransport
from .anthropic_transport import AnthropicTransport
from app.messaging.stream_normalizer import _THINK_PREFIXES, _think_fragment_tail

logger = logging.getLogger(__name__)

_EMPTY_RESPONSE_COUNT = 0

_VERIFY_CACHE: Dict[str, bool] = {}

# ---- 默认模型注册表（内置兜底）----
_DEFAULT_MODEL_REGISTRY: Dict[str, ModelInfo] = {
    "default": ModelInfo(8192, 4096, True),
}

def _load_model_registry() -> Dict[str, ModelInfo]:
    """从配置文件加载模型注册表，失败时使用内置默认值"""
    import yaml
    from pathlib import Path

    registry_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "agent" / "model_registry.yaml"

    try:
        if registry_path.exists():
            with open(registry_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            models = data.get("models", {}) or {}
            result = {}
            for name, values in models.items():
                if isinstance(values, (list, tuple)) and len(values) >= 2:
                    result[str(name)] = ModelInfo(
                        context_window=int(values[0]),
                        max_output_tokens=int(values[1]),
                        supports_vision=bool(values[2]) if len(values) >= 3 else False,
                    )
            if result:
                logger.info("[LLM] 从 %s 加载了 %d 个模型", registry_path, len(result))
                return result
    except Exception as e:
        logger.warning("[LLM] 加载模型注册表失败，使用内置默认值: %s", e)

    return dict(_DEFAULT_MODEL_REGISTRY)

_MODEL_REGISTRY: Optional[Dict[str, ModelInfo]] = None


def _get_model_registry() -> Dict[str, ModelInfo]:
    global _MODEL_REGISTRY
    if _MODEL_REGISTRY is None:
        _MODEL_REGISTRY = _load_model_registry()
    return _MODEL_REGISTRY


def _match_model(model_name: str) -> Optional[ModelInfo]:
    if not model_name:
        return None

    registry = _get_model_registry()
    key = model_name.strip().lower()

    if key in registry:
        return registry[key]

    for reg_key, info in registry.items():
        if key.startswith(reg_key):
            logger.info("[LLM] 模型 '%s' 前缀匹配到注册项 '%s'", model_name, reg_key)
            return info

    base = key.split(":")[0]
    for reg_key, info in registry.items():
        reg_base = reg_key.split(":")[0]
        if base == reg_base or reg_key.startswith(base + ":"):
            logger.info("[LLM] 模型 '%s' Ollama-tag 匹配到 '%s'", model_name, reg_key)
            return info

    logger.warning(
        "[LLM] 模型 '%s' 不在内置数据库中，使用保守默认值 "
        "(context=8192, max_output=4096)。如需精确值请在 config/agent/model_registry.yaml 中配置。",
        model_name,
    )
    return None


# ============================================================
#  Token 预估器 
# ============================================================

def _estimate_tokens(text: str) -> int:
    """
    粗略估算文本的 token 数

    规则:
      - 中文 ≈ 1.5~2 字符/token（取 1.75）
      - 英文 ≈ 4 字符/token（标准 BPE 平均值）
      - 混合文本按比例加权
      - 误差范围 ±20%，仅用于超限预警，不用于精确计费
    """
    if not text:
        return 0

    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or
                        '\u3000' <= c <= '\u303f' or
                        '\uff00' <= c <= '\uffef')
    other_chars = len(text) - chinese_chars

    # 中文 ~1.75 char/token, 其他 ~4 char/token
    return int(chinese_chars / 1.75 + other_chars / 4.0) + 1


def _estimate_messages_tokens(messages: List[Dict]) -> int:
    """估算消息列表总 token 数（含格式开销）"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "")
        total += _estimate_tokens(content) + 4
    total = int(total * 1.1)
    return max(total, 10)




# ============================================================
#  抽象基类
# ============================================================

class BaseLLMEngine(ABC):
    """LLM 引擎抽象基类 — AgentLoop 只依赖此接口"""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = None,
        max_tokens: int = None,
        **kwargs,
    ) -> ChatResponse:
        ...

    @property
    @abstractmethod
    def model_info(self) -> ModelInfo:
        """当前模型的能力信息"""

    @property
    @abstractmethod
    def context_window(self) -> int:
        """当前模型的最大上下文窗口"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""

    async def close(self):
        """释放底层资源（httpx 连接池等）。默认无操作，子类按需覆盖。"""


# ============================================================
#  OpenAI 兼容引擎
# ============================================================

class OpenAICompatibleEngine(BaseLLMEngine):

    # 保守默认值（未知模型时的安全底线）
    DEFAULT_CONTEXT_WINDOW = 128000
    DEFAULT_MAX_OUTPUT = 16384

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = None,      
        timeout: int = 60,
        context_window: int = None,  
        verify_model: bool = True,
        omit_max_tokens: bool = True,
        thinking: bool = False,
        thinking_budget: int = None,
        vision: bool = False,
        provider: str = "openai",
        **kwargs,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.provider = self._detect_provider(provider, self.base_url, model)
        self.base_url = self._normalize_base_url(self.base_url, self.provider)
        self._extra_client_args = kwargs
        self._verify_model = verify_model
        self._verify_deferred = False
        self._verify_done = False
        self._omit_max_tokens = omit_max_tokens
        self._thinking = thinking
        self._thinking_budget = thinking_budget

        detected = _match_model(model)

        self._model_info = ModelInfo(
            context_window=context_window or (detected.context_window if detected else self.DEFAULT_CONTEXT_WINDOW),
            max_output_tokens=max_tokens or (detected.max_output_tokens if detected else self.DEFAULT_MAX_OUTPUT),
            supports_vision=vision or (detected.supports_vision if detected else False),
        )

        self._calibrated = False
        self._actual_token_ratio = None  

        self._explicit_max_tokens = max_tokens

        self._transport: Optional[OpenAICompatTransport] = None

        max_tokens_hint = "(API自定)" if omit_max_tokens else str(self._model_info.max_output_tokens)
        logger.info(
            "[LLM] 引擎初始化 | model=%s | ctx=%d | max_out=%s | url=%s | 能力来源=%s",
            model, self._model_info.context_window,
            max_tokens_hint, base_url[:50],
            "显式传入" if (context_window or max_tokens)
            else ("内置注册表" if detected
                  else "保守默认值(待校准)"),
        )

    @property
    def model_info(self) -> ModelInfo:
        return self._model_info

    @property
    def context_window(self) -> int:
        return self._model_info.context_window

    @property
    def effective_max_tokens(self) -> int:
        """实际生效的 max_tokens（考虑上下文剩余空间）"""
        return self._model_info.max_output_tokens

    def estimate_tokens_calibrated(self, messages: List[Dict], tools: List = None) -> int:
        """估算 token 数"""
        est = _estimate_messages_tokens(messages)
        if tools:
            est += len(tools) * 150
        if self._actual_token_ratio and self._actual_token_ratio > 0:
            est = int(est * self._actual_token_ratio)
        return est

    @property
    def max_tokens(self) -> int:
        return self._model_info.max_output_tokens

    def _build_params(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = None,
        max_tokens: int = None,
        truncate: bool = True,
        **kwargs,
    ) -> Dict:
        if not self.api_key:
            raise ValueError("LLM 引擎未配置有效的 API key，请在设置中配置后重试")

        effective_messages = messages
        if truncate and messages:
            effective_messages, was_truncated = self._truncate_if_needed(messages, tools)
            if was_truncated:
                logger.info(
                    "[LLM] 输入已截断以适应 %d 上下文窗口",
                    self.context_window,
                )

        final_max_tokens = self._resolve_max_tokens(
            effective_messages, tools, max_tokens
        )

        params = {
            "model": self.model,
            "messages": effective_messages,
            "temperature": temperature or self.temperature,
        }

        if not self._omit_max_tokens:
            params["max_tokens"] = final_max_tokens

        if tools:
            params["tools"] = tools

        if kwargs:
            params.update(kwargs)

        if self._thinking:
            model_lower = self.model.lower()
            if any(m in model_lower for m in ["o1", "o3", "o4"]):
                extra_body = {"thinking": {"type": "enabled"}}
                if self._thinking_budget:
                    extra_body["thinking"]["budget_tokens"] = self._thinking_budget
                params.update(extra_body)
                logger.info("[LLM] 思考模式 | model=%s | type=OpenAI-o系列 | budget=%s", self.model, self._thinking_budget or "默认")
            elif "deepseek" in model_lower and "reasoner" in model_lower:
                logger.info("[LLM] 思考模式 | model=%s | type=DeepSeek-Reasoner", self.model)
            else:
                logger.info("[LLM] 思考模式 | model=%s | type=第三方模型(传递thinking参数) | budget=%s", self.model, self._thinking_budget or "默认")
                extra_body = {"thinking": {"type": "enabled"}}
                if self._thinking_budget:
                    extra_body["thinking"]["budget_tokens"] = self._thinking_budget
                params.update(extra_body)

        return params

    @staticmethod
    def _detect_provider(provider: str, base_url: str, model: str) -> str:
        provider = (provider or "openai").lower()
        if provider == "anthropic":
            return "anthropic"
        
        url_lower = (base_url or "").lower()
        if "/anthropic" in url_lower or "/v1/messages" in url_lower:
            return "anthropic"
        
        model_lower = (model or "").lower()
        if model_lower.startswith("claude"):
            return "anthropic"
        
        return "openai"
    
    @staticmethod
    def _normalize_base_url(base_url: str, provider: str) -> str:
        url = (base_url or "").rstrip("/")
        
        if provider == "anthropic":
            if url.endswith("/v1/messages"):
                url = url.rsplit("/v1/messages", 1)[0]
            elif url.endswith("/messages"):
                url = url.rsplit("/messages", 1)[0]
            return url
        
        if url.endswith("/chat/completions"):
            url = url.rsplit("/chat/completions", 1)[0]
        
        return url

    def _ensure_transport(self):
        if self._transport is None:
            if self.provider == "anthropic":
                self._transport = AnthropicTransport(
                    api_key=self.api_key,
                    base_url=self.base_url or "https://api.anthropic.com",
                    timeout=self.timeout,
                )
            else:
                self._transport = OpenAICompatTransport(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                )
        return self._transport

    async def close(self):
        if self._transport is not None:
            await self._transport.aclose()
            self._transport = None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = None,
        max_tokens: int = None,
        truncate: bool = True,
        **kwargs,
    ) -> ChatResponse:
        """
        异步调用 LLM

        Args:
            messages: 消息列表
            tools: 工具定义
            temperature: 温度覆盖
            max_tokens: 最大输出 token 覆盖（None → 用模型默认值）
            truncate: 是否在输入超限时自动截断早期消息
        """
        if not self.api_key:
            raise ValueError("LLM 引擎未配置有效的 API key，请在设置中配置后重试")
        transport = self._ensure_transport()

        params = self._build_params(
            messages, tools, temperature, max_tokens, truncate, **kwargs
        )
        effective_messages = params["messages"]
        final_max_tokens = params.get("max_tokens")

        req_tokens = self.estimate_tokens_calibrated(effective_messages, tools)
        tools_count = len(tools) if tools else 0
        max_tokens_display = "API自定" if self._omit_max_tokens else str(final_max_tokens)
        logger.info(
            "[LLM] >>> 调用开始 | model=%s | 消息数=%d | 预估输入≈%d tokens | max_tokens=%s | tools=%d | temperature=%s",
            self.model, len(effective_messages), req_tokens,
            max_tokens_display, tools_count,
            params.get("temperature", "N/A"),
        )

        t0 = time.monotonic()
        try:
            parsed = await transport.chat(params)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "[LLM] <<< 调用失败 | model=%s | 耗时=%.0fms | 错误类型=%s | 详情: %s",
                self.model, elapsed_ms, type(e).__name__, str(e),
            )
            raise

        usage_info = (
            f"prompt={parsed.usage.get('prompt_tokens', '?')}, "
            f"completion={parsed.usage.get('completion_tokens', '?')}, "
            f"total={parsed.usage.get('total_tokens', '?')}"
        )
        logger.info(
            "[LLM] <<< 调用成功 | model=%s | 耗时=%.0fms | finish_reason=%s | "
            "tool_calls=%d | content长度=%d | %s | response_type=%s",
            self.model, elapsed_ms, parsed.finish_reason,
            len(parsed.tool_calls),
            len(parsed.content or ""), usage_info,
            type(parsed).__name__,
        )

        if parsed.reasoning_content:
            logger.info(
                "[LLM] <<< 思考内容 | model=%s | 长度=%d | 内容预览: %s",
                self.model,
                len(parsed.reasoning_content),
                parsed.reasoning_content[:300],
            )

        if not parsed.content and not parsed.tool_calls:
            global _EMPTY_RESPONSE_COUNT
            _EMPTY_RESPONSE_COUNT += 1
            
            tool_names = [t.get("function", {}).get("name", "?") for t in (tools or [])]
            
            logger.warning(
                "[LLM] 空响应 #%d | model=%s | base_url=%s | response_type=%s | "
                "req_tokens≈%d | max_tokens=%d | tools=%d(%s) | finish_reason=%s | "
                "usage=%s | temperature=%.2f | preview=%s",
                _EMPTY_RESPONSE_COUNT,
                self.model,
                getattr(self, 'base_url', '?'),
                type(parsed).__name__,
                req_tokens,
                final_max_tokens,
                tools_count,
                tool_names,
                parsed.finish_reason,
                parsed.usage,
                self.temperature,
                str(parsed)[:500],
            )
            
            if _EMPTY_RESPONSE_COUNT % 5 == 0:
                logger.error(
                    "[LLM] 空响应累计 %d 次，可能存在模型配置或 API 问题！",
                    _EMPTY_RESPONSE_COUNT,
                )
            
            return ChatResponse(
                content="",
                tool_calls=[],
                finish_reason=parsed.finish_reason or "stop",
                usage=parsed.usage or {"prompt_tokens": req_tokens, "completion_tokens": 0, "total_tokens": req_tokens},
            )

        if parsed.tool_calls:
            for tc in parsed.tool_calls:
                logger.info(
                    "[LLM]    └─ tool_call: id=%s | name=%s | args_keys=%s",
                    tc.id[:12], tc.name, list(tc.arguments.keys()),
                )

        if parsed.usage:
            self._calibrate_from_usage(parsed.usage, req_tokens)

        return parsed

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = None,
        max_tokens: int = None,
        truncate: bool = True,
        **kwargs,
    ):
        """
        流式调用 LLM — 逐 chunk 产出

        Yields:
            dict: 每个 chunk
                - {"type": "content", "text": str}      内容增量
                - {"type": "reasoning", "text": str}    思考增量
                - {"type": "tool_calls", "calls": [...]} 工具调用（最终聚合）
                - {"type": "done", "response": ChatResponse} 最终聚合响应
        """
        if not self.api_key:
            raise ValueError("LLM 引擎未配置有效的 API key，请在设置中配置后重试")
        transport = self._ensure_transport()

        params = self._build_params(
            messages, tools, temperature, max_tokens, truncate, **kwargs
        )
        effective_messages = params["messages"]

        req_tokens = self.estimate_tokens_calibrated(effective_messages, tools)
        logger.info(
            "[LLM] >>> 流式调用开始 | model=%s | 消息数=%d | 预估输入≈%d tokens | tools=%d",
            self.model, len(effective_messages), req_tokens,
            len(tools) if tools else 0,
        )

        if self.provider == "anthropic":
            async for result in self._chat_stream_anthropic(transport, params, req_tokens):
                yield result
            return

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls_map: Dict[int, dict] = {}
        finish_reason = ""
        usage = {}
        model_name = ""

        in_think = False
        think_frag = ""
        _reasoning_start_sent = False

        try:
            async for item in transport.chat_stream(params):
                choices = item.get("choices") or []
                if not choices:
                    if item.get("usage"):
                        usage = item.get("usage")
                    continue
                choice0 = choices[0] or {}
                delta = choice0.get("delta") or {}
                if not model_name:
                    model_name = item.get("model", "") or model_name

                delta_content = delta.get("content")
                if delta_content:
                    if think_frag:
                        delta_content = think_frag + delta_content
                        think_frag = ""
                    rest = delta_content
                    while rest:
                        if not in_think:
                            tag = re.search(r"<think>", rest)
                            if tag:
                                before = rest[:tag.start()]
                                if before:
                                    content_parts.append(before)
                                    yield {"type": "content", "text": before}
                                rest = rest[tag.end():]
                                in_think = True
                                # 立即触发原生思考动画（青色 spinner + 实时耗时）
                                yield {"type": "reasoning", "text": "", "start_time": time.time()}
                                continue
                            frag = _think_fragment_tail(rest)
                            if frag:
                                keep = rest[:-len(frag)] if frag != rest else ""
                                if keep:
                                    content_parts.append(keep)
                                    yield {"type": "content", "text": keep}
                                think_frag = frag
                                rest = ""
                                break
                            if rest:
                                content_parts.append(rest)
                                yield {"type": "content", "text": rest}
                            rest = ""
                        else:
                            close = re.search(r"</think>", rest)
                            if close:
                                chunk_text = rest[:close.start()]
                                if chunk_text:
                                    reasoning_parts.append(chunk_text)
                                    yield {"type": "reasoning", "text": chunk_text}
                                rest = rest[close.end():]
                                in_think = False
                                continue
                            frag = _think_fragment_tail(rest)
                            if frag:
                                keep = rest[:-len(frag)] if frag != rest else ""
                                if keep:
                                    reasoning_parts.append(keep)
                                    yield {"type": "reasoning", "text": keep}
                                think_frag = frag
                                rest = ""
                                break
                            if rest:
                                reasoning_parts.append(rest)
                                yield {"type": "reasoning", "text": rest}
                            rest = ""

                delta_reasoning = delta.get("reasoning_content")
                if delta_reasoning:
                    reasoning_parts.append(delta_reasoning)
                    chunk = {"type": "reasoning", "text": delta_reasoning}
                    if not _reasoning_start_sent:
                        chunk["start_time"] = time.time()
                        _reasoning_start_sent = True
                    yield chunk

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    entry = tool_calls_map.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["arguments"] += fn["arguments"]

                if choice0.get("finish_reason"):
                    finish_reason = choice0.get("finish_reason")

            if in_think and think_frag:
                reasoning_parts.append(think_frag)
            think_frag = ""

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
            if calls:
                yield {"type": "tool_calls", "calls": calls}

            response = ChatResponse(
                content="".join(content_parts),
                tool_calls=calls,
                model=model_name,
                finish_reason=finish_reason or "stop",
                usage=usage,
                reasoning_content="".join(reasoning_parts) or None,
            )
            if usage:
                self._calibrate_from_usage(usage, req_tokens)
            logger.info(
                "[LLM] <<< 流式完成 | model=%s | content长度=%d | tool_calls=%d | finish_reason=%s",
                self.model, len(response.content or ""), len(calls), response.finish_reason,
            )
            yield {"type": "done", "response": response}
        except Exception as e:
            logger.error("[LLM] <<< 流式调用失败 | model=%s | 错误类型=%s | 详情: %s", self.model, type(e).__name__, str(e))
            raise

    async def _chat_stream_anthropic(self, transport, params, req_tokens):
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls_map: Dict[int, dict] = {}
        usage = {}
        model_name = ""
        finish_reason = ""

        try:
            async for item in transport.chat_stream(params):
                evt_type = item.get("type")
                
                if evt_type == "message_start":
                    msg = item.get("message") or {}
                    model_name = msg.get("model", "")
                elif evt_type == "message_delta":
                    finish_reason = item.get("delta", {}).get("stop_reason", "")
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
                        text = delta.get("text", "")
                        content_parts.append(text)
                        yield {"type": "content", "text": text}
                    elif dtype == "thinking_delta":
                        text = delta.get("thinking", "")
                        reasoning_parts.append(text)
                        yield {"type": "reasoning", "text": text}

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
            if calls:
                yield {"type": "tool_calls", "calls": calls}

            response = ChatResponse(
                content="".join(content_parts),
                tool_calls=calls,
                model=model_name,
                finish_reason=finish_reason or "stop",
                usage=usage,
                reasoning_content="".join(reasoning_parts) or None,
            )
            if usage:
                self._calibrate_from_usage(usage, req_tokens)
            logger.info(
                "[LLM] <<< 流式完成 | model=%s | content长度=%d | tool_calls=%d | finish_reason=%s",
                self.model, len(response.content or ""), len(calls), response.finish_reason,
            )
            yield {"type": "done", "response": response}
        except Exception as e:
            logger.error("[LLM] <<< 流式调用失败 | model=%s | 错误类型=%s | 详情: %s", self.model, type(e).__name__, str(e))
            raise

    def chat_sync(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = None,
        max_tokens: int = None,
        **kwargs,
    ) -> ChatResponse:
        """同步调用（测试用）"""
        if not self.api_key:
            raise ValueError("LLM 引擎未配置有效的 API key，请在设置中配置后重试")
        transport = self._ensure_transport()

        effective_messages = messages
        if messages:
            effective_messages, _ = self._truncate_if_needed(messages, tools)

        final_max_tokens = self._resolve_max_tokens(effective_messages, tools, max_tokens)

        params = {
            "model": self.model,
            "messages": effective_messages,
            "temperature": temperature or self.temperature,
        }
        if not self._omit_max_tokens:
            params["max_tokens"] = final_max_tokens
        if tools:
            params["tools"] = tools
        if kwargs:
            params.update(kwargs)

        logger.info(
            "[LLM-sync] >>> 调用开始 | model=%s | 消息数=%d | tools=%d",
            self.model, len(effective_messages), len(tools) if tools else 0,
        )
        t0 = time.monotonic()
        try:
            parsed = transport.chat_sync(params)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error("[LLM-sync] <<< 失败 | 耗时=%.0fms | %s: %s", elapsed_ms, type(e).__name__, str(e))
            raise

        logger.info(
            "[LLM-sync] <<< 成功 | model=%s | 耗时=%.0fms | finish_reason=%s | tool_calls=%d",
            self.model, elapsed_ms, parsed.finish_reason, len(parsed.tool_calls),
        )
        return parsed

    def _resolve_max_tokens(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
        explicit_max: int = None,
    ) -> int:
        """
        计算安全的 max_tokens 值

        逻辑:
          1. 如果显式指定了且合理 → 使用指定值
          2. 否则用模型默认值
          3. 但不能超过 (上下文窗口 - 已用输入 tokens)
        """
        input_estimate = self.estimate_tokens_calibrated(messages, tools)

        safety_margin = 512

        available = self.context_window - input_estimate - safety_margin
        if available < 256:
            available = 256 

        model_default = self.effective_max_tokens

        if explicit_max is not None:
            return min(explicit_max, available, model_default)

        return min(model_default, available)

    def _truncate_if_needed(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
    ) -> tuple:
        """
        检查并截断消息列表以适应上下文窗口

        策略: 从最早的消息开始丢弃，保留 system prompt 和最近的消息。

        Returns:
            (可能被截断后的消息列表, bool: 是否发生了截断)
        """
        input_est = _estimate_messages_tokens(messages)
        if tools:
            input_est += len(tools) * 150

        if input_est <= self.context_window:
            return messages, False

        logger.warning(
            "[LLM] 输入约 %d tokens 超出 %d 上下文窗口，开始截断",
            input_est, self.context_window,
        )

        system_msgs = [m for m in messages if m.get("role") == "system"]
        normal_msgs = [m for m in messages if m.get("role") != "system"]

        while normal_msgs:
            test_msgs = system_msgs + normal_msgs
            est = _estimate_messages_tokens(test_msgs)
            if tools:
                est += len(tools) * 150
            if est <= self.context_window * 0.95:  
                break
            normal_msgs.pop(0)  

        result = system_msgs + normal_msgs
        new_est = _estimate_messages_tokens(result)
        logger.info(
            "[LLM] 截断完成: %d → %d 条消息 (%d → 约 %d tokens)",
            len(messages), len(result), input_est, new_est,
        )
        return result, True

    async def health_check(self) -> bool:
        try:
            resp = await self.chat(
                messages=[{"role": "user", "content": "ping"}],
                tools=None,
            )
            return True
        except Exception as e:
            logger.warning("[LLM] 健康检查失败: %s", e)
            return False

    def _trigger_deferred_verify(self):
        import threading

        self._verify_done = True 

        def _do_verify():
            try:
                ok = self.verify_model_exists()
                if not ok:
                    logger.warning(
                        "[LLM] 后台验证失败：模型 '%s' 可能不存在 | 引擎仍将尝试调用",
                        self.model,
                    )
                else:
                    logger.info("[LLM] 后台模型验证通过：%s", self.model)
            except Exception as e:
                logger.warning("[LLM] 延迟模型验证异常: %s", e)

        t = threading.Thread(target=_do_verify, daemon=True, name="model-verify")
        t.start()
        logger.info("[LLM] 已触发后台模型验证（线程=%s）", t.name)

    def verify_model_exists(self) -> bool:
        if not self._verify_model:
            logger.debug("[LLM] 模型验证已跳过 (verify_model=False)")
            return True

        cache_key = f"{self.base_url}|{self.model}"
        if cache_key in _VERIFY_CACHE:
            logger.debug("[LLM] 模型验证命中缓存 | model=%s | cached=%s", self.model, _VERIFY_CACHE[cache_key])
            return _VERIFY_CACHE[cache_key]

        if _match_model(self.model) is not None:
            logger.info("[LLM] 模型 '%s' 在内置注册表中，跳过 API 验证", self.model)
            _VERIFY_CACHE[cache_key] = True
            return True

        try:
            available_ids = self._ensure_transport().list_models()
            logger.debug("[LLM] /models 返回 %d 个可用模型", len(available_ids))

            target = self.model.lower().strip()
            for mid in available_ids:
                if mid.lower() == target or mid.lower().startswith(target):
                    logger.info(
                        "[LLM] 模型验证通过 | config='%s' → 匹配到 '%s' | 来源=API",
                        self.model, mid,
                    )
                    _VERIFY_CACHE[cache_key] = True
                    return True

            similar = [mid for mid in available_ids if any(
                part in mid.lower() for part in target.replace("-", " ").replace(".", " ").split()
            )][:5]
            logger.error(
                "[LLM] 模型验证失败 | 配置的模型 '%s' 在 /models 中未找到！"
                "\n   可用模型数=%d | 相似模型=%s",
                self.model, len(available_ids), similar or "(无)",
            )
            _VERIFY_CACHE[cache_key] = False
            return False

        except Exception as e:
            logger.warning(
                "[LLM] /models 接口调用失败 (%s: %s)，跳过模型验证。"
                "首次实际调用时会暴露问题。",
                type(e).__name__, str(e),
            )
            _VERIFY_CACHE[cache_key] = True 
            return True  

    def _calibrate_from_usage(self, actual_usage: Dict, estimated_input: int):
        if self._calibrated:
            return

        prompt_tokens = actual_usage.get("prompt_tokens", 0)
        if prompt_tokens <= 0 or estimated_input <= 0:
            return

        ratio = prompt_tokens / estimated_input
        self._actual_token_ratio = ratio
        self._calibrated = True

        if 0.8 <= ratio <= 1.3:
            logger.debug("[LLM] Token 校准完成 | 估算=%d | 实际=%d | 比值=%.2fx",
                        estimated_input, prompt_tokens, ratio)
        elif 0.5 <= ratio <= 2.0:
            logger.info("[LLM] Token 校准完成 | 估算=%d | 实际=%d | 比值=%.2fx | 已自动校准",
                        estimated_input, prompt_tokens, ratio)
        else:
            logger.warning("[LLM] Token 校准完成 | 估算=%d | 实际=%d | 比值=%.2fx | 偏差较大，建议检查 model 配置",
                        estimated_input, prompt_tokens, ratio)


# ============================================================
#  工厂函数
# ============================================================

def create_llm_engine(config_dict: Dict = None) -> BaseLLMEngine:
    """根据配置创建引擎实例

    从 models[current_model] 获取当前模型的配置，而不是从 openai.* 获取
    """
    if config_dict is None:
        from app.core.util.agent_config import get_config
        cfg = get_config()
        config_dict = cfg.get_section("llm")

    if not config_dict:
        raise ValueError("LLM 配置为空，请检查 config/agent/llm.yaml")

    provider = config_dict.get("provider", "openai").lower()

    if provider == "anthropic":
        models = config_dict.get("models", [])
        current_model_name = config_dict.get("current_model", "")

        model_config = None
        for m in models:
            if m.get("name") == current_model_name:
                model_config = m
                break

        if not model_config:
            if models:
                model_config = models[0]
                current_model_name = models[0].get("name", "default")
                logger.warning("[LLMFactory] 未找到模型 '%s'，使用第一个模型: %s",
                               config_dict.get("current_model", ""), current_model_name)
            else:
                raise ValueError(f"未找到当前模型配置: {current_model_name}，且 models 列表为空")

        api_key = model_config.get("api_key", "")
        base_url = model_config.get("base_url", "https://api.anthropic.com")
        model_name = model_config.get("model", "claude-sonnet-4-20250514")
        if not model_name:
            logger.warning("[LLMFactory] 模型名称为空，自动回退到 claude-sonnet-4-20250514")
            model_name = "claude-sonnet-4-20250514"
        if not base_url:
            logger.warning("[LLMFactory] API 地址为空，自动回退到 https://api.anthropic.com")
            base_url = "https://api.anthropic.com"
        api_key_preview = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
        logger.info("[LLMFactory] 使用 Anthropic 模型配置 | name=%s | api_key=%s | base_url=%s",
                    current_model_name, api_key_preview, base_url)

        thinking_config = config_dict.get("thinking", {})
        thinking_enabled = thinking_config.get("enabled", False)
        thinking_budget = thinking_config.get("budget_tokens")

        model_vision = bool(model_config.get("vision", False))

        engine = OpenAICompatibleEngine(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            temperature=float(model_config.get("temperature", 0.7)),
            max_tokens=int(model_config.get("max_tokens", 0)) or None,
            timeout=int(model_config.get("timeout", 120)),
            context_window=int(model_config.get("context_window", 0)) or None,
            verify_model=True,
            omit_max_tokens=bool(model_config.get("omit_max_tokens", False)),
            thinking=thinking_enabled,
            thinking_budget=thinking_budget,
            vision=model_vision,
            provider="anthropic",
        )

        info = engine.model_info
        logger.info(
            "[LLMFactory] 已创建 Anthropic 引擎 | model=%s | current_model=%s | ctx=%d out=%d vision=%s",
            engine.model, current_model_name, info.context_window, info.max_output_tokens,
            info.supports_vision,
        )

        engine._verify_deferred = False
        _get_model_registry()
        logger.info("[LLMFactory] Anthropic 引擎已创建")
        return engine

    elif provider in ("openai", "stepfun", "deepseek", "custom"):
        models = config_dict.get("models", [])
        current_model_name = config_dict.get("current_model", "")

        model_config = None
        for m in models:
            if m.get("name") == current_model_name:
                model_config = m
                break

        if not model_config:
            if models:
                model_config = models[0]
                current_model_name = models[0].get("name", "default")
                logger.warning("[LLMFactory] 未找到模型 '%s'，使用第一个模型: %s",
                               config_dict.get("current_model", ""), current_model_name)
            else:
                raise ValueError(f"未找到当前模型配置: {current_model_name}，且 models 列表为空")

        api_key = model_config.get("api_key", "")
        base_url = model_config.get("base_url", "https://api.openai.com/v1")
        model_name = model_config.get("model", "gpt-4o")
        if not model_name:
            logger.warning("[LLMFactory] 模型名称为空，自动回退到 gpt-4o")
            model_name = "gpt-4o"
        if not base_url:
            logger.warning("[LLMFactory] API 地址为空，自动回退到 https://api.openai.com/v1")
            base_url = "https://api.openai.com/v1"
        api_key_preview = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
        logger.info("[LLMFactory] 使用模型配置 | name=%s | api_key=%s | base_url=%s",
                    current_model_name, api_key_preview, base_url)

        thinking_config = config_dict.get("thinking", {})
        thinking_enabled = thinking_config.get("enabled", False)
        thinking_budget = thinking_config.get("budget_tokens")

        model_vision = bool(model_config.get("vision", False))

        engine = OpenAICompatibleEngine(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            temperature=float(model_config.get("temperature", 0.7)),
            max_tokens=int(model_config.get("max_tokens", 0)) or None,
            timeout=int(model_config.get("timeout", 60)),
            context_window=int(model_config.get("context_window", 0)) or None,
            verify_model=True,
            omit_max_tokens=bool(model_config.get("omit_max_tokens", False)),
            thinking=thinking_enabled,
            thinking_budget=thinking_budget,
            vision=model_vision,
        )

        info = engine.model_info
        logger.info(
            "[LLMFactory] 已创建引擎 | model=%s | current_model=%s | ctx=%d out=%d vision=%s",
            engine.model, current_model_name, info.context_window, info.max_output_tokens,
            info.supports_vision,
        )

        engine._verify_deferred = False
        _get_model_registry()
        logger.info("[LLMFactory] 引擎已创建")
        return engine

    elif provider == "ollama":
        oc = config_dict.get("ollama", {})
        thinking_config = config_dict.get("thinking", {})
        thinking_enabled = thinking_config.get("enabled", False)
        thinking_budget = thinking_config.get("budget_tokens")
        engine = OpenAICompatibleEngine(
            api_key="ollama",
            base_url=oc.get("base_url", "http://localhost:11434/v1"),
            model=oc.get("model", "qwen2.5:7b"),
            temperature=float(oc.get("temperature", 0.7)),
            max_tokens=int(oc.get("max_tokens", 0)) or None,
            timeout=int(oc.get("timeout", 120)),
            context_window=int(oc.get("context_window", 0)) or None,
            verify_model=True,
            omit_max_tokens=bool(oc.get("omit_max_tokens", False)),
            thinking=thinking_enabled,
            thinking_budget=thinking_budget,
            vision=bool(oc.get("vision", False)),
        )
        info = engine.model_info
        logger.info(
            "[LLMFactory] 已创建 Ollama 引擎 | model=%s | ctx=%d out=%d vision=%s",
            engine.model, info.context_window, info.max_output_tokens, info.supports_vision,
        )
        engine._verify_deferred = False
        _get_model_registry()
        logger.info("[LLMFactory] Ollama 引擎已创建（首次调用时初始化 client）")
        return engine

    else:
        raise ValueError(f"不支持的 LLM provider: {provider} (可选: openai, ollama)")


def list_supported_models() -> Dict[str, ModelInfo]:
    """列出所有内置支持的模型及其能力"""
    return dict(_get_model_registry())


def query_model_capability(model_name: str) -> Optional[ModelInfo]:
    """查询指定模型的能力信息（不创建引擎）"""
    return _match_model(model_name)
