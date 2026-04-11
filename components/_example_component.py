# -*- coding: utf-8 -*-
"""
示例组件 — Agent 自扩展参考模板

【组件铁律示范】
  1. 继承 BaseCell
  2. cell_name = "xxx" （小写，用于命令路由）
  3. 命令方法以 _cmd_ 开头，必须有 docstring
  4. 文件放 components/ 下即自动生效

使用方式：
  - 前端/Agent 调用: cell.execute("命令名", *args)
  - 热加载: 放入目录 → 系统扫描发现 → 自动注册到 settings.yaml

复制此文件，改名为 your_tool.py 即可开始编写新组件。
"""

from typing import Any, Dict
from app.core.interface.base_cell import BaseCell


class ExampleComponent(BaseCell):
    """
    示例组件 — 展示组件规范写法
    
    组件说明: 这是一个计算器示例，演示如何正确实现一个 Cellium 组件。
    """

    @property
    def cell_name(self) -> str:
        """组件标识（必须小写）— 用于全局唯一识别和命令路由"""
        return "calculator"

    # ================================================================
    # 命令方法（_cmd_ 前缀 + docstring 必须有）
    # ================================================================

    def _cmd_calc(self, expression: str) -> Dict[str, Any]:
        """
        安全计算数学表达式
        
        Args:
            expression: 数学表达式，如 "1+2*3"
            
        Returns:
            {"result": 数值结果, "expression": 原始表达式}
        
        使用: execute("calc", "2+3*4")
        """
        # 安全限制：只允许数字和基本运算符
        allowed = set("0123456789+-*/().% ")
        if not all(c in allowed for c in expression):
            return {"error": "表达式包含非法字符", "expression": expression}

        try:
            result = eval(expression)  # noqa: 已做字符过滤
            return {
                "result": result,
                "expression": expression,
                "type": type(result).__name__,
            }
        except Exception as e:
            return {"error": str(e), "expression": expression}

    def _cmd_echo(self, message: str, repeat: int = 1) -> Dict[str, Any]:
        """
        回显消息（可重复多次）
        
        Args:
            message: 要回显的文本
            repeat: 重复次数（默认1次）
        
        使用: execute("echo", "Hello") 或 execute("echo", "Hi", repeat=3)
        """
        result = (message + "\n") * repeat
        return {"output": result.strip(), "repeat": repeat}

    def _cmd_info(self) -> Dict[str, Any]:
        """
        返回组件自身信息（版本、能力列表等）
        
        使用: execute("info")
        """
        commands = self.get_commands()
        return {
            "name": self.cell_name,
            "class": self.__class__.__name__,
            "version": "1.0.0",
            "commands": commands,
            "command_count": len(commands),
        }

    # ================================================================
    # 生命周期钩子（可选）
    # ================================================================

    def on_load(self):
        """组件被加载后调用 — 可用于初始化资源、注册事件等"""
        super().on_load()
        print(f"[{self.cell_name}] 组件已加载就绪")

    def on_unload(self):
        """组件被卸载前调用 — 清理资源"""
        print(f"[{self.cell_name}] 组件正在卸载，清理资源...")
