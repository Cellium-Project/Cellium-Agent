# -*- coding: utf-8 -*-
"""
核心模块
按职责分组的子模块：
- bus: 事件总线（EventBus、事件类型、事件模型）
- di: 依赖注入容器
- util: 工具类（日志、多进程管理、组件加载）
- window: 窗口管理（MainWindow）
- interface: 接口定义（ICell、BaseCell）
"""

from .bus.event_bus import event_bus, EventBus, EventBusManager
from .bus.events import EventType
from .bus.event_models import BaseEvent
from .util.mp_manager import (
    MultiprocessManager,
    get_multiprocess_manager,
    run_in_process,
    run_in_process_async
)
from .di.container import (
    DIContainer,
    get_container,
    inject,
    injected,
    AutoInjectMeta,
    setup_di_container
)

__all__ = [
    'event_bus',
    'EventBus',
    'EventType',
    'BaseEvent',
    'MultiprocessManager',
    'get_multiprocess_manager',
    'run_in_process',
    'run_in_process_async',
    'DIContainer',
    'get_container',
    'inject',
    'injected',
    'AutoInjectMeta',
    'setup_di_container'
]
