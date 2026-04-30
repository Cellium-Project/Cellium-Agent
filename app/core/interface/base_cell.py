# -*- coding: utf-8 -*-
"""
BaseCell - 基础组件类
提供自动命令映射、依赖注入和事件支持
"""

import logging

from app.core.interface.icell import ICell
from app.core.di.container import AutoInjectMeta
from app.core.bus import event_bus, register_component_handlers
from app.core.exception import CommandNotFoundError
from typing import Any, Dict


class BaseCell(ICell, metaclass=AutoInjectMeta):

    COMMAND_PREFIX = "_cmd_"

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def on_load(self):
        """组件加载后调用，用于注册事件处理器"""
        register_component_handlers(self)
    
    @property
    def cell_name(self) -> str:
        return self.__class__.__name__.lower()
    
    def execute(self, command: str, *args, **kwargs) -> Any:
        command = command.strip().strip('>"\'')
        method_name = f"{self.COMMAND_PREFIX}{command}"
        if hasattr(self, method_name):
            return getattr(self, method_name)(*args, **kwargs)
        raise CommandNotFoundError(command, self.cell_name)
    
    def execute_with_context(self, arguments: Dict[str, Any], session_id: str = None, platform_context: Dict[str, Any] = None) -> Any:
        """带上下文执行命令（由 ToolExecutor 调用）
        
        Args:
            arguments: 命令参数字典，包含 'command' 和其他参数
            session_id: 当前会话 ID
            platform_context: 平台上下文（如 target_id）
        """
        command = arguments.get("command", "")
        if not command:
            return {"error": "Missing 'command' in arguments"}
        
        command = command.strip().strip('>"\'')
        method_name = f"{self.COMMAND_PREFIX}{command}"
        
        if not hasattr(self, method_name):
            raise CommandNotFoundError(command, self.cell_name)
        
        kwargs = {k: v for k, v in arguments.items() if k != "command"}
        
        if session_id:
            kwargs["session_id"] = session_id
        
        if platform_context:
            kwargs["platform_context"] = platform_context
        
        method = getattr(self, method_name)
        return method(**kwargs)
    
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
