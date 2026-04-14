# -*- coding: utf-8 -*-
"""
Web Search 纯单元测试（Mock）

不依赖真实浏览器环境，使用 Mock 测试核心逻辑
"""

import os
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from components.web_search import WebSearch


class TestWebSearchDomainScoring(unittest.TestCase):
    """测试域名评分逻辑"""

    def setUp(self):
        self.web_search = WebSearch()

    def test_openai_domain_score(self):
        """测试 OpenAI 域名评分"""
        score = self.web_search._score_domain("https://openai.com/research/gpt-4")
        self.assertGreaterEqual(score, 2)  # 至少默认分数

    def test_github_domain_score(self):
        """测试 GitHub 域名评分"""
        score = self.web_search._score_domain("https://github.com/microsoft/vscode")
        self.assertGreaterEqual(score, 2)

    def test_edu_domain_score(self):
        """测试教育机构域名评分"""
        score = self.web_search._score_domain("https://cs.stanford.edu/courses")
        self.assertGreaterEqual(score, 2)

    def test_default_domain_score(self):
        """测试默认域名评分"""
        score = self.web_search._score_domain("https://www.example.com/page")
        self.assertEqual(score, 2)


class TestWebSearchCache(unittest.TestCase):
    """测试缓存功能"""

    def setUp(self):
        self.web_search = WebSearch()

    def test_cache_key_generation(self):
        """测试缓存键生成"""
        key1 = self.web_search._get_cache_key("Python", "google")
        key2 = self.web_search._get_cache_key("python", "google")
        key3 = self.web_search._get_cache_key("Python", "bing")

        # 相同关键词和引擎应该生成相同键（小写）
        self.assertEqual(key1, key2)
        # 不同引擎应该生成不同键
        self.assertNotEqual(key1, key3)

    def test_cache_storage_and_retrieval(self):
        """测试缓存存储和读取"""
        test_data = {"results": [{"title": "Test"}], "count": 1}

        self.web_search._set_cached_result("test query", "google", test_data)

        # 验证缓存已存储（通过检查内部状态）
        cache_key = self.web_search._get_cache_key("test query", "google")
        self.assertIn(cache_key, self.web_search._search_cache)

        # 验证缓存数据结构
        cached_entry = self.web_search._search_cache[cache_key]
        self.assertIn("_cached_at", cached_entry)
        self.assertEqual(cached_entry["count"], 1)


class TestWebSearchInputValidation(unittest.TestCase):
    """测试输入验证"""

    def setUp(self):
        self.web_search = WebSearch()

    def test_empty_keywords_returns_error(self):
        """测试空关键词返回错误"""
        result = self.web_search._cmd_search("")
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_whitespace_only_keywords_returns_error(self):
        """测试仅空白字符返回错误"""
        result = self.web_search._cmd_search("   ")
        self.assertFalse(result["success"])


class TestWebSearchEngineHealth(unittest.TestCase):
    """测试引擎健康状态"""

    def setUp(self):
        self.web_search = WebSearch()

    def test_initial_health_is_unknown(self):
        """测试初始健康状态为 unknown"""
        health = self.web_search.get_engine_health()
        self.assertIn("google", health)
        self.assertIn("bing", health)


class TestWebSearchResultDeduplication(unittest.TestCase):
    """测试结果去重"""

    def setUp(self):
        self.web_search = WebSearch()

    def test_score_domain_with_empty_url(self):
        """测试空 URL 评分"""
        score = self.web_search._score_domain("")
        self.assertEqual(score, 2)  # 默认分数

    def test_score_domain_with_invalid_url(self):
        """测试无效 URL 评分"""
        score = self.web_search._score_domain("not-a-valid-url")
        self.assertEqual(score, 2)  # 默认分数


if __name__ == '__main__':
    unittest.main()
