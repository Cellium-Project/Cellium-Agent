# -*- coding: utf-8 -*-
"""
组件接口定义
定义所有组件必须实现的统一接口
"""

import logging
from abc import abstractmethod
from typing import Any, Dict

logger = logging.getLogger(__name__)


class ICell:
    """组件统一接口

    所有组件必须实现此接口，以支持统一的命令分发机制。

    使用方式：
        前端调用：pycmd('组件名:命令:参数')
        例如：pycmd('calculator:calc:1+1')
              pycmd('filemanager:read:C:/test.txt')
    """

    @property
    @abstractmethod
    def cell_name(self) -> str:
        """获取组件名称

        Returns:
            str: 组件名称（小写字母），用于前端调用标识
        """
        pass

    @abstractmethod
    def execute(self, command: str, *args, **kwargs) -> Any:
        """执行命令

        Args:
            command: 命令名称
            *args: 可变位置参数
            **kwargs: 可变关键字参数

        Returns:
            Any: 命令执行结果（必须是可序列化的）
        """
        pass

    @abstractmethod
    def get_commands(self) -> Dict[str, str]:
        """获取可用命令列表

        Returns:
            Dict[str, str]: {命令名: 命令描述}
        """
        pass
