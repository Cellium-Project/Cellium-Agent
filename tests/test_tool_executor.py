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

    def test_generate_sub_agent_parallel(self):
        result = ToolDescriptionGenerator.generate("sub_agent", {
            "command": "parallel",
            "tasks": [{"name": "a", "task": "拆 A"}, {"name": "b", "task": "拆 B"},
                      {"name": "c", "task": "拆 C"}],
        })
        self.assertIn("并行运行 3 个子 Agent", result)
        self.assertIn("a, b, c", result)
        self.assertNotIn("正在调用 sub_agent", result)

    def test_generate_sub_agent_spawn_json(self):
        result = ToolDescriptionGenerator.generate("sub_agent", {
            "command": "spawn",
            "tasks": '[{"name": "s1", "task": "t1"}, {"name": "s2", "task": "t2"}]',
        })
        self.assertIn("后台运行 2 个子 Agent", result)
        self.assertIn("s1, s2", result)

    def test_generate_ls_and_glob(self):
        self.assertIn("列出目录：src", ToolDescriptionGenerator.generate(
            "ls", {"path": "/project/src"}))
        self.assertIn("列出目录：当前目录", ToolDescriptionGenerator.generate("ls", {}))
        self.assertIn("查找文件：**/*.py", ToolDescriptionGenerator.generate(
            "glob", {"pattern": "**/*.py"}))
        self.assertNotIn("正在调用 glob", ToolDescriptionGenerator.generate("glob", {"pattern": "**/*.py"}))

    def test_generate_im_file_tools(self):
        g = ToolDescriptionGenerator.generate
        self.assertIn("从飞书下载文件：report.pdf", g("feishu_files", {
            "command": "download", "filename": "report.pdf"}))
        self.assertIn("发送文件到飞书：a.txt", g("feishu_files", {
            "command": "send_file", "file_path": "/tmp/a.txt"}))
        self.assertIn("从 Telegram 下载文件：b.zip", g("telegram_files", {
            "command": "download", "filename": "b.zip"}))
        self.assertIn("列出 Telegram 下载文件", g("telegram_files", {"command": "list"}))

    def test_generate_scheduler_and_skill_tools(self):
        g = ToolDescriptionGenerator.generate
        self.assertIn("创建每日定时任务：日报", g("scheduler", {
            "command": "create_daily", "name": "日报", "time": "09:00"}))
        self.assertIn("立即执行定时任务：t0001", g("scheduler", {
            "command": "run_now", "task_id": "t0001"}))
        self.assertIn("正在查看定时任务列表", g("scheduler", {"command": "list"}))
        self.assertIn("安装 Skill：git_helper", g("skill_installer", {
            "command": "install", "name": "git_helper"}))
        self.assertIn("搜索 Skill：git", g("skill_manager", {
            "command": "search", "query": "git"}))
        self.assertNotIn("正在调用 skill_manager", g("skill_manager", {"command": "list"}))

    def test_generate_sub_agent_single_and_commands(self):
        self.assertIn("状态：w1", ToolDescriptionGenerator.generate(
            "sub_agent", {"command": "status", "name": "w1"}))
        self.assertIn("结论：w1", ToolDescriptionGenerator.generate(
            "sub_agent", {"command": "results", "name": "w1"}))
        self.assertIn("取消子 Agent：w1", ToolDescriptionGenerator.generate(
            "sub_agent", {"command": "cancel", "name": "w1"}))
        self.assertIn("列表", ToolDescriptionGenerator.generate("sub_agent", {"command": "list"}))


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
