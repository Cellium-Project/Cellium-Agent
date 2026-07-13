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

    def test_generate_read_full(self):
        result = ToolDescriptionGenerator.generate("read", {"file_path": "/test/file.txt"})
        self.assertIn("file.txt", result)
        self.assertIn("读取", result)

    def test_generate_read_with_target(self):
        result = ToolDescriptionGenerator.generate("read", {"file_path": "/test/file.txt", "target": "def hello"})
        self.assertIn("file.txt", result)

    def test_generate_read_with_needle(self):
        result = ToolDescriptionGenerator.generate("read", {"file_path": "/test/file.txt", "needle": "def foo():"})
        self.assertIn("file.txt", result)

    def test_generate_file_insight_grep(self):
        result = ToolDescriptionGenerator.generate("file", {"command": "insight", "mode": "grep", "query": "hello"})
        self.assertIn("hello", result)

    def test_generate_file_insight_structure(self):
        result = ToolDescriptionGenerator.generate("file", {"command": "insight", "path": "/test/file.txt", "mode": "structure"})
        self.assertIn("file.txt", result)

    def test_generate_edit_replace(self):
        result = ToolDescriptionGenerator.generate("edit", {"file_path": "/test/file.txt", "old_string": "foo", "new_string": "bar"})
        self.assertIn("file.txt", result)

    def test_generate_edit_replace_all(self):
        result = ToolDescriptionGenerator.generate("edit", {"file_path": "/test/file.txt", "old_string": "foo", "new_string": "bar", "replace_all": True})
        self.assertIn("file.txt", result)
        self.assertIn("批量", result)

    def test_generate_file_fs_mkdir(self):
        result = ToolDescriptionGenerator.generate("file", {"command": "fs", "action": "mkdir", "path": "/test/newdir"})
        self.assertIn("newdir", result)

    def test_generate_file_fs_delete(self):
        result = ToolDescriptionGenerator.generate("file", {"command": "fs", "action": "delete", "path": "/test/old.txt"})
        self.assertIn("old.txt", result)

    def test_generate_file_fs_create(self):
        result = ToolDescriptionGenerator.generate("file", {"command": "fs", "action": "create", "path": "/project", "files": {"a.py": "", "b.py": ""}})
        self.assertIn("project", result)

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

    def test_extract_read_desc_context_mode(self):
        ctx = ToolDescriptionGenerator.extract_context("read", {"file_path": "/test.py", "target": "def hello"})
        self.assertIn("read_desc", ctx)
        self.assertIn("test.py", ctx["read_desc"])

    def test_extract_insight_desc_grep_mode(self):
        ctx = ToolDescriptionGenerator.extract_context("file", {"command": "insight", "mode": "grep", "query": "hello"})
        self.assertIn("insight_desc", ctx)
        self.assertIn("hello", ctx["insight_desc"])

    def test_extract_edit_desc_basic(self):
        ctx = ToolDescriptionGenerator.extract_context("edit", {"file_path": "/test.py", "old_string": "foo", "new_string": "bar"})
        self.assertIn("edit_desc", ctx)
        self.assertIn("test.py", ctx["edit_desc"])

    def test_extract_fs_desc_create_action(self):
        ctx = ToolDescriptionGenerator.extract_context("file", {"command": "fs", "action": "create", "path": "/project", "files": {"a.py": "", "b.py": ""}})
        self.assertIn("fs_desc", ctx)
        self.assertIn("project", ctx["fs_desc"])


if __name__ == '__main__':
    unittest.main()
