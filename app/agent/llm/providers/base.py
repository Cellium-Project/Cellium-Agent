# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Dict, List
from urllib.parse import urljoin


_CONTEXT_KEYS = (
    "context_length", "context_window", "context_size",
    "max_model_len", "max_input_tokens", "max_context_tokens",
)


def parse_context_length(item: Dict) -> int:
    if not isinstance(item, dict):
        return 0
    for key in _CONTEXT_KEYS:
        val = item.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
        if isinstance(val, str) and val.strip().isdigit() and int(val) > 0:
            return int(val)
    for holder in ("limit", "top_provider"):
        sub = item.get(holder)
        if isinstance(sub, dict):
            val = sub.get("context") or sub.get("context_length")
            if isinstance(val, (int, float)) and val > 0:
                return int(val)
    return 0


class BaseProvider(ABC):
    """LLM 厂商适配器基类"""

    @abstractmethod
    def get_provider_id(self) -> str:
        ...

    @abstractmethod
    def get_provider_name(self) -> str:
        ...

    @abstractmethod
    def get_base_url(self) -> str:
        ...

    @abstractmethod
    def get_models_endpoint(self) -> str:
        ...

    @abstractmethod
    def get_chat_endpoint(self) -> str:
        ...

    @abstractmethod
    def get_reasoning_param_name(self) -> str:
        ...

    @abstractmethod
    async def fetch_models(self, api_key: str) -> List[Dict]:
        ...

    def get_chat_base_url(self) -> str:
        base = self.get_base_url().rstrip("/")
        chat_ep = self.get_chat_endpoint()
        prefix = chat_ep.rsplit("/chat/completions", 1)[0].rstrip("/")
        if prefix and prefix.startswith("/"):
            prefix = prefix[1:]
        if prefix:
            return urljoin(base + "/", prefix)
        return base + "/v1"

    def get_anthropic_base_url(self) -> str:
        return ""

    def get_default_thinking(self) -> Dict:
        return {}

    def get_match_urls(self) -> List[str]:
        """用于 detect_provider 的 base_url 匹配列表"""
        return [self.get_base_url()]

    def get_thinking_payload(self, thinking_enabled: bool, budget_tokens: int, effort: str):
        return None

    def create_model_config(self, api_key: str, model_id: str, model_name: str = "",
                            context_length: int = None) -> Dict:
        cfg = {
            "name": f"{self.get_provider_id()}-{model_id.split('/')[-1]}",
            "api_key": api_key,
            "base_url": self.get_chat_base_url(),
            "model": model_id,
            "temperature": 0.7,
            "timeout": 120,
        }
        if context_length:
            cfg["context_window"] = int(context_length)
        thinking = self.get_default_thinking()
        if thinking:
            cfg["thinking"] = thinking
        return cfg
