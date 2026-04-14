# -*- coding: utf-8 -*-
"""
Embedding 搜索性能基准测试（优化版）

优化策略：
  - 使用类级 setUpClass 减少重复初始化
  - 批量插入数据
  - 只测试关键性能点
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


class TestEmbeddingSearchPerformance(unittest.TestCase):
    """测试向量搜索性能 - 使用共享测试数据"""

    @classmethod
    def setUpClass(cls):
        """类级设置 - 只执行一次"""
        cls.test_dir = tempfile.mkdtemp()
        cls.searcher = FTS5MemorySearcher(cls.test_dir)
        cls.repo = MemoryRepository(cls.test_dir, cls.searcher)

        # 预插入测试数据（500条足够测试性能）
        print("\n[性能测试] 准备测试数据...")
        start = time.time()
        for i in range(500):
            cls.repo.upsert_memory(
                title=f"记忆 {i}",
                content=f"这是第 {i} 条测试记忆的内容，包含一些关键词如 Python、测试、性能。",
                category="test",
                schema_type="general",
                memory_key=f"test:{i}",
            )
        print(f"[性能测试] 数据准备完成，耗时 {time.time() - start:.2f}s")

    @classmethod
    def tearDownClass(cls):
        """类级清理"""
        cls.searcher.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_search_500_records_under_30ms(self):
        """500条记录搜索应在30ms内完成"""
        start = time.perf_counter()
        results = self.repo.search("Python 测试", top_k=5)
        elapsed = (time.perf_counter() - start) * 1000

        self.assertTrue(len(results) > 0, "应该返回搜索结果")
        self.assertLess(elapsed, 30, f"500条记录搜索应<30ms，实际{elapsed:.2f}ms")

    def test_search_returns_correct_top_k(self):
        """测试返回指定数量的结果"""
        for k in [1, 3, 5, 10]:
            results = self.repo.search("Python", top_k=k)
            self.assertLessEqual(len(results), k, f"top_k={k} 应返回不超过{k}条结果")

    def test_search_filters_by_category(self):
        """测试分类过滤"""
        results = self.repo.search("Python", top_k=10, category="test")
        for r in results:
            self.assertEqual(r["category"], "test")

    def test_embedding_score_range(self):
        """测试 embedding 分数范围"""
        results = self.repo.search("Python", top_k=5)
        for r in results:
            score = r.get("embedding_score", 0)
            self.assertGreaterEqual(score, 0, "分数应>=0")
            self.assertLessEqual(score, 1, "分数应<=1")

    def test_hybrid_search_combines_scores(self):
        """测试混合搜索结合 FTS 和 Embedding 分数"""
        results = self.repo.search("Python 测试", top_k=5)

        # 检查结果包含分数
        for r in results:
            self.assertIn("score", r, "应有综合分数")
            # 注意：embedding_score 可能不存在（如果分数太低被过滤）
            if "embedding_score" in r:
                self.assertGreaterEqual(r["embedding_score"], 0)
                self.assertLessEqual(r["embedding_score"], 1)


class TestEmbeddingSearchStress(unittest.TestCase):
    """压力测试 - 轻量级"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.searcher = FTS5MemorySearcher(self.test_dir)
        self.repo = MemoryRepository(self.test_dir, self.searcher)

        # 插入100条数据
        for i in range(100):
            self.repo.upsert_memory(
                title=f"记忆 {i}",
                content="Python 测试内容",
                category="test",
                schema_type="general",
                memory_key=f"test:{i}",
            )

    def tearDown(self):
        self.searcher.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_search_after_delete(self):
        """测试删除后搜索正常"""
        # 删除一条
        self.repo.delete_memory(identifier="1")

        # 搜索应正常
        results = self.repo.search("Python", top_k=10)
        self.assertTrue(len(results) > 0)

    def test_empty_query_returns_empty(self):
        """测试空查询返回空结果"""
        results = self.repo.search("", top_k=5)
        self.assertEqual(len(results), 0)

    def test_no_match_query_returns_low_score(self):
        """测试无匹配查询返回低分结果或无结果"""
        results = self.repo.search("xyzabc123不存在的关键词", top_k=5)
        # 可能返回一些低分结果，也可能为空，取决于实现
        if len(results) > 0:
            # 如果有结果，分数应该很低
            for r in results:
                self.assertLess(r.get("score", 1), 0.5, "无匹配查询的结果分数应较低")


if __name__ == '__main__':
    unittest.main()
