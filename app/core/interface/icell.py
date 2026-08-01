# -*- coding: utf-8 -*-
"""
组件接口定义
定义所有组件必须实现的统一接口

ICell 提供默认实现，子类可直接继承使用；
继承 ICell 后定义 _cmd_ 方法即可成为可用组件。
"""

import inspect
import logging
from typing import Any, Dict, List

from app.core.exception import CommandNotFoundError

logger = logging.getLogger(__name__)


class ICell:
    """组件统一接口

    所有组件必须实现此接口，以支持统一的命令分发机制。

    使用方式：
        前端调用：pycmd('组件名:命令:参数')
        例如：pycmd('calculator:calc:1+1')
              pycmd('filemanager:read:C:/test.txt')
    """

    COMMAND_PREFIX = "_cmd_"

    @property
    def cell_name(self) -> str:
        """获取组件名称（默认小写类名，子类可覆盖）"""
        return self.__class__.__name__.lower()

    def execute(self, command: str, *args, **kwargs) -> Any:
        """执行命令（自动路由到 _cmd_<command> 方法）"""
        command = command.strip().strip('>"\'')
        method_name = f"{self.COMMAND_PREFIX}{command}"
        if hasattr(self, method_name):
            return getattr(self, method_name)(*args, **kwargs)
        raise CommandNotFoundError(command, self.cell_name)

    def get_commands(self) -> Dict[str, str]:
        """获取可用命令列表 {命令名: 描述}"""
        commands = {}
        for name in dir(self):
            if name.startswith(self.COMMAND_PREFIX):
                cmd_name = name[len(self.COMMAND_PREFIX):]
                method = getattr(self, name)
                if callable(method):
                    doc = method.__doc__ or ""
                    commands[cmd_name] = doc.strip()
        return commands

    def get_command_params(self) -> Dict[str, list]:
        """返回每个命令的参数名列表（用于沙箱模式的参数注入判断）"""
        params_map = {}
        for name in dir(self):
            if name.startswith(self.COMMAND_PREFIX):
                cmd_name = name[len(self.COMMAND_PREFIX):]
                method = getattr(self, name)
                if callable(method):
                    sig = inspect.signature(method)
                    params = [p for p in sig.parameters if p != "self"]
                    params_map[cmd_name] = params
        return params_map
