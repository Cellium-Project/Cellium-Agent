# -*- coding: utf-8 -*-
"""组件市场 HTTP API：参数透传与错误处理"""
import asyncio

import pytest
from fastapi import HTTPException

from app.core.util import components_loader as cl
from app.server.routes import components as routes


class _FakeBuilder:
    def _cmd_market_search(self, **kwargs):
        return {"success": True, "kind": "search", "params": kwargs}

    def _cmd_market_info(self, **kwargs):
        return {"success": True, "kind": "info", "params": kwargs}

    def _cmd_market_install(self, **kwargs):
        return {"success": True, "kind": "install", "params": kwargs}


def test_market_search_passthrough(monkeypatch):
    monkeypatch.setattr(routes, "_component_builder", lambda: _FakeBuilder())
    out = routes.market_search(keyword="pdf", category="automation", tags="a,b", page=2, page_size=5, refresh=True)
    assert out["params"] == {
        "keyword": "pdf",
        "category": "automation",
        "tags": "a,b",
        "page": 2,
        "page_size": 5,
        "refresh": True,
    }


def test_market_info_requires_slug(monkeypatch):
    monkeypatch.setattr(routes, "_component_builder", lambda: _FakeBuilder())
    with pytest.raises(HTTPException) as exc:
        routes.market_info(slug="  ")
    assert exc.value.status_code == 400
    out = routes.market_info(slug="demo", version="1.0.0")
    assert out["params"] == {"slug": "demo", "version": "1.0.0"}


def test_market_install_payload(monkeypatch):
    monkeypatch.setattr(routes, "_component_builder", lambda: _FakeBuilder())
    with pytest.raises(HTTPException) as exc:
        routes.market_install(routes.MarketInstallRequest())
    assert exc.value.status_code == 400
    out = routes.market_install(routes.MarketInstallRequest(slug="demo", version="1.0.0", force=True, dry_run=True))
    assert out["params"] == {
        "slug": "demo",
        "version": "1.0.0",
        "force": True,
        "dry_run": True,
        "install_deps": False,
    }


def test_market_routes_registered():
    paths = {route.path for route in routes.router.routes}
    assert "/api/components/market/search" in paths
    assert "/api/components/market/info" in paths
    assert "/api/components/market/install" in paths


class _FakeCell:
    _source_file = "components/scheduler.py"

    def __init__(self, name):
        self._name = name

    @property
    def cell_name(self):
        return self._name


def test_list_marks_builtin_and_user_components(monkeypatch):
    monkeypatch.setattr(
        cl, "get_all_cells",
        lambda: {"scheduler": _FakeCell("scheduler"), "my_tool": _FakeCell("my_tool")},
    )
    monkeypatch.setattr(
        cl, "get_all_commands",
        lambda: {"scheduler": {"list": "x"}, "my_tool": {"run": "y"}},
    )
    out = asyncio.run(routes.list_components())
    by_name = {c["cell_name"]: c for c in out["components"]}
    assert by_name["scheduler"]["is_builtin"] is True
    assert by_name["scheduler"]["removable"] is False
    assert by_name["my_tool"]["is_builtin"] is False
    assert by_name["my_tool"]["removable"] is True


def test_unload_builtin_forbidden():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.unload_component("scheduler"))
    assert exc.value.status_code == 403
