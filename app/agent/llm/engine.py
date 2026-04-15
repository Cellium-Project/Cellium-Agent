# -*- coding: utf-8 -*-
"""LLM 引擎 — 统一接口 + OpenAI 兼容实现 + 模型能力自动检测"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EMPTY_RESPONSE_COUNT = 0

_VERIFY_CACHE: Dict[str, bool] = {}

@dataclass(frozen=True)
class ModelInfo:
    """单个模型的能力参数"""
    context_window: int      # 最大输入上下文 (tokens)
    max_output_tokens: int   # 单次最大输出 (tokens)
    supports_tools: bool     # 是否支持 function calling / tool use


@dataclass
class ToolCall:
    """工具调用（LLM 返回的 function_call）"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ChatResponse:
    """LLM 聊天响应（统一格式，屏蔽不同 provider 差异）"""
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    finish_reason: str = ""
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# 格式: "model_name" → ModelInfo(context_window, max_output, supports_tools)
_MODEL_REGISTRY: Dict[str, ModelInfo] = {
    # ---- OpenAI ----
    "gpt-4o":            ModelInfo(128000, 16384, True),
    "gpt-4o-mini":       ModelInfo(128000, 16384, True),
    "gpt-4-turbo":       ModelInfo(128000, 4096, True),
    "gpt-4":             ModelInfo(8192, 8192, True),
    "gpt-4-32k":         ModelInfo(32768, 8192, True),
    "gpt-3.5-turbo":     ModelInfo(16385, 4096, True),
    "o1":                ModelInfo(200000, 100000, False),  # 推理模型，tool 支持有限
    "o1-mini":            ModelInfo(128000, 65536, False),
    "o3-mini":            ModelInfo(200000, 100000, False),

    # ---- 阶跃星辰 (StepFun) ----
    "step-1v":            ModelInfo(256000, 8000, True),
    "step-1-flash":       ModelInfo(256000, 8000, True),
    "step-1.5v":          ModelInfo(256000, 16000, True),
    "step-1.5-flash":     ModelInfo(256000, 16000, True),
    "step-2-16k":         ModelInfo(16000, 8192, True),
    "step-2-medium":      ModelInfo(32000, 8192, True),
    "step-2-turbo":       ModelInfo(128000, 8192, True),
    "step-3":             ModelInfo(256000, 16000, True),
    "step-3-flash":       ModelInfo(131072, 8192, True),
    "step-3.5-flash-260307": ModelInfo(131072, 8192, True),  # 2026-03 版本

    # ---- DeepSeek ----
    "deepseek-chat":      ModelInfo(128000, 8192, True),      # 128K 上下文，默认 4K，最大 8K
    "deepseek-reasoner":  ModelInfo(128000, 65536, True),     # 128K 上下文，支持 tool_use

    # ---- 通义千问 (Qwen) via OpenAI 兼容接口 ----
    "qwen-turbo":         ModelInfo(8192, 2048, True),
    "qwen-plus":          ModelInfo(32768, 8192, True),
    "qwen-max":           ModelInfo(32768, 8192, True),
    "qwen-long":          ModelInfo(1000000, 8192, True),  # 百万上下文
    "qwq-32b":            ModelInfo(32768, 16384, False),  # 推理模型

    # ---- Ollama 常见模型 (本地) ----
    "llama3:latest":      ModelInfo(128000, 4096, True),
    "llama3.1:latest":    ModelInfo(128000, 4096, True),
    "llama3.2:latest":    ModelInfo(128000, 4096, True),
    "llama3.3:latest":    ModelInfo(128000, 4096, True),
    "qwen2:7b":           ModelInfo(32768, 2048, True),
    "qwen2:72b":          ModelInfo(32768, 2048, True),
    "qwen2.5:7b":         ModelInfo(32768, 2048, True),
    "qwen2.5:72b":        ModelInfo(32768, 2048, True),
    "qwen3:latest":       ModelInfo(32768, 2048, True),
    "mistral:latest":     ModelInfo(32768, 4096, True),
    "codellama:latest":   ModelInfo(16384, 4096, True),
    "phi3:latest":        ModelInfo(128000, 4096, True),
    "gemma2:latest":      ModelInfo(8192, 4096, True),
    "command-r:latest":   ModelInfo(128000, 4096, True),
    "deepseek-v2:16b":    ModelInfo(32768, 4096, True),

    # ---- Claude (via OpenAI 兼容代理) ----
    "claude-3-5-sonnet":  ModelInfo(200000, 8192, True),
    "claude-3-opus":      ModelInfo(200000, 4096, True),
    "claude-3-haiku":     ModelInfo(200000, 4096, True),

    # ---- 通用兜底 ----
}


def _match_model(model_name: str) -> Optional[ModelInfo]:

    if not model_name:
        return None

    key = model_name.strip().lower()

    if key in _MODEL_REGISTRY:
        return _MODEL_REGISTRY[key]

    for reg_key, info in _MODEL_REGISTRY.items():
        if key.startswith(reg_key):
            logger.info("[LLM] 模型 '%s' 前缀匹配到注册项 '%s'", model_name, reg_key)
            return info

    base = key.split(":")[0]
    for reg_key, info in _MODEL_REGISTRY.items():
        reg_base = reg_key.split(":")[0]
        if base == reg_base or reg_key.startswith(base + ":"):
            logger.info("[LLM] 模型 '%s' Ollama-tag 匹配到 '%s'", model_name, reg_key)
            return info

    logger.warning(
        "[LLM] 模型 '%s' 不在内置数据库中，使用保守默认值 "
        "(context=8192, max_output=4096)。如需精确值请配置或提交 issue。",
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
        # 每条消息有 ~4 token 的格式开销 (role + header)
        total += _estimate_tokens(content) + 4

    # system prompt 和工具定义也有额外开销，加 10% buffer
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
        **kwargs,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self._extra_client_args = kwargs
        self._verify_model = verify_model
        self._verify_deferred = False  
        self._verify_done = False     

        detected = _match_model(model)

        self._model_info = ModelInfo(
            context_window=context_window or (detected.context_window if detected else self.DEFAULT_CONTEXT_WINDOW),
            max_output_tokens=max_tokens or (detected.max_output_tokens if detected else self.DEFAULT_MAX_OUTPUT),
            supports_tools=(detected.supports_tools if detected else True),
        )

        self._calibrated = False
        self._actual_token_ratio = None  

        self._explicit_max_tokens = max_tokens

        self._client = None
        self._async_client = None

        logger.info(
            "[LLM] 引擎初始化 | model=%s | ctx=%d | max_out=%d | tools=%s | url=%s | 能力来源=%s",
            model, self._model_info.context_window,
            self._model_info.max_output_tokens,
            self._model_info.supports_tools, base_url[:50],
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

    def _ensure_sync_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                **self._extra_client_args,
            )

    def _ensure_async_client(self):
        if self._async_client is None:
            from openai import AsyncOpenAI
            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                **self._extra_client_args,
            )

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
        异步调用 LLM（AgentLoop 主路径）

        Args:
            messages: 消息列表
            tools: 工具定义
            temperature: 温度覆盖
            max_tokens: 最大输出 token 覆盖（None → 用模型默认值）
            truncate: 是否在输入超限时自动截断早期消息
        """
        self._ensure_async_client()

        if self._verify_deferred and not self._verify_done:
            self._trigger_deferred_verify()

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
            "max_tokens": final_max_tokens,
        }

        if tools and self._model_info.supports_tools:
            params["tools"] = tools
        elif tools and not self._model_info.supports_tools:
            logger.warning(
                "[LLM] 模型 %s 可能不支持 tool_use，工具定义仍将发送",
                self.model,
            )
            params["tools"] = tools

        if kwargs:
            params.update(kwargs)

        req_tokens = _estimate_messages_tokens(effective_messages)
        tools_count = len(tools) if tools else 0
        logger.info(
            "[LLM] >>> 调用开始 | model=%s | 消息数=%d | 预估输入≈%d tokens | max_tokens=%d | tools=%d | temperature=%s",
            self.model, len(effective_messages), req_tokens,
            final_max_tokens, tools_count,
            params.get("temperature", "N/A"),
        )

        t0 = time.monotonic()
        try:
            response = await self._async_client.chat.completions.create(**params)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "[LLM] <<< 调用失败 | model=%s | 耗时=%.0fms | 错误类型=%s | 详情: %s",
                self.model, elapsed_ms, type(e).__name__, str(e),
            )
            raise

        parsed = self._parse_response(response)
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
            type(response).__name__,
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
                type(response).__name__,
                req_tokens,
                final_max_tokens,
                tools_count,
                tool_names,
                parsed.finish_reason,
                parsed.usage,
                self.temperature,
                str(response)[:500],
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

    def chat_sync(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = None,
        max_tokens: int = None,
        **kwargs,
    ) -> ChatResponse:
        """同步调用（测试用）"""
        self._ensure_sync_client()

        effective_messages = messages
        if messages:
            effective_messages, _ = self._truncate_if_needed(messages, tools)

        final_max_tokens = self._resolve_max_tokens(effective_messages, tools, max_tokens)

        params = {
            "model": self.model,
            "messages": effective_messages,
            "temperature": temperature or self.temperature,
            "max_tokens": final_max_tokens,
        }
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
            response = self._client.chat.completions.create(**params)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error("[LLM-sync] <<< 失败 | 耗时=%.0fms | %s: %s", elapsed_ms, type(e).__name__, str(e))
            raise

        parsed = self._parse_response(response)
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

    @staticmethod
    def _parse_sse_text_response(raw_text: str) -> ChatResponse:
        """解析整段 SSE 文本串响应，兼容第三方把流式结果整体返回为字符串"""
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

    @staticmethod
    def _parse_response(raw_response) -> ChatResponse:
        """统一解析 OpenAI SDK 响应，并兼容部分第三方非标准返回"""
        if hasattr(raw_response, "choices"):
            choice = raw_response.choices[0]
            message = choice.message

            content = message.content

            logger.info(
                "[LLM] _parse_response | finish_reason=%s | content=%s | has_tool_calls=%s",
                choice.finish_reason,
                (content or "(空)")[:200],
                hasattr(message, 'tool_calls') and bool(message.tool_calls),
            )

            tool_calls = []
            if hasattr(message, 'tool_calls') and message.tool_calls:
                import json as _json
                for tc in message.tool_calls:
                    args = {}
                    if tc.function and tc.function.arguments:
                        try:
                            args = _json.loads(tc.function.arguments)
                        except _json.JSONDecodeError:
                            args = {"raw": tc.function.arguments}
                    tool_calls.append(ToolCall(
                        id=tc.id or "",
                        name=tc.function.name if tc.function else "",
                        arguments=args,
                    ))

            usage = {}
            if hasattr(raw_response, 'usage') and raw_response.usage:
                usage = {
                    "prompt_tokens": raw_response.usage.prompt_tokens or 0,
                    "completion_tokens": raw_response.usage.completion_tokens or 0,
                    "total_tokens": raw_response.usage.total_tokens or 0,
                }

            return ChatResponse(
                content=content,
                tool_calls=tool_calls,
                model=getattr(raw_response, 'model', ''),
                finish_reason=choice.finish_reason or '',
                usage=usage,
            )

        if isinstance(raw_response, str):
            raw = raw_response.strip()
            if raw.startswith("data:") and "\n" in raw_response:
                parsed_sse = OpenAILLMEngine._parse_sse_text_response(raw_response)
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
                parsed_json = json.loads(raw)
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
            raw_response = parsed_json

        if isinstance(raw_response, dict):
            if isinstance(raw_response.get("choices"), list) and raw_response["choices"]:
                choice = raw_response["choices"][0] or {}
                message = choice.get("message", {}) or {}
                content = message.get("content", "")
                return ChatResponse(
                    content=content,
                    tool_calls=[],
                    model=raw_response.get("model", ""),
                    finish_reason=choice.get("finish_reason", "") or "",
                    usage=raw_response.get("usage", {}) or {},
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

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        **kwargs,
    ):
        """流式生成（SSE/WebSocket 实时推送用）"""
        self._ensure_async_client()

        if self._verify_deferred and not self._verify_done:
            self._trigger_deferred_verify()

        effective_messages = messages
        if messages:
            effective_messages, _ = self._truncate_if_needed(messages, tools)

        params = {
            "model": self.model,
            "messages": effective_messages,
            "stream": True,
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.effective_max_tokens),
        }
        if tools and self._model_info.supports_tools:
            params["tools"] = tools
        params.update(kwargs)

        stream = await self._async_client.chat.completions.create(**params)

        req_tokens = _estimate_messages_tokens(effective_messages)
        tools_count = len(tools) if tools else 0
        logger.info(
            "[LLM] >>> 流式调用开始 | model=%s | 消息数=%d | 预估输入≈%d tokens | max_tokens=%d | tools=%d | temperature=%s",
            self.model, len(effective_messages), req_tokens,
            params.get("max_tokens", self.effective_max_tokens), tools_count,
            params.get("temperature", "N/A"),
        )

        full_content = ""
        done_sent = False
        saw_content_chunk = False
        async for chunk in stream:
            if hasattr(chunk, "choices"):
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    full_content += delta.content
                    saw_content_chunk = True
                    yield {"type": "chunk", "content": delta.content, "full_content": full_content}
                elif not done_sent and (not delta or (chunk.choices and chunk.choices[0].finish_reason in ("stop", None))):
                    done_sent = True
                    yield {"type": "done", "full_content": full_content}
                continue

            if isinstance(chunk, str):
                raw = chunk.strip()
                if raw.startswith("data:") and "\n" in chunk:
                    parsed_sse = self._parse_sse_text_response(chunk)
                    if parsed_sse.content:
                        full_content += parsed_sse.content
                        saw_content_chunk = True
                        yield {"type": "chunk", "content": parsed_sse.content, "full_content": full_content}
                    if parsed_sse.usage:
                        logger.debug("[LLM] 收到整段 SSE 文本串 chunk，usage=%s", parsed_sse.usage)
                    if not done_sent:
                        done_sent = True
                        yield {"type": "done", "full_content": full_content}
                    continue
                if raw.startswith("data:"):
                    raw = raw[5:].strip()
                if raw == "[DONE]":
                    if not done_sent:
                        done_sent = True
                        yield {"type": "done", "full_content": full_content}
                    continue
                try:
                    chunk = json.loads(raw)
                except Exception:
                    if raw:
                        full_content += raw
                        saw_content_chunk = True
                        yield {"type": "chunk", "content": raw, "full_content": full_content}
                    continue

            if isinstance(chunk, dict):
                choices = chunk.get("choices") or []
                if choices:
                    choice0 = choices[0] or {}
                    delta = choice0.get("delta") or {}
                    delta_content = delta.get("content") if isinstance(delta, dict) else None
                    finish_reason = choice0.get("finish_reason")
                    if delta_content:
                        full_content += delta_content
                        saw_content_chunk = True
                        yield {"type": "chunk", "content": delta_content, "full_content": full_content}
                        continue
                    if not done_sent and finish_reason in ("stop", None):
                        done_sent = True
                        yield {"type": "done", "full_content": full_content}
                        continue
                elif chunk.get("usage"):
                    logger.debug("[LLM] 收到 usage 尾包 chunk，忽略内容")
                    continue
                preview = str(chunk)[:200]
                logger.warning("[LLM] 流式 chunk 为非标准 dict，跳过 | preview=%s", preview)
                continue

            preview = str(chunk)[:200]
            logger.warning("[LLM] 流式 chunk 类型异常，跳过 | type=%s | preview=%s", type(chunk).__name__, preview)

        if not saw_content_chunk and not full_content:
            logger.error(
                "[LLM] 流式空完成 | model=%s | req_tokens≈%d | max_tokens=%d | tools=%d",
                self.model,
                req_tokens,
                params.get("max_tokens", self.effective_max_tokens),
                tools_count,
            )
            raise ValueError(
                f"第三方兼容接口返回空流式完成：model={self.model}, req_tokens≈{req_tokens}, max_tokens={params.get('max_tokens', self.effective_max_tokens)}, tools={tools_count}"
            )


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
        global _VERIFY_CACHE

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
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=10)
            models_resp = client.models.list()

            available_ids = [m.id for m in models_resp.data]
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
            level = "info" 
            msg = "准确"
        elif 0.5 <= ratio <= 2.0:
            level = "warning"  
            msg = f"偏差 {ratio:.1f}x"
        else:
            level = "error"  
            msg = f"严重偏差 {ratio:.1f}x！建议手动配置 context_window"

        getattr(logger, level)(
            "[LLM] Token 校准完成 | 估算=%d | 实际=%d | 比值=%.2fx | %s",
            estimated_input, prompt_tokens, ratio, msg,
        )

        if abs(ratio - 1.0) > 0.3:
            logger.warning(
                "[LLM] 如需更精确，可在 llm.yaml 中显式配置:\n"
                "     llm:\n"
                "       openai:\n"
                "         model: \"%s\"\n"
                "         context_window: <实际值>  # 根据厂商文档\n"
                "         max_tokens: <实际值>",
                self.model,
            )


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

    if provider in ("openai", "stepfun", "deepseek", "custom"):
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
        api_key_preview = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
        logger.info("[LLMFactory] 使用模型配置 | name=%s | api_key=%s | base_url=%s",
                    current_model_name, api_key_preview, model_config.get("base_url", ""))

        engine = OpenAICompatibleEngine(
            api_key=api_key,
            base_url=model_config.get("base_url", "https://api.openai.com/v1"),
            model=model_config.get("model", "gpt-4o"),
            temperature=float(model_config.get("temperature", 0.7)),
            max_tokens=int(model_config.get("max_tokens", 0)) or None,
            timeout=int(model_config.get("timeout", 60)),
            context_window=int(model_config.get("context_window", 0)) or None,
            verify_model=True,
        )

        info = engine.model_info
        logger.info(
            "[LLMFactory] 已创建引擎 | model=%s | current_model=%s | ctx=%d out=%d tools=%s",
            engine.model, current_model_name, info.context_window, info.max_output_tokens,
            info.supports_tools,
        )

        engine._verify_deferred = True
        logger.info("[LLMFactory] 引擎已创建（模型验证已延迟到首次使用）")

        return engine

    elif provider == "ollama":
        oc = config_dict.get("ollama", {})
        engine = OpenAICompatibleEngine(
            api_key="ollama",
            base_url=oc.get("base_url", "http://localhost:11434/v1"),
            model=oc.get("model", "qwen2.5:7b"),
            temperature=float(oc.get("temperature", 0.7)),
            max_tokens=int(oc.get("max_tokens", 0)) or None,
            timeout=int(oc.get("timeout", 120)),
            context_window=int(oc.get("context_window", 0)) or None,
            verify_model=True,
        )
        info = engine.model_info
        logger.info(
            "[LLMFactory] 已创建 Ollama 引擎 | model=%s | ctx=%d out=%d",
            engine.model, info.context_window, info.max_output_tokens,
        )
        engine._verify_deferred = True
        logger.info("[LLMFactory] Ollama 引擎已创建（模型验证已延迟到首次使用）")
        return engine

    else:
        raise ValueError(f"不支持的 LLM provider: {provider} (可选: openai, ollama)")


def list_supported_models() -> Dict[str, ModelInfo]:
    """列出所有内置支持的模型及其能力"""
    return dict(_MODEL_REGISTRY)


def query_model_capability(model_name: str) -> Optional[ModelInfo]:
    """查询指定模型的能力信息（不创建引擎）"""
    return _match_model(model_name)
