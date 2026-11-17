import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from app.server.routes.config import CustomModelsRequest, fetch_custom_models


class _Client:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        return httpx.Response(
            self.status,
            json=self.payload,
            request=httpx.Request("GET", url),
        )


def test_custom_models_parses_context(monkeypatch):
    payload = {
        "data": [
            {"id": "m1", "name": "M1", "limit": {"context": 100000}},
            {"id": "m2", "context_window": 32000},
        ]
    }
    with patch("httpx.AsyncClient", lambda *a, **kw: _Client(200, payload)):
        result = asyncio.run(fetch_custom_models(CustomModelsRequest(base_url="https://x", api_key="k")))
    assert result["models"] == [
        {"id": "m1", "name": "M1", "context_length": 100000},
        {"id": "m2", "name": "m2", "context_length": 32000},
    ]


def test_custom_models_failure_raises():
    with patch("httpx.AsyncClient", lambda *a, **kw: _Client(404, {})):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(fetch_custom_models(CustomModelsRequest(base_url="https://x", api_key="k")))
    assert exc.value.status_code == 502


def test_custom_models_opencode_host_fallback():
    urls = []

    class _FallbackClient(_Client):
        async def get(self, url, headers=None):
            urls.append(url)
            if url.endswith("/zen/go/v1/models"):
                return httpx.Response(
                    200,
                    json={"data": [{"id": "m", "limit": {"context": 1000000}}]},
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(404, request=httpx.Request("GET", url))

    with patch("httpx.AsyncClient", lambda *a, **kw: _FallbackClient()):
        result = asyncio.run(fetch_custom_models(CustomModelsRequest(base_url="https://opencode.ai", api_key="oc_sk_x")))

    assert result["models"][0]["context_length"] == 1000000
    assert urls[-1] == "https://opencode.ai/zen/go/v1/models"


def test_custom_models_opencode_key_overrides_wrong_base():
    urls = []

    class _FallbackClient(_Client):
        async def get(self, url, headers=None):
            urls.append(url)
            if url.endswith("/zen/go/v1/models"):
                return httpx.Response(
                    200,
                    json={"data": [{"id": "m", "context_length": 128000}]},
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(401, request=httpx.Request("GET", url))

    with patch("httpx.AsyncClient", lambda *a, **kw: _FallbackClient()):
        result = asyncio.run(fetch_custom_models(CustomModelsRequest(base_url="https://api.openai.com/v1", api_key="oc_sk_x")))

    assert result["models"][0]["context_length"] == 128000
    assert urls[-1] == "https://opencode.ai/zen/go/v1/models"
