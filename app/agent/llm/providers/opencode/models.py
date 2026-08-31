# -*- coding: utf-8 -*-
import httpx
from typing import Dict, List

from ..base import BaseProvider
from .config import (
    PROVIDER_ID, PROVIDER_NAME, BASE_URL,
    MODELS_ENDPOINT, CHAT_ENDPOINT, REASONING_PARAM,
    DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT,
)


class OpenCodeProvider(BaseProvider):

    def get_provider_id(self) -> str:
        return PROVIDER_ID

    def get_provider_name(self) -> str:
        return PROVIDER_NAME

    def get_base_url(self) -> str:
        return BASE_URL

    def get_models_endpoint(self) -> str:
        return MODELS_ENDPOINT

    def get_chat_endpoint(self) -> str:
        return CHAT_ENDPOINT

    def get_reasoning_param_name(self) -> str:
        return REASONING_PARAM

    def get_default_thinking(self) -> Dict:
        return {"enabled": True, "reasoning_effort": "high"}

    async def fetch_models(self, api_key: str) -> List[Dict]:
        url = f"{BASE_URL}{MODELS_ENDPOINT}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        models = []
        for item in data.get("data", []):
            models.append({
                "id": item.get("id", ""),
                "name": item.get("name", item.get("id", "")),
                "context_length": item.get("context_length", 128000),
            })
        return models

    def create_model_config(self, api_key: str, model_id: str, model_name: str = "") -> Dict:
        cfg = {
            "name": f"opencode-{model_id.split('/')[-1]}",
            "api_key": api_key,
            "base_url": f"{BASE_URL}/zen/go/v1",
            "model": model_id,
            "temperature": DEFAULT_TEMPERATURE,
            "timeout": DEFAULT_TIMEOUT,
            "vision": False,
            "thinking": self.get_default_thinking(),
        }
        return cfg
