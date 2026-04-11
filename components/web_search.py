# -*- coding: utf-8 -*-
"""
网页搜索工具 - 用 Bing 搜索关键词，返回链接列表供选择抓取
"""

import logging
import re
import time
import json
from DrissionPage import ChromiumPage, ChromiumOptions

from app.core.interface.base_cell import BaseCell
from app.core.util.browser_utils import find_browser_path

logger = logging.getLogger(__name__)


class WebSearch(BaseCell):
    """
    网页搜索工具 - Bing 搜索返回链接列表

    功能说明:
      - search: 用 Bing 搜索关键词，返回链接列表供 Agent 选择
    """

    TRUSTED_DOMAINS = {
        # 权威科技媒体
        'techcrunch.com': 10, 'theverge.com': 10, 'wired.com': 10,
        'arstechnica.com': 9, 'engadget.com': 8, 'theregister.com': 8,
        'zdnet.com': 7, 'cnet.com': 6, 'pcmag.com': 6,
        # 研究机构
        'arxiv.org': 10, 'nature.com': 10, 'science.org': 10,
        'mit.edu': 9, 'stanford.edu': 9, 'berkeley.edu': 8,
        # 顶级科技公司研究
        'openai.com': 10, 'anthropic.com': 10, 'deepmind.com': 10,
        'google.com/research': 9, 'microsoft.com/research': 9,
        'meta.com/research': 8, 'apple.com/research': 8,
        # AI/ML 专业
        'huggingface.co': 9, 'paperswithcode.com': 9,
        ' TowardsDataScience.com': 7, 'medium.com/towards-data-science': 7,
        # 新闻
        'reuters.com': 8, 'bloomberg.com': 8, 'wsj.com': 8,
        'nytimes.com': 7, 'economist.com': 8,
        # 中国科技
        '36kr.com': 7, 'jiqizhixin.com': 7, 'leiphone.com': 6,
    }

    def __init__(self):
        super().__init__()
        self._page = None
        self._options = None

    @property
    def cell_name(self) -> str:
        return "web_search"

    def _get_options(self):
        if self._options is None:
            from DrissionPage import ChromiumOptions
            self._options = ChromiumOptions()
            browser_path = find_browser_path()
            if browser_path:
                self._options.set_browser_path(browser_path)
            self._options.set_argument('--headless=new')
            self._options.set_argument('--disable-gpu')
            self._options.set_argument('--no-sandbox')
            self._options.set_argument('--disable-blink-features=AutomationControlled')
            self._options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        return self._options

    def _get_page(self):
        if self._page is None:
            from DrissionPage import ChromiumPage
            self._page = ChromiumPage(self._get_options(), timeout=15)
        return self._page

    def _close_page(self):
        if self._page:
            try:
                self._page.quit()
            except Exception as e:
                logger.debug("[WebSearch] 关闭浏览器失败: %s", e)
            finally:
                self._page = None

    def _new_page(self):
        """创建新页面（每次搜索使用新页面避免连接断开）"""
        try:
            self._close_page()
        except Exception:
            pass
        from DrissionPage import ChromiumPage
        self._page = ChromiumPage(self._get_options(), timeout=15)
        return self._page

    def _extract_links_from_bing(self, page):
        links = []
        try:
            result_items = page.eles('css:#b_results > li.b_algo', timeout=3)
            for item in result_items:
                try:
                    h2 = item.ele('css:h2', timeout=0.5)
                    if h2:
                        link_elem = h2.ele('css:a', timeout=0.5)
                    else:
                        link_elem = item.ele('css:a', timeout=0.5)
                    if link_elem:
                        href = link_elem.attr('href')
                        text = link_elem.text.strip()
                        if href and text and len(text) > 5:
                            links.append({'url': href, 'anchor': text[:80]})
                except Exception:
                    continue  # 单个结果解析失败，继续下一个
        except Exception as e:
            logger.debug("[WebSearch] 提取搜索结果失败: %s", e)

        if not links:
            html = page.html
            pattern = re.compile(r'<li[^>]*>.*?h="ID=SERP[^>]+>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL)
            for url, text in pattern.findall(html)[:20]:
                clean_text = re.sub(r'<[^>]+>', '', text).strip()
                if len(clean_text) > 5:
                    links.append({'url': url, 'anchor': clean_text[:80]})

        return links

    def _score_domain(self, url: str) -> int:
        """根据域名返回信任评分"""
        url_lower = url.lower()
        for domain, score in self.TRUSTED_DOMAINS.items():
            if domain in url_lower:
                return score
        # 默认分数
        if '.edu' in url_lower or '.gov' in url_lower:
            return 6
        if '.org' in url_lower:
            return 4
        return 2

    def _extract_date_from_result(self, item) -> str:
        """从搜索结果项提取日期"""
        try:
            date_elem = item.ele('css:.news_dtdate', timeout=0.5)
            if date_elem:
                return date_elem.text.strip()
        except Exception:
            pass
        try:
            span = item.ele('css:span', timeout=0.5)
            if span:
                text = span.text.strip()
                if any(m in text for m in ['2024', '2025', '2026']):
                    return text
        except Exception:
            pass
        return ""

    def _extract_real_url_from_bing_short_link(self, short_url, page):
        try:
            page.get(short_url, timeout=6, retry=1)
            real_url = page.url
            page.back()
            return real_url
        except Exception as e:
            logger.debug("[WebSearch] 解析短链接失败: %s", e)
            try:
                page.back()
            except Exception:
                pass
            return short_url

    def _cmd_search(self, keywords: str, max_results: int = 10, wait_time: int = 1) -> dict:
        """
        用 Bing 搜索关键词，返回链接列表

        Args:
            keywords: 搜索关键词（必填）
            max_results: 最大结果数（默认 10）
            wait_time: 等待秒数（默认 1）

        Returns:
            {"success": bool, "results": [...], "count": int, "keywords": str}
        """
        try:
            if not keywords or not keywords.strip():
                return {"success": False, "error": "缺少必填参数 'keywords'"}

            keywords = keywords.strip()

            # 过滤干扰词
            clean_keywords = keywords.replace('年预测', '').replace('年展望', '').replace('2026年', '').strip()
            if not clean_keywords:
                clean_keywords = keywords  # 如果清理后为空，保留原关键词

            page = self._new_page()
            # 使用 site:tech 加关键词搜索，获取更精准的技术内容
            search_query = f"{clean_keywords} AI technology trends"
            # 时间筛选：最近一年 (tbs=qdr:y)
            url = f"https://www.bing.com/search?q={search_query.replace(' ', '+')}&tbs=qdr:y"

            page.get(url, timeout=20)
            page.wait.doc_loaded()
            page.wait(wait_time)

            links = self._extract_links_from_bing(page)

            # 过滤关键词（标题或URL包含这些词的跳过）
            exclude_patterns = ['calendar', '日历', '促销', '打折', 'sale', 'discount',
                               'holiday', '节日', 'gift', '礼物', 'amazon.com/dp',
                               'zhihu.com/search', 'baike.baidu.com/search']

            # 先收集所有结果并评分
            scored_results = []
            seen = set()
            for link in links[:max_results * 3]:  # 多取一些，过滤后够用
                short_url = link['url']
                anchor = link['anchor']

                # 跳过明显无关的结果
                if any(p in anchor.lower() or p in short_url.lower() for p in exclude_patterns):
                    continue

                if '/ck/a?' in short_url and 'bing.com' in short_url:
                    real_url = self._extract_real_url_from_bing_short_link(short_url, page)
                else:
                    real_url = short_url

                if real_url not in seen and not any(x in real_url for x in ['login', 'signin', 'auth', 'account']):
                    seen.add(real_url)
                    score = self._score_domain(real_url)
                    scored_results.append({
                        'title': anchor,
                        'url': real_url,
                        'score': score
                    })

            # 按域名评分排序（高分的在前）
            scored_results.sort(key=lambda x: x['score'], reverse=True)

            # 取前 max_results 个
            results = []
            for i, r in enumerate(scored_results[:max_results]):
                results.append({
                    'index': i + 1,
                    'title': r['title'],
                    'url': r['url'],
                    'source': 'trusted' if r['score'] >= 7 else 'web'
                })

            return {
                "success": True,
                "results": results,
                "count": len(results),
                "keywords": keywords,
                "next_step": "使用 web_fetch.fetch(url=\"选择的链接\") 获取详细内容",
                "hint": "从 results 中选择感兴趣的链接，用 web_fetch 组件抓取页面正文"
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_close(self) -> dict:
        """关闭浏览器，释放资源"""
        self._close_page()
        return {"success": True, "message": "浏览器已关闭"}

    def _cmd_help(self, topic: str = "") -> dict:
        """查询组件使用帮助"""
        commands = self.get_commands()
        return {
            "name": self.cell_name,
            "description": "网页搜索工具 - Bing 搜索返回链接列表",
            "workflow": [
                "1. web_search.search({\"keywords\": \"搜索词\"}) - 获取链接列表",
                "2. 从 results 中选择感兴趣的链接",
                "3. web_fetch.fetch({\"url\": \"选择的链接\"}) - 获取页面详细内容"
            ],
            "available_commands": list(commands.keys()),
            "usage": {
                "search": {
                    "args": {"keywords": "搜索关键词", "max_results": "最大结果数", "wait_time": "等待秒数"},
                    "example": '{"keywords": "Python教程", "max_results": 5}'
                }
            },
            "next_tool": "web_fetch"
        }

    def on_load(self):
        super().on_load()