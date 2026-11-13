# -*- coding: utf-8 -*-
"""内置工具名与默认组件名的保留规则"""

from app.core.interface.base_cell import BaseCell
from app.core.util import components_loader as cl
from app.core.util.component_tool_registry import ComponentToolRegistry


def _make_cell(cell_name: str, module: str):
    cls = type(f"Fake_{cell_name}", (BaseCell,), {"cell_name": cell_name})
    cls.__module__ = module
    return cls()


def test_user_component_cannot_use_default_component_name():
    reg = ComponentToolRegistry()
    assert reg.register(_make_cell("scheduler", "components.my_evil")) is None
    assert not reg.has("scheduler")


def test_default_component_module_can_use_its_name():
    reg = ComponentToolRegistry()
    assert reg.register(_make_cell("scheduler", "components.scheduler")) is not None
    assert reg.has("scheduler")


def test_user_component_cannot_use_builtin_tool_name():
    reg = ComponentToolRegistry()
    assert reg.register(_make_cell("shell", "components.whatever")) is None
    assert reg.register(_make_cell("config", "components.whatever")) is None
    assert not reg.has("shell")
    assert not reg.has("config")


def test_loader_refuses_to_override_default_component():
    default = _make_cell("scheduler", "components.scheduler")
    other = _make_cell("scheduler", "components.other")
    cl._cell_registry.clear()
    try:
        cl.register_cell(default)
        cl.register_cell(other)
        assert cl.get_cell("scheduler") is default
    finally:
        cl._cell_registry.clear()
