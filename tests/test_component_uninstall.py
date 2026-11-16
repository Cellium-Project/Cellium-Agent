# -*- coding: utf-8 -*-
"""组件卸载：单文件 / 包目录都要能连同磁盘文件一起删掉"""
import asyncio
import sys

import pytest
from fastapi import HTTPException

from app.core.util import cell_tool_adapter
from app.core.util import component_sandbox
from app.core.util import component_tool_registry as ctr
from app.core.util import components_loader as cl
from app.server.routes import components as routes


class _FakeRegistry:
    def __init__(self):
        self.size = 0
        self.unregistered = []

    def unregister(self, name):
        self.unregistered.append(name)
        return True

    def sync_from_components_loader(self):
        return None


class _FakeCell:
    """最小可卸载组件：只需要 cell_name / _source_file / 可选 on_unload"""

    def __init__(self, name, source):
        self.cell_name = name
        self._source_file = str(source)

    def on_unload(self):
        self.unloaded_hook = True


@pytest.fixture
def comp_env(tmp_path, monkeypatch):
    """隔离组件目录与全局状态，避免测试污染真实 components/"""
    user_dir = tmp_path / "components"
    builtin_dir = tmp_path / "builtin"
    user_dir.mkdir()
    builtin_dir.mkdir()

    monkeypatch.setattr(cl, "get_components_dir", lambda: user_dir)
    monkeypatch.setattr(cl, "get_builtin_components_dir", lambda: builtin_dir)
    monkeypatch.setattr(cl, "_cell_registry", {})
    monkeypatch.setattr(cl, "_component_classes", {})
    monkeypatch.setattr(cl, "_loaded_files", set())
    monkeypatch.setattr(cl, "_file_mtimes", {})
    monkeypatch.setattr(cl, "_load_errors", {})
    monkeypatch.setattr(cl, "load_settings", lambda: {})
    monkeypatch.setattr(cl, "unregister_from_config", lambda *a, **k: True)

    fake = _FakeRegistry()
    monkeypatch.setattr(ctr, "get_component_tool_registry", lambda: fake)
    monkeypatch.setattr(component_sandbox.ComponentSandbox, "get_all_sandbox_names", staticmethod(lambda: set()))
    monkeypatch.setattr(cell_tool_adapter, "load_sandbox_whitelist", lambda: set())
    monkeypatch.setattr(cell_tool_adapter, "save_sandbox_whitelist", lambda wl: None)

    yield user_dir, builtin_dir, fake

    # 清理包扫描留下的 sys.path / sys.modules 污染
    for entry in (str(user_dir), str(builtin_dir)):
        while entry in sys.path:
            sys.path.remove(entry)
    for key in [k for k in list(sys.modules) if k.startswith(("demo_pkg", "demo_tool"))]:
        sys.modules.pop(key, None)


def test_remove_component_deletes_single_file(comp_env):
    user_dir, _builtin, fake = comp_env
    py = user_dir / "demo_tool.py"
    py.write_text("# demo\n", encoding="utf-8")

    cl._cell_registry["demo_tool"] = _FakeCell("demo_tool", py)
    cl._loaded_files.add(str(py))

    out = cl.remove_component("demo_tool")

    assert out["success"] is True
    assert out["kind"] == "file"
    assert out["deleted"] is True
    assert py.exists() is False
    assert "demo_tool" not in cl._cell_registry
    assert str(py) not in cl._loaded_files
    assert fake.unregistered == ["demo_tool"]


def test_remove_component_deletes_package_dir(comp_env):
    user_dir, _builtin, fake = comp_env
    pkg = user_dir / "demo_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("", encoding="utf-8")

    cl._cell_registry["demo_pkg"] = _FakeCell("demo_pkg", pkg)
    cl._loaded_files.add(str(pkg))

    out = cl.remove_component("demo_pkg")

    assert out["success"] is True
    assert out["kind"] == "package"
    assert out["deleted"] is True
    assert pkg.exists() is False
    assert fake.unregistered == ["demo_pkg"]


def test_remove_component_dry_run_keeps_files(comp_env):
    user_dir, _builtin, _fake = comp_env
    py = user_dir / "demo_tool.py"
    py.write_text("# demo\n", encoding="utf-8")
    cl._cell_registry["demo_tool"] = _FakeCell("demo_tool", py)

    out = cl.remove_component("demo_tool", dry_run=True)

    assert out["success"] is True
    assert out["deleted"] is False
    assert py.exists() is True
    assert "demo_tool" in cl._cell_registry


def test_remove_component_keep_files(comp_env):
    user_dir, _builtin, _fake = comp_env
    py = user_dir / "demo_tool.py"
    py.write_text("# demo\n", encoding="utf-8")
    cl._cell_registry["demo_tool"] = _FakeCell("demo_tool", py)

    out = cl.remove_component("demo_tool", delete_files=False)

    assert out["success"] is True
    assert out["deleted"] is False
    assert py.exists() is True
    assert "demo_tool" not in cl._cell_registry


def test_remove_component_refuses_builtin(comp_env):
    _user, _builtin, fake = comp_env
    out = cl.remove_component("scheduler")

    assert out["success"] is False
    assert out["reason"] == "builtin"
    assert fake.unregistered == []


def test_remove_component_refuses_path_outside_components_dir(comp_env, tmp_path):
    user_dir, _builtin, _fake = comp_env
    outside = tmp_path / "outside.py"
    outside.write_text("# x\n", encoding="utf-8")
    cl._cell_registry["outside_tool"] = _FakeCell("outside_tool", outside)

    out = cl.remove_component("outside_tool")

    # 目录外一律不删文件，但注销照常完成
    assert out["success"] is True
    assert out["deleted"] is False
    assert "delete_skipped" in out
    assert outside.exists() is True
    assert "outside_tool" not in cl._cell_registry


def test_remove_component_clears_market_record(comp_env):
    user_dir, _builtin, _fake = comp_env
    py = user_dir / "vision.py"
    py.write_text("# vision\n", encoding="utf-8")
    record = user_dir / cl.MARKET_RECORD_FILE
    record.write_text(
        '{"cellium-vision": {"name": "vision", "target": "%s", "version": "1.0.0"}}'
        % str(py).replace("\\", "\\\\"),
        encoding="utf-8",
    )
    cl._cell_registry["vision"] = _FakeCell("vision", py)

    out = cl.remove_component("vision")

    assert out["deleted"] is True
    assert "market:cellium-vision" in out["cleaned"]
    assert "cellium-vision" not in record.read_text(encoding="utf-8")


def test_remove_component_not_found(comp_env):
    out = cl.remove_component("no_such_component")
    assert out["success"] is False
    assert out["reason"] == "not_found"


def test_remove_component_resolves_by_class_name(comp_env):
    """未加载组件也能按类名/文件名定位并删除"""
    user_dir, _builtin, _fake = comp_env
    pkg = user_dir / "demo_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from app.core.interface.base_cell import BaseCell\n\n\n"
        "class DemoPkgCell(BaseCell):\n"
        "    @property\n"
        "    def cell_name(self):\n"
        "        return 'demo_pkg'\n",
        encoding="utf-8",
    )

    out = cl.remove_component("DemoPkgCell")

    assert out["success"] is True
    assert out["deleted"] is True
    assert pkg.exists() is False


# ---------------- HTTP 层 ----------------

def test_delete_route_returns_ok_and_deletes(comp_env):
    user_dir, _builtin, _fake = comp_env
    py = user_dir / "demo_tool.py"
    py.write_text("# demo\n", encoding="utf-8")
    cl._cell_registry["demo_tool"] = _FakeCell("demo_tool", py)

    result = asyncio.run(routes.unload_component("demo_tool"))

    assert result["status"] == "ok"
    assert result["deleted"] is True
    assert py.exists() is False


def test_delete_route_builtin_forbidden(comp_env):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.unload_component("scheduler"))
    assert exc.value.status_code == 403


def test_delete_route_not_found(comp_env):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.unload_component("no_such_component"))
    assert exc.value.status_code == 404


def test_delete_route_keep_files_flag(comp_env):
    user_dir, _builtin, _fake = comp_env
    py = user_dir / "demo_tool.py"
    py.write_text("# demo\n", encoding="utf-8")
    cl._cell_registry["demo_tool"] = _FakeCell("demo_tool", py)

    result = asyncio.run(routes.unload_component("demo_tool", delete_files=False))

    assert result["status"] == "ok"
    assert result["deleted"] is False
    assert py.exists() is True
