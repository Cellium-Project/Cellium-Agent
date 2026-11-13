# -*- coding: utf-8 -*-
"""文件夹（包）组件热重载后必须注入到工具注册表，且嵌套模块变更可被检测"""

import os
import pathlib
import shutil
import sys
from unittest.mock import patch

import pytest

from app.core.util import components_loader as cl
from app.core.util.component_tool_registry import get_component_tool_registry
from app.core.util.component_watcher import ComponentWatcher

CORE_V1 = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell
from .sub.mod import LABEL


class PkgDemo(BaseCell):
    """包组件演示"""

    @property
    def cell_name(self) -> str:
        return "pkg_demo"

    def _cmd_ping(self) -> dict:
        """ping"""
        return {"label": LABEL}
'''

MOD_V1 = 'LABEL = "v1"\n'
MOD_V2 = 'LABEL = "v2"\n'

INIT_SINGLE = 'from .core import PkgDemo\n\n__all__ = ["PkgDemo"]\n'

CORE_MULTI = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell
from .sub.mod import LABEL


class PkgDemoA(BaseCell):
    """包组件 A"""

    @property
    def cell_name(self) -> str:
        return "pkg_demo_a"

    def _cmd_ping(self) -> dict:
        """ping"""
        return {"label": LABEL}


class PkgDemoB(BaseCell):
    """包组件 B"""

    @property
    def cell_name(self) -> str:
        return "pkg_demo_b"

    def _cmd_ping(self) -> dict:
        """ping"""
        return {"label": LABEL}
'''

INIT_MULTI = 'from .core import PkgDemoA, PkgDemoB\n\n__all__ = ["PkgDemoA", "PkgDemoB"]\n'

CORE_RENAMED = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell


class PkgDemoV2(BaseCell):
    """包组件演示 v2"""

    @property
    def cell_name(self) -> str:
        return "pkg_demo_v2"

    def _cmd_ping(self) -> dict:
        """ping"""
        return {"label": "v2"}
'''

INIT_RENAMED = 'from .core import PkgDemoV2\n\n__all__ = ["PkgDemoV2"]\n'


def _write_pkg(root, multi=False):
    pkg = root / "pkgdemo"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text(INIT_MULTI if multi else INIT_SINGLE, encoding="utf-8")
    (pkg / "core.py").write_text(CORE_MULTI if multi else CORE_V1, encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "mod.py").write_text(MOD_V1, encoding="utf-8")
    return pkg


def _bump(path):
    future = os.path.getmtime(path) + 5
    os.utime(path, (future, future))


@pytest.fixture
def env(tmp_path, monkeypatch):
    user_dir, builtin_dir = _prepare(
        tmp_path, monkeypatch,
        {"pkg_demo", "pkg_demo_a", "pkg_demo_b", "pkg_demo_v2"},
    )

    registry = get_component_tool_registry()
    registry._registry.clear()

    yield user_dir, builtin_dir

    _cleanup(user_dir, builtin_dir)


@pytest.fixture
def sandbox_env(tmp_path, monkeypatch):
    user_dir, builtin_dir = _prepare(tmp_path, monkeypatch, set())

    registry = get_component_tool_registry()
    registry._registry.clear()

    yield user_dir, builtin_dir

    from app.core.util.component_sandbox import ComponentSandbox
    ComponentSandbox.remove_sandbox("pkg_demo")
    _cleanup(user_dir, builtin_dir)


def _prepare(tmp_path, monkeypatch, exempted):
    user_dir = tmp_path / "components"
    builtin_dir = tmp_path / "builtin"
    user_dir.mkdir()
    builtin_dir.mkdir()

    monkeypatch.setattr(cl, "get_components_dir", lambda: user_dir)
    monkeypatch.setattr(cl, "get_builtin_components_dir", lambda: builtin_dir)
    monkeypatch.setattr(cl, "register_to_config", lambda *a, **k: None)
    monkeypatch.setattr(cl, "unregister_from_config", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.core.util.cell_tool_adapter.get_exempted_names", lambda: exempted
    )
    return user_dir, builtin_dir


def _cleanup(user_dir, builtin_dir):
    registry = get_component_tool_registry()
    for name in list(cl._cell_registry.keys()):
        cl.unregister_cell(name)
    cl._loaded_files.clear()
    cl._file_mtimes.clear()
    cl._load_errors.clear()
    registry._registry.clear()
    for mod in [m for m in sys.modules if m == "pkgdemo" or m.startswith("pkgdemo.")]:
        sys.modules.pop(mod, None)
    for path in (str(user_dir), str(builtin_dir)):
        while path in sys.path:
            sys.path.remove(path)


def test_package_component_injected_after_reload(env):
    user_dir, _ = env
    _write_pkg(user_dir)

    report = cl.hot_reload()
    assert any(item["name"] == "pkg_demo" for item in report["added"])

    registry = get_component_tool_registry()
    assert registry.has("pkg_demo")
    names = [d["function"]["name"] for d in registry.get_tool_definitions()]
    assert "pkg_demo" in names


def test_nested_module_change_marks_updated(env):
    user_dir, _ = env
    pkg = _write_pkg(user_dir)
    cl.hot_reload()

    mod = pkg / "sub" / "mod.py"
    mod.write_text(MOD_V2, encoding="utf-8")
    _bump(mod)

    report = cl.hot_reload()
    assert any(item["name"] == "pkg_demo" for item in report["updated"])

    registry = get_component_tool_registry()
    assert registry.has("pkg_demo")
    assert registry.get("pkg_demo").cell._cmd_ping()["label"] == "v2"


def test_watcher_tracks_nested_files_and_triggers_reload(env):
    user_dir, _ = env
    pkg = _write_pkg(user_dir)

    watcher = ComponentWatcher(interval=1.0)
    watcher.COMPONENTS_DIR = user_dir
    watcher.COMPONENTS_DIRS = [user_dir]
    watcher._take_snapshot()

    assert str((pkg / "sub" / "mod.py").resolve()) in watcher._file_snapshot
    assert str((pkg / "core.py").resolve()) in watcher._file_snapshot

    _bump(pkg / "sub" / "mod.py")
    called = {"n": 0}
    with patch.object(watcher, "reload_now", lambda: called.update(n=called["n"] + 1)):
        watcher._scan_once()
    assert called["n"] == 1


def test_agent_loop_tools_definition_contains_package_tool(env):
    user_dir, _ = env
    _write_pkg(user_dir)
    cl.hot_reload()

    from app.agent.loop.agent_loop import AgentLoop
    from app.agent.loop.memory import MemoryManager
    from app.agent.shell.cellium_shell import CelliumShell

    class _FakeLLM:
        async def chat(self, messages, tools=None, **kwargs):
            return None

    shell = CelliumShell()
    try:
        loop = AgentLoop(
            llm_engine=_FakeLLM(),
            shell=shell,
            memory=MemoryManager(),
            enable_heuristics=False,
            enable_learning=False,
        )
        names = [d["function"]["name"] for d in loop._get_tools_definition()]
        assert "pkg_demo" in names
    finally:
        shell.close()


def test_watcher_detects_new_package_dir(env):
    user_dir, _ = env

    watcher = ComponentWatcher(interval=1.0)
    watcher.COMPONENTS_DIR = user_dir
    watcher.COMPONENTS_DIRS = [user_dir]
    watcher._take_snapshot()
    assert watcher._file_snapshot == {}

    _write_pkg(user_dir)
    called = {"n": 0}
    with patch.object(watcher, "reload_now", lambda: called.update(n=called["n"] + 1)):
        watcher._scan_once()
    assert called["n"] == 1


def test_package_component_runs_in_sandbox_and_uses_new_code(sandbox_env):
    user_dir, _ = sandbox_env
    pkg = _write_pkg(user_dir)
    cl.hot_reload()

    from app.core.util.component_sandbox import ComponentSandbox

    try:
        adapter = get_component_tool_registry().get("pkg_demo")
        assert adapter._use_sandbox is True
        assert pathlib.Path(adapter._get_component_source()).resolve() == pkg.resolve()
        assert adapter.execute_with_context({"command": "ping"})["label"] == "v1"

        mod = pkg / "sub" / "mod.py"
        mod.write_text(MOD_V2, encoding="utf-8")
        _bump(mod)
        report = cl.hot_reload()
        assert any(item["name"] == "pkg_demo" for item in report["updated"])

        adapter2 = get_component_tool_registry().get("pkg_demo")
        assert adapter2.execute_with_context({"command": "ping"})["label"] == "v2"
    finally:
        ComponentSandbox.remove_sandbox("pkg_demo")


def test_exported_class_discovered_once(env):
    user_dir, _ = env
    _write_pkg(user_dir)
    items = cl.discover_components()
    assert [i["class_name"] for i in items] == ["PkgDemo"]


def test_multi_class_package_loads_and_updates_all(env):
    user_dir, _ = env
    pkg = _write_pkg(user_dir, multi=True)

    report = cl.hot_reload()
    assert {i["name"] for i in report["added"]} == {"pkg_demo_a", "pkg_demo_b"}

    registry = get_component_tool_registry()
    assert {"pkg_demo_a", "pkg_demo_b"} <= set(registry.get_all_names())

    mod = pkg / "sub" / "mod.py"
    mod.write_text(MOD_V2, encoding="utf-8")
    _bump(mod)
    report = cl.hot_reload()
    assert {i["name"] for i in report["updated"]} == {"pkg_demo_a", "pkg_demo_b"}
    assert registry.get("pkg_demo_a").cell._cmd_ping()["label"] == "v2"
    assert registry.get("pkg_demo_b").cell._cmd_ping()["label"] == "v2"

    shutil.rmtree(pkg)
    report = cl.hot_reload()
    assert {i["name"] for i in report["removed"]} == {"pkg_demo_a", "pkg_demo_b"}
    assert not registry.has("pkg_demo_a")
    assert not registry.has("pkg_demo_b")


def test_renamed_class_replaces_stale_tool(env):
    user_dir, _ = env
    pkg = _write_pkg(user_dir)
    cl.hot_reload()

    registry = get_component_tool_registry()
    assert registry.has("pkg_demo")

    (pkg / "core.py").write_text(CORE_RENAMED, encoding="utf-8")
    (pkg / "__init__.py").write_text(INIT_RENAMED, encoding="utf-8")
    _bump(pkg / "core.py")
    report = cl.hot_reload()

    assert any(i["name"] == "pkg_demo" for i in report["removed"])
    assert any(i["name"] == "pkg_demo_v2" for i in report["updated"])
    assert not registry.has("pkg_demo")
    assert registry.has("pkg_demo_v2")



