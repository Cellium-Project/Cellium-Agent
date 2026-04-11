# -*- coding: utf-8 -*-
"""
微内核集成测试 - 验证 BaseTool / ShellTool 核心功能
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.tools.shell_tool import ShellTool
from app.agent.tools.base_tool import BaseTool


class TestBaseTool(unittest.TestCase):
    """测试 BaseTool 基类"""

    def test_tool_name(self):
        """测试工具名称"""
        class TestTool(BaseTool):
            @property
            def name(self) -> str:
                return "test_tool"

            def execute(self, **kwargs):
                return {"result": "ok"}

        tool = TestTool()
        self.assertEqual(tool.name, "test_tool")

    def test_tool_execute(self):
        """测试工具执行"""
        class TestTool(BaseTool):
            @property
            def name(self) -> str:
                return "test_tool"

            def execute(self, **kwargs):
                return {"result": "ok"}

        tool = TestTool()
        result = tool.execute()
        self.assertEqual(result["result"], "ok")


class TestShellTool(unittest.TestCase):
    """测试 ShellTool"""

    def test_shell_tool_execute(self):
        """测试 Shell 工具执行"""
        tool = ShellTool()
        result = tool.execute(command="Get-Process | Select-Object -First 1")
        self.assertTrue(isinstance(result, dict))

    def test_shell_tool_with_timeout(self):
        """测试带超时的命令执行"""
        tool = ShellTool()
        result = tool.execute(command="Start-Sleep -Seconds 1", timeout=5)
        self.assertTrue(isinstance(result, dict))


if __name__ == "__main__":
    unittest.main()
