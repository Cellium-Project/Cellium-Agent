# -*- coding: utf-8 -*-
"""
记忆系统 archive 关联功能测试
测试 user_question 存储、search 返回 archive_entry_id、read_archive 读取
"""

import sys
import os
import tempfile
import shutil
import unittest
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.memory.repository import MemoryRepository
from app.agent.memory.archive_store import ArchiveStore
from app.agent.memory.fts5_searcher import FTS5MemorySearcher
from app.agent.memory.three_layer import ThreeLayerMemory
from app.agent.tools.memory_tool import MemoryTool


class TestMemoryArchiveIntegration(unittest.TestCase):
    """测试记忆系统 archive 关联功能"""

    def setUp(self):
        """测试前准备"""
        self.test_dir = tempfile.mkdtemp()
        self.memory_dir = os.path.join(self.test_dir, "memory")
        os.makedirs(self.memory_dir, exist_ok=True)

    def tearDown(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_user_question_with_archive(self):
        """测试 user_question 类型记忆的完整流程"""
        # 创建 repository 和 archive
        searcher = FTS5MemorySearcher(memory_dir=self.memory_dir)
        repo = MemoryRepository(memory_dir=self.memory_dir, searcher=searcher)
        archive = ArchiveStore(base_dir=os.path.join(self.memory_dir, "archive"))

        # 1. 模拟对话并存储到 archive
        session_id = "test_session_001"
        user_input = "如何使用 Python 的 asyncio？"
        assistant_response = """
Python 的 asyncio 是用于编写并发代码的库。基本用法：

1. 使用 async def 定义协程
2. 使用 await 等待异步操作
3. 使用 asyncio.run() 运行主协程
"""

        archive_id = archive.append_messages(
            session_id=session_id,
            messages=[
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": assistant_response}
            ]
        )
        self.assertIsNotNone(archive_id)

        # 2. 存储 user_question 类型记忆
        answer_summary = assistant_response[:200] + "..." if len(assistant_response) > 200 else assistant_response
        result = repo.store_user_question(
            question=user_input,
            answer_summary=answer_summary,
            archive_entry_id=archive_id,
            session_id=session_id
        )
        self.assertTrue(result.get("success"))

        # 3. 搜索记忆（验证返回 archive_entry_id）
        search_results = repo.search("Python asyncio", top_k=5)

        found = False
        for item in search_results:
            metadata = item.get("metadata", {})
            if metadata.get("memory_type") == "user_question":
                found = True
                entry_id = metadata.get("archive_entry_id")
                self.assertEqual(entry_id, archive_id)
                break

        self.assertTrue(found, "应该找到 user_question 类型记忆")

        # 4. 使用 archive_entry_id 读取助手回复
        record = archive.get_by_id(archive_id)
        self.assertIsNotNone(record)

        messages = record.get("messages", [])
        assistant_msg = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                assistant_msg = msg.get("content", "")
                break

        self.assertIsNotNone(assistant_msg)
        self.assertIn("asyncio", assistant_msg)

    def test_memory_tool_integration(self):
        """测试 memory_tool 层面的集成（真实用户流程）"""
        # 1. 创建 ThreeLayerMemory 和 MemoryTool
        tlm = ThreeLayerMemory(memory_dir=self.memory_dir)
        tool = MemoryTool(three_layer_memory=tlm)

        # 2. 使用 persist_session 存储对话
        session_id = "test_tool_session"
        user_input = "什么是机器学习？"
        response = """
机器学习是人工智能的一个分支，让计算机能够从数据中学习而无需明确编程。

主要类型：
- 监督学习
- 无监督学习  
- 强化学习
"""

        entry_id = tlm.persist_session(
            user_input=user_input,
            response=response,
            session_id=session_id
        )
        self.assertIsNotNone(entry_id)

        # 3. 使用 memory.search 搜索（真实流程）
        time.sleep(0.5)  # 等待索引刷新
        search_result = tool._cmd_search("机器学习")

        self.assertTrue(search_result.get("success"))
        items = search_result.get("results", [])
        self.assertGreater(len(items), 0, "应该找到至少一条结果")

        # 查找带 archive_entry_id 的结果
        target_item = None
        for item in items:
            if "archive_entry_id" in item:
                target_item = item
                break

        self.assertIsNotNone(target_item, "应该找到带 archive_entry_id 的结果")
        self.assertIn("hint", target_item)
        self.assertIn("read_archive", target_item["hint"])

        # 4. 根据 hint 使用 memory.read_archive 查看
        ae_id = target_item["archive_entry_id"]
        read_result = tool._cmd_read_archive(entry_id=ae_id)

        self.assertTrue(read_result.get("success"))
        answer = read_result.get("answer", "")
        self.assertIn("机器学习", answer)


if __name__ == "__main__":
    unittest.main()
