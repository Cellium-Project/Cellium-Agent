# -*- coding: utf-8 -*-
"""
WebSearch 组件单元测试
"""

import os
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from components.web_search import WebSearch


class TestWebSearch(unittest.TestCase):
    """测试 WebSearch 组件"""

    def setUp(self):
        """测试前准备"""
        self.web_search = WebSearch()
        self.web_search._page = Mock()

    def test_score_domain_trusted(self):
        """测试域名评分 - 可信域名"""
        score = self.web_search._score_domain("https://openai.com/research")
        self.assertEqual(score, 10)

    def test_score_domain_edu(self):
        """测试域名评分 - 教育机构"""
        # stanford.edu 在 TRUSTED_DOMAINS 中定义为 9 分
        score = self.web_search._score_domain("https://cs.stanford.edu/research")
        self.assertEqual(score, 9)

    def test_score_domain_gov(self):
        """测试域名评分 - 政府机构"""
        score = self.web_search._score_domain("https://www.nasa.gov/news")
        self.assertEqual(score, 6)

    def test_score_domain_org(self):
        """测试域名评分 - 非营利组织"""
        score = self.web_search._score_domain("https://www.wikipedia.org")
        self.assertEqual(score, 4)

    def test_score_domain_default(self):
        """测试域名评分 - 默认分数"""
        score = self.web_search._score_domain("https://www.example.com")
        self.assertEqual(score, 2)

    def test_extract_links_from_bing_normal(self):
        """测试从 Bing 提取链接 - 正常情况"""
        # 模拟页面元素
        mock_item = Mock()
        mock_h2 = Mock()
        mock_link = Mock()
        mock_link.attr.return_value = "https://example.com/article"
        mock_link.text = "Example Article Title"
        mock_h2.ele.return_value = mock_link
        mock_item.ele.side_effect = [mock_h2, mock_link]

        self.web_search._page.eles.return_value = [mock_item]

        links = self.web_search._extract_links_from_bing(self.web_search._page)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['url'], "https://example.com/article")
        self.assertEqual(links[0]['title'], "Example Article Title")

    def test_extract_links_from_bing_empty(self):
        """测试从 Bing 提取链接 - 空结果"""
        self.web_search._page.eles.return_value = []
        self.web_search._page.html = "<html><body>No results</body></html>"

        links = self.web_search._extract_links_from_bing(self.web_search._page)

        self.assertEqual(len(links), 0)

    @unittest.skip("需要浏览器环境，跳过 URL 构造测试")
    def test_search_url_construction_with_future_year(self):
        """测试搜索 URL 构建 - 包含未来年份"""
        # 此测试需要实际的浏览器环境，跳过
        pass

    @unittest.skip("需要浏览器环境，跳过 URL 构造测试")
    def test_search_url_construction_without_year(self):
        """测试搜索 URL 构建 - 不包含年份"""
        # 此测试需要实际的浏览器环境，跳过
        pass

    def test_search_empty_keywords(self):
        """测试搜索 - 空关键词"""
        result = self.web_search._cmd_search("")
        self.assertFalse(result['success'])
        self.assertIn("缺少必填参数", result['error'])

    @unittest.skip("需要浏览器环境，跳过过滤测试")
    def test_search_filters_excluded_patterns(self):
        """测试搜索 - 过滤排除模式"""
        # 此测试需要实际的浏览器环境，跳过
        pass

    @unittest.skip("需要浏览器环境，跳过结果格式测试")
    def test_search_result_format(self):
        """测试搜索结果格式"""
        # 此测试需要实际的浏览器环境，跳过
        pass


class TestWebSearchIntegration(unittest.TestCase):
    """WebSearch 集成测试（需要网络连接）"""

    @unittest.skip("需要网络连接，手动运行")
    def test_real_search(self):
        """测试真实搜索（需要网络）"""
        web_search = WebSearch()
        result = web_search._cmd_search("Python programming", max_results=5)

        print(f"\n搜索结果: {result}")
        self.assertTrue(result['success'])
        self.assertGreater(result['count'], 0)


if __name__ == '__main__':
    unittest.main()
