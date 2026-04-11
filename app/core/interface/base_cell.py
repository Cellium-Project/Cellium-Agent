# -*- coding: utf-8 -*-
"""
BaseCell - 基础组件类
提供自动命令映射、依赖注入和事件支持
"""

from app.core.interface.icell import ICell
from app.core.di.container import AutoInjectMeta
from app.core.bus import event_bus, register_component_handlers
from app.core.exception import CommandNotFoundError
from typing import Any, Dict


class BaseCell(ICell, metaclass=AutoInjectMeta):
    
    COMMAND_PREFIX = "_cmd_"
    
    def __init__(self):
        pass
    
    def on_load(self):
        """组件加载后调用，用于注册事件处理器"""
        register_component_handlers(self)
    
    @property
    def cell_name(self) -> str:
        return self.__class__.__name__.lower()
    
    def execute(self, command: str, *args, **kwargs) -> Any:
        method_name = f"{self.COMMAND_PREFIX}{command}"
        if hasattr(self, method_name):
            return getattr(self, method_name)(*args, **kwargs)
        raise CommandNotFoundError(command, self.cell_name)
    
    def get_commands(self) -> Dict[str, str]:
        commands = {}
        for name in dir(self):
            if name.startswith(self.COMMAND_PREFIX):
                cmd_name = name[len(self.COMMAND_PREFIX):]
                method = getattr(self, name)
                if callable(method):
                    doc = method.__doc__ or ""
                    commands[cmd_name] = doc.strip()
        return commands
    
    @property
    def event_bus(self):
        return event_bus
