# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.loop.tool_executor import ToolDescriptionGenerator


class TestToolDescriptionGenerator(unittest.TestCase):
    def test_render_template_simple(self):
        result = ToolDescriptionGenerator.render_template("Hello {name}", {"name": "World"})
        self.assertEqual(result, "Hello World")

    def test_render_template_missing_key(self):
        result = ToolDescriptionGenerator.render_template("Hello {name}", {})
        self.assertEqual(result, "Hello ")

    def test_generate_file_read(self):
        result = ToolDescriptionGenerator.generate("file", {"command": "read", "path": "/test/file.txt"})
        self.assertIn("读取文件", result)
        self.assertIn("file.txt", result)

    def test_generate_file_write(self):
        result = ToolDescriptionGenerator.generate("file", {"command": "write", "path": "/test/file.txt"})
        self.assertIn("写入文件", result)

    def test_generate_memory_search(self):
        result = ToolDescriptionGenerator.generate("memory", {"command": "search", "query": "测试查询"})
        self.assertIn("搜索历史记忆", result)
        self.assertIn("测试查询", result)

    def test_generate_web_search(self):
        result = ToolDescriptionGenerator.generate("web_search", {"command": "search", "query": "Python"})
        self.assertIn("搜索", result)

    def test_generate_with_intent(self):
        result = ToolDescriptionGenerator.generate("file", {"_intent": "自定义意图描述", "path": "/test.txt"})
        self.assertEqual(result, "自定义意图描述")

    def test_generate_read_file_alias(self):
        result = ToolDescriptionGenerator.generate("read_file", {"path": "/test.txt"})
        self.assertIn("读取文件", result)

    def test_generate_default_fallback(self):
        result = ToolDescriptionGenerator.generate("unknown_tool", {"param": "value"})
        self.assertIn("unknown_tool", result)


class TestShellCommandDescription(unittest.TestCase):
    def test_describe_shell_cat(self):
        result = ToolDescriptionGenerator.describe_shell_command("cat /etc/passwd")
        self.assertIn("读取文件", result)

    def test_describe_shell_mkdir(self):
        result = ToolDescriptionGenerator.describe_shell_command("mkdir test_dir")
        self.assertIn("创建目录", result)

    def test_describe_shell_git_clone(self):
        result = ToolDescriptionGenerator.describe_shell_command("git clone https://github.com/test/repo")
        self.assertIn("克隆", result)

    def test_describe_shell_python_install(self):
        result = ToolDescriptionGenerator.describe_shell_command("pip install numpy")
        self.assertIn("安装", result)

    def test_describe_shell_ls(self):
        result = ToolDescriptionGenerator.describe_shell_command("ls -la /home")
        self.assertIn("查看目录", result)

    def test_describe_shell_empty(self):
        result = ToolDescriptionGenerator.describe_shell_command("")
        self.assertIn("执行命令", result)


class TestExtractContext(unittest.TestCase):
    def test_extract_basename(self):
        ctx = ToolDescriptionGenerator.extract_context("file", {"path": "/a/b/c.txt"})
        self.assertEqual(ctx["basename"], "c.txt")

    def test_extract_url_short(self):
        ctx = ToolDescriptionGenerator.extract_context("web_fetch", {"url": "https://example.com/very/long/path"})
        self.assertLessEqual(len(ctx["url_short"]), 50)

    def test_extract_file_count(self):
        ctx = ToolDescriptionGenerator.extract_context("file", {"files": {"a": 1, "b": 2}})
        self.assertEqual(ctx["file_count"], 2)


if __name__ == '__main__':
    unittest.main()
