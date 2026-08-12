# -*- coding: utf-8 -*-
"""app.server - Web 服务模块"""

import importlib

_LAZY_EXPORTS = {
    "create_app": "app.server.web_server",
}

__all__ = ["create_app"]


def __getattr__(name):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))
