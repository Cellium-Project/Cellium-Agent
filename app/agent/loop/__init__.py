# -*- coding: utf-8 -*-
"""app.agent.loop - Agent 主循环模块"""

import importlib

_LAZY_EXPORTS = {
    "AgentLoop": "app.agent.loop.agent_loop",
    "MemoryManager": "app.agent.loop.memory",
    "SessionManager": "app.agent.loop.session_manager",
    "get_session_manager": "app.agent.loop.session_manager",
    "init_session_manager": "app.agent.loop.session_manager",
    "LoopController": "app.agent.loop.loop_controller",
    "LoopEventPublisher": "app.agent.loop.loop_event_publisher",
    "AgentLoopManager": "app.agent.loop.agent_loop_manager",
}


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
