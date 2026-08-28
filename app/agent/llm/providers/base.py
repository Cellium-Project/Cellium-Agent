# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Dict, List
from urllib.parse import urljoin


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

    def create_model_config(self, api_key: str, model_id: str, model_name: str = "") -> Dict:
        cfg = {
            "name": f"{self.get_provider_id()}-{model_id.split('/')[-1]}",
            "api_key": api_key,
            "base_url": self.get_chat_base_url(),
            "model": model_id,
            "temperature": 0.7,
            "timeout": 120,
            "vision": False,
        }
        thinking = self.get_default_thinking()
        if thinking:
            cfg["thinking"] = thinking
        return cfg
