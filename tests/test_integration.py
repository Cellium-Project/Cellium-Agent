# -*- coding: utf-8 -*-
"""
集成测试 - 验证核心模块集成
"""

import os
import sys
import unittest
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.shell.cellium_shell import CelliumShell
from app.agent.memory.three_layer import ThreeLayerMemory
from app.agent.loop.memory import MemoryManager
from app.core.security.policy import SecurityPolicy, RiskLevel
from app.agent.shell.cellium_shell import check_dangerous_command, classify_command, CommandType


class TestShellMemoryIntegration(unittest.TestCase):
    """Shell 与记忆系统集成测试"""

    def setUp(self):
        self.shell = CelliumShell()
        self.memory_manager = MemoryManager()
        self.test_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.test_dir, "archive"), exist_ok=True)
        with open(os.path.join(self.test_dir, "personality.md"), "w", encoding="utf-8") as f:
            f.write("# AI Assistant\n\nHelpful assistant")
        self.tlm = ThreeLayerMemory(self.test_dir)

    def tearDown(self):
        self.shell.close()
        self.memory_manager.clear()
        self.tlm.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_shell_command_with_memory_save(self):
        """测试 Shell 命令执行后保存到记忆"""
        self.memory_manager.add_user_message("如何查看进程？")

        result = self.shell.run("Get-Process | Select-Object -First 1")

        self.memory_manager.add_assistant_message("使用 Get-Process 查看进程")

        self.assertIn("status", result)

        source_id = self.tlm.save_conversation(
            "如何查看进程？",
            "使用 Get-Process 查看进程",
            messages=self.memory_manager.get_messages()
        )

        self.assertIsNotNone(source_id)

    def test_security_block_integration(self):
        """测试安全拦截集成"""
        result = self.shell.run("Remove-Item -Recurse C:\\Windows\\Temp")
        self.assertIn("error", result)

        result = self.shell.run("Get-Process")
        self.assertEqual(result.get("status"), "success")

    def test_memory_context_prompt_building(self):
        """测试记忆上下文提示词构建"""
        self.memory_manager.add_user_message("之前的问题")
        self.memory_manager.add_assistant_message("之前的回答")

        prompt = self.tlm.build_prompt(
            "新问题",
            session_messages=self.memory_manager.get_messages()
        )

        self.assertIn("新问题", prompt)
        self.assertIn("AI Assistant", prompt)


class TestSecurityShellIntegration(unittest.TestCase):
    """安全策略与 Shell 集成测试"""

    def setUp(self):
        self.shell = CelliumShell()
        self.policy = SecurityPolicy()

    def tearDown(self):
        self.shell.close()

    def test_policy_blocks_dangerous_commands(self):
        """测试策略拦截危险命令"""
        dangerous_commands = [
            "Remove-Item -Recurse C:\\",
            "Format D: /FS:NTFS",
            "Stop-Computer -Force",
            "shutdown /s /t 0",
        ]

        for cmd in dangerous_commands:
            result = self.policy.check_command(cmd)
            self.assertFalse(result["allowed"], f"命令应该被拦截: {cmd}")

    def test_policy_allows_safe_commands(self):
        """测试策略允许安全命令"""
        safe_commands = [
            "Get-Process",
            "Get-Service",
            "Get-Date",
        ]

        for cmd in safe_commands:
            result = self.policy.check_command(cmd)
            self.assertTrue(result["allowed"], f"命令应该被允许: {cmd}")


class TestCommandExecution(unittest.TestCase):
    """命令执行测试"""

    def setUp(self):
        self.shell = CelliumShell()

    def tearDown(self):
        self.shell.close()

    def test_basic_command_execution(self):
        """测试基本命令执行"""
        result = self.shell.run("Get-Process | Select-Object -First 1")
        self.assertTrue(isinstance(result, dict))

    def test_command_with_pipe(self):
        """测试管道命令"""
        result = self.shell.run("Get-Process | Where-Object { $_.CPU -gt 0 } | Select-Object -First 1")
        self.assertTrue(isinstance(result, dict))

    def test_command_classification(self):
        """测试命令分类"""
        read_cmd = classify_command("Get-Process")
        self.assertEqual(read_cmd, CommandType.READ)

        write_cmd = classify_command("rm -rf /tmp/test")
        self.assertEqual(write_cmd, CommandType.WRITE)

    def test_dangerous_pattern_detection(self):
        """测试危险模式检测"""
        dangerous = [
            "rm -rf /",
            "fork(); fork();",
            "dd if=/dev/zero of=/dev/sda",
        ]

        for cmd in dangerous:
            result = check_dangerous_command(cmd)
            self.assertIsNotNone(result, f"应该检测到危险命令: {cmd}")


class TestMemorySystemIntegration(unittest.TestCase):
    """记忆系统集成测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.test_dir, "archive"), exist_ok=True)
        with open(os.path.join(self.test_dir, "personality.md"), "w", encoding="utf-8") as f:
            f.write("# AI Assistant\n\nHelpful assistant")
        self.tlm = ThreeLayerMemory(self.test_dir)
        self.sm = MemoryManager()

    def tearDown(self):
        self.tlm.close()
        self.sm.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_full_conversation_cycle(self):
        """测试完整对话周期"""
        self.sm.add_user_message("如何查看系统信息？")
        self.sm.add_assistant_message("使用 Get-SystemInfo 或 Get-ComputerInfo")

        source_id = self.tlm.save_conversation(
            "如何查看系统信息？",
            "使用 Get-SystemInfo 或 Get-ComputerInfo",
            messages=self.sm.get_messages()
        )

        self.assertIsNotNone(source_id)

        record = self.tlm.archive.get_by_id(source_id)
        self.assertIsNotNone(record)

    def test_context_retrieval(self):
        """测试上下文检索"""
        conversations = [
            ("如何配置网络？", "使用 Set-NetIPAddress"),
            ("如何查看网络配置？", "使用 Get-NetIPAddress"),
        ]

        for user, assistant in conversations:
            self.sm.add_user_message(user)
            self.sm.add_assistant_message(assistant)
            self.tlm.save_conversation(user, assistant, messages=self.sm.get_messages())
            self.sm.clear()


if __name__ == "__main__":
    unittest.main()
