# -*- coding: utf-8 -*-
"""
MemoryRepository 纯单元测试

优化策略：
  - 使用类级 setUpClass 共享数据库连接
  - 减少重复的数据库创建/销毁
"""

import os
import sys
import unittest
import tempfile
import shutil
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.memory.repository import MemoryRepository
from app.agent.memory.fts5_searcher import FTS5MemorySearcher


class TestMemoryRepositoryCRUD(unittest.TestCase):
    """测试增删改查"""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.searcher = FTS5MemorySearcher(cls.test_dir)
        cls.repo = MemoryRepository(cls.test_dir, cls.searcher)

    @classmethod
    def tearDownClass(cls):
        cls.searcher.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        # 每个测试前清理数据，但保留连接
        self._cleanup_data()

    def _cleanup_data(self):
        """清理所有测试数据"""
        result = self.repo.list_memories(limit=1000)
        for item in result.get("items", []):
            self.repo.delete_memory(identifier=item["id"])

    def test_upsert_creates_new_memory(self):
        """测试创建新记忆"""
        result = self.repo.upsert_memory(
            title="测试标题",
            content="测试内容",
            category="test",
            schema_type="general",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "created")
        self.assertIn("id", result)

    def test_upsert_merges_by_memory_key(self):
        """测试相同 memory_key 合并"""
        self.repo.upsert_memory(
            title="偏好",
            content="喜欢Python",
            category="user_info",
            schema_type="profile",
            memory_key="profile:lang",
        )
        second = self.repo.upsert_memory(
            title="偏好",
            content="喜欢简洁",
            category="user_info",
            schema_type="profile",
            memory_key="profile:lang",
        )

        self.assertEqual(second["action"], "merged")

        # 验证合并后的内容
        result = self.repo.list_memories(schema_type="profile")
        items = result["items"]
        self.assertEqual(len(items), 1)
        self.assertIn("Python", items[0]["content"])
        self.assertIn("简洁", items[0]["content"])

    def test_upsert_validates_required_fields(self):
        """测试必填字段验证"""
        result = self.repo.upsert_memory(title="", content="内容")
        self.assertFalse(result["success"])

        result = self.repo.upsert_memory(title="标题", content="")
        self.assertFalse(result["success"])

    def test_get_record_returns_record(self):
        """测试获取单条记录"""
        created = self.repo.upsert_memory(
            title="测试", content="内容", category="test"
        )

        record = self.repo.get_record(created["id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["title"], "测试")

    def test_get_record_returns_none_for_invalid_id(self):
        """测试无效ID返回None"""
        record = self.repo.get_record("invalid-id-99999")
        self.assertIsNone(record)

    def test_update_memory_updates_fields(self):
        """测试更新记忆"""
        created = self.repo.upsert_memory(
            title="旧标题", content="旧内容", category="test"
        )

        updated = self.repo.update_memory(
            identifier=created["id"],
            title="新标题",
            content="新内容",
        )

        self.assertTrue(updated["success"])

        record = self.repo.get_record(created["id"])
        self.assertEqual(record["title"], "新标题")

    def test_delete_memory_marks_deleted(self):
        """测试删除标记为deleted"""
        created = self.repo.upsert_memory(
            title="测试", content="内容", category="test"
        )

        deleted = self.repo.delete_memory(identifier=created["id"])
        self.assertTrue(deleted["success"])

        record = self.repo.get_record(created["id"])
        self.assertEqual(record["status"], "deleted")

    def test_forget_memories_marks_forgotten(self):
        """测试遗忘标记为forgotten"""
        created = self.repo.upsert_memory(
            title="测试", content="内容", category="test",
            memory_key="test:forget"
        )

        forgotten = self.repo.forget_memories(query="测试")
        self.assertTrue(forgotten["success"])

        record = self.repo.get_record(created["id"])
        self.assertEqual(record["status"], "forgotten")


class TestMemoryRepositorySearch(unittest.TestCase):
    """测试搜索功能"""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.searcher = FTS5MemorySearcher(cls.test_dir)
        cls.repo = MemoryRepository(cls.test_dir, cls.searcher)

        # 准备测试数据（只创建一次）
        cls.repo.upsert_memory(
            title="Python 教程",
            content="Python 是一种编程语言",
            category="tech",
            schema_type="general",
            tags="python,programming",
        )
        cls.repo.upsert_memory(
            title="JavaScript 指南",
            content="JavaScript 用于网页开发",
            category="tech",
            schema_type="general",
            tags="javascript,web",
        )
        cls.repo.upsert_memory(
            title="烹饪技巧",
            content="如何煮出好吃的米饭",
            category="life",
            schema_type="general",
            tags="cooking,food",
        )

    @classmethod
    def tearDownClass(cls):
        cls.searcher.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_search_by_keyword(self):
        """测试关键词搜索"""
        results = self.repo.search("Python", top_k=5)
        self.assertTrue(len(results) > 0)
        self.assertIn("Python", results[0]["title"])

    def test_search_filters_by_category(self):
        """测试分类过滤"""
        results = self.repo.search("开发", top_k=5, category="tech")
        for r in results:
            self.assertEqual(r["category"], "tech")

    def test_search_respects_top_k(self):
        """测试 top_k 限制"""
        results = self.repo.search("教程", top_k=2)
        self.assertLessEqual(len(results), 2)

    def test_search_returns_score(self):
        """测试返回分数"""
        results = self.repo.search("Python", top_k=1)
        self.assertEqual(len(results), 1)
        self.assertIn("score", results[0])
        self.assertGreater(results[0]["score"], 0)


class TestMemoryRepositorySensitive(unittest.TestCase):
    """测试敏感信息处理"""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.searcher = FTS5MemorySearcher(cls.test_dir)
        cls.repo = MemoryRepository(cls.test_dir, cls.searcher)

    @classmethod
    def tearDownClass(cls):
        cls.searcher.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        self._cleanup_data()

    def _cleanup_data(self):
        result = self.repo.list_memories(limit=1000)
        for item in result.get("items", []):
            self.repo.delete_memory(identifier=item["id"])

    def test_detects_api_key_as_sensitive(self):
        """测试检测 API Key 为敏感信息"""
        result = self.repo.upsert_memory(
            title="配置",
            content="api_key = secret1234567890",
            category="test",
        )
        self.assertTrue(result["success"])
        # 验证敏感标记
        self.assertTrue(result["sensitive"])
        # 验证记录中的内容被脱敏
        record = self.repo.get_record(result["id"])
        self.assertIn("REDACTED", record["content"])

    def test_blocks_private_key(self):
        """测试阻止私钥存储"""
        result = self.repo.upsert_memory(
            title="密钥",
            content="-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC...",
            category="test",
        )
        self.assertFalse(result["success"])

    def test_excludes_sensitive_from_search_by_default(self):
        """测试默认搜索排除敏感信息"""
        self.repo.upsert_memory(title="普通记忆", content="这是普通内容", category="test")
        self.repo.upsert_memory(
            title="敏感记忆", content="api_key = secret123", category="test"
        )

        results = self.repo.search("记忆", top_k=10)
        for r in results:
            self.assertFalse(r.get("sensitive", False))


class TestMemoryRepositoryList(unittest.TestCase):
    """测试列表查询"""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.searcher = FTS5MemorySearcher(cls.test_dir)
        cls.repo = MemoryRepository(cls.test_dir, cls.searcher)

        # 准备20条数据（只创建一次）
        for i in range(20):
            cls.repo.upsert_memory(
                title=f"记忆 {i}",
                content=f"内容 {i}",
                category="test" if i < 10 else "other",
                schema_type="general",
            )

    @classmethod
    def tearDownClass(cls):
        cls.searcher.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_list_returns_paginated_result(self):
        """测试返回分页结果"""
        result = self.repo.list_memories(limit=5, offset=0)
        self.assertIn("items", result)
        self.assertIn("total", result)
        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(result["total"], 20)

    def test_list_pagination_works(self):
        """测试分页工作正常"""
        page1 = self.repo.list_memories(limit=5, offset=0)
        page2 = self.repo.list_memories(limit=5, offset=5)

        page1_ids = {r["id"] for r in page1["items"]}
        page2_ids = {r["id"] for r in page2["items"]}
        self.assertEqual(len(page1_ids & page2_ids), 0)

    def test_list_filters_by_category(self):
        """测试列表分类过滤"""
        result = self.repo.list_memories(category="test")
        self.assertEqual(result["total"], 10)


if __name__ == '__main__':
    unittest.main()
