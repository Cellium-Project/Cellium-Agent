# -*- coding: utf-8 -*-
"""Scheduler - 定时任务调度模块"""

import importlib

_LAZY_EXPORTS = {
    "get_scheduler_manager": "app.core.scheduler.manager",
    "SchedulerManager": "app.core.scheduler.manager",
    "TaskConfig": "app.core.scheduler.manager",
    "ScheduledTask": "app.core.scheduler.manager",
    "get_executor": "app.core.scheduler.executor",
    "start_executor": "app.core.scheduler.executor",
    "SchedulerExecutor": "app.core.scheduler.executor",
}

__all__ = list(_LAZY_EXPORTS.keys())


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
