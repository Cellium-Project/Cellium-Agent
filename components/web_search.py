# -*- coding: utf-8 -*-
"""
网页搜索工具 - 用 Bing 搜索关键词，返回链接列表供选择抓取
"""

import logging
import re
import time
import json
import asyncio
import base64
import urllib.parse
from DrissionPage import ChromiumPage, ChromiumOptions

from app.core.interface.base_cell import BaseCell
from app.core.util.browser_utils import find_browser_path

logger = logging.getLogger(__name__)


class WebSearch(BaseCell):
    """
    网页搜索工具 - 支持多种搜索引擎

    功能说明:
      - search: 用指定搜索引擎搜索关键词，返回链接列表供 Agent 选择
      - 支持搜索引擎: bing, baidu, google, duckduckgo
    """

    SEARCH_ENGINES = {
        'bing': {
            'name': 'Bing',
            'url_template': 'https://www.bing.com/search?q={query}&filters=ex1%3a%22ez2%22',
            'url_template_no_filter': 'https://www.bing.com/search?q={query}',
            'result_selector': '#b_results > li.b_algo',
            'title_selector': 'css:h2',
            'link_selector': 'css:a',
            'desc_selector': '.b_caption p',
            'shortlink_pattern': 'bing.com/ck/a?',
        },
        'baidu': {
            'name': '百度',
            'url_template': 'https://www.baidu.com/s?wd={query}&rn=20',
            'result_selector': '#content_left .c-container',
            'title_selector': 'css:h3',
            'link_selector': 'css:a',
            'desc_selector': '.c-abstract, .c-span-last',
            'shortlink_pattern': 'baidu.com/link?url=',
        },
        'google': {
            'name': 'Google',
            'url_template': 'https://www.google.com/search?q={query}&num=20',
            'result_selector': 'div.g',
            'title_selector': 'css:h3',
            'link_selector': 'css:a',
            'desc_selector': '.VwiC3b, .IsZvec',
            'shortlink_pattern': 'google.com/url?',
        },
        'duckduckgo': {
            'name': 'DuckDuckGo',
            'url_template': 'https://html.duckduckgo.com/html/?q={query}',
            'result_selector': '.result',
            'title_selector': '.result__title',
            'link_selector': 'css:a',
            'desc_selector': '.result__snippet',
            'shortlink_pattern': 'duckduckgo.com/y.js?',
            'requires_proxy': True,
            'cn_accessible': False,
        },
        'searxng': {
            'name': 'SearXNG',
            'url_template': 'https://searxng.site/search?q={query}',
            'result_selector': '.result',
            'title_selector': '.result_title',
            'link_selector': 'css:a',
            'desc_selector': '.result_content',
            'shortlink_pattern': '',
            'requires_proxy': False,
            'cn_accessible': True,
        },
    }

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
        'towardsdatascience.com': 7, 'medium.com/towards-data-science': 7,
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
        self._last_search_time = 0
        self._min_search_interval = 5
        self._use_proxy = False
        self._proxy_server = None
        self._engine_health = {
            'bing': 'unknown',
            'baidu': 'unknown',
            'google': 'unknown',
            'duckduckgo': 'unknown',
            'searxng': 'unknown',
        }
        self._auto_detect_proxy()

    def _auto_detect_proxy(self):
        """自动检测本地代理服务器"""
        import socket

        common_proxies = [
            ('http://127.0.0.1:7890', 'Clash'),
            ('http://127.0.0.1:7891', 'Clash (Mixed)'),
            ('http://127.0.0.1:1080', 'V2Ray/Vmess'),
            ('http://127.0.0.1:10808', 'V2RayN'),
            ('http://127.0.0.1:8080', 'HTTP Proxy'),
            ('http://127.0.0.1:8888', 'System Proxy'),
            ('http://127.0.0.1:8118', 'Privoxy'),
            ('socks5://127.0.0.1:1080', 'Socks5'),
            ('http://127.0.0.1:1081', 'Shadowsocks'),
        ]

        for proxy_url, name in common_proxies:
            try:
                host = proxy_url.split('://')[1].split(':')[0]
                port = int(proxy_url.split(':')[-1])
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                sock.close()
                if result == 0:
                    self._proxy_server = proxy_url
                    self._use_proxy = True
                    logger.info(f"[WebSearch] 自动检测到代理: {proxy_url} ({name})")
                    return
            except Exception:
                continue

        logger.info("[WebSearch] 未检测到本地代理，将使用直连")

    @property
    def cell_name(self) -> str:
        return "web_search"

    def set_proxy(self, proxy_server: str):
        """设置代理服务器"""
        self._proxy_server = proxy_server
        self._use_proxy = bool(proxy_server)
        if self._options:
            self._options = None
        logger.info(f"[WebSearch] 代理设置: {proxy_server if self._use_proxy else '无'}")

    def get_engine_health(self) -> dict:
        """获取搜索引擎健康状态"""
        return self._engine_health.copy()

    def _check_engine_accessible(self, engine: str) -> bool:
        """检测引擎是否可访问"""
        if engine not in self.SEARCH_ENGINES:
            return False
        config = self.SEARCH_ENGINES[engine]
        if config.get('requires_proxy', False) and not self._use_proxy:
            return False
        return config.get('cn_accessible', True)

    def _select_best_engine(self) -> str:
        """自动选择最佳的可用搜索引擎"""
        priority_order = ['baidu', 'bing', 'searxng', 'google', 'duckduckgo']

        for engine in priority_order:
            if engine not in self.SEARCH_ENGINES:
                continue
            if not self._check_engine_accessible(engine):
                continue
            health = self._engine_health.get(engine, 'unknown')
            if health == 'green':
                logger.info(f"[WebSearch:auto] 选择 {engine} (状态: green)")
                return engine

        for engine in priority_order:
            if engine not in self.SEARCH_ENGINES:
                continue
            if not self._check_engine_accessible(engine):
                continue
            health = self._engine_health.get(engine, 'unknown')
            if health == 'unknown':
                logger.info(f"[WebSearch:auto] 选择 {engine} (状态: unknown, 首次尝试)")
                return engine

        logger.info(f"[WebSearch:auto] 默认选择 baidu")
        return 'baidu'

    def _get_options(self):
        if self._options is None:
            from DrissionPage import ChromiumOptions
            self._options = ChromiumOptions()
            browser_path = find_browser_path()
            if browser_path:
                self._options.set_browser_path(browser_path)
            if self._use_proxy and self._proxy_server:
                self._options.set_argument(f'--proxy-server={self._proxy_server}')
            self._options.set_argument('--disable-gpu')
            self._options.set_argument('--no-sandbox')
            self._options.set_argument('--headless=new')
            self._options.set_argument('--disable-blink-features=AutomationControlled')
            self._options.set_argument('--disable-dev-shm-usage')
            self._options.set_argument('--disable-extensions')
            self._options.set_argument('--profile-directory=Default')
            self._options.set_argument('--disable-plugins-discovery')
            self._options.set_argument('--disable-infobars')
            self._options.set_argument('--start-maximized')
            self._options.set_argument('--disable-popup-blocking')
            self._options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            self._options.set_argument('--disable-web-security')
            self._options.set_argument('--disable-bundled-ppapi-flash')
            self._options.set_argument('--allow-running-insecure-content')
            self._options.set_argument('--disable-client-side-phishing-detection')
            self._options.set_argument('--mute-audio')
            self._options.set_argument('--hide-scrollbars')
            self._options.set_argument('--disable-background-networking')
            self._options.headless(True)
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

    def _get_or_create_page(self):
        """获取或创建页面（复用浏览器 session）"""
        if self._page is None or not self._is_page_alive():
            from DrissionPage import ChromiumPage
            self._page = ChromiumPage(self._get_options(), timeout=15)
        return self._page

    def _is_page_alive(self) -> bool:
        """检查页面是否仍然可用"""
        try:
            if self._page and self._page.url:
                return True
        except Exception:
            pass
        return False

    def _extract_links_generic(self, page, engine: str):
        """通用链接提取方法，适用于所有搜索引擎"""
        links = []
        config = self.SEARCH_ENGINES.get(engine, {})

        try:
            result_items = page.eles(config.get('result_selector', ''), timeout=3)
            logger.info(f"[WebSearch:{engine}] DOM 解析找到 {len(result_items)} 个搜索结果项")
            for item in result_items:
                try:
                    if engine == 'bing':
                        h2 = item.ele('css:h2', timeout=0.5)
                        link_elem = h2.ele('css:a', timeout=0.5) if h2 else item.ele('css:a', timeout=0.5)
                    elif engine == 'baidu':
                        h3 = item.ele('css:h3', timeout=0.5)
                        link_elem = h3.ele('css:a', timeout=0.5) if h3 else item.ele('css:a', timeout=0.5)
                    elif engine == 'google':
                        link_elem = item.ele('css:a', timeout=0.5)
                    elif engine == 'duckduckgo':
                        title_elem = item.ele('.result__title', timeout=0.5)
                        link_elem = title_elem.ele('css:a', timeout=0.5) if title_elem else item.ele('css:a', timeout=0.5)
                    else:
                        link_elem = item.ele('css:a', timeout=0.5)

                    if link_elem:
                        href = link_elem.attr('href')
                        title = link_elem.text.strip() if link_elem.text else ""

                        if href and title and len(title) > 5:
                            if engine == 'bing':
                                desc_elem = item.ele('.b_caption p', timeout=0.5)
                            elif engine == 'baidu':
                                desc_elem = item.ele('.c-abstract', timeout=0.5) or item.ele('.c-span-last', timeout=0.5)
                            elif engine == 'google':
                                desc_elem = item.ele('.VwiC3b', timeout=0.5) or item.ele('.IsZvec', timeout=0.5)
                            elif engine == 'duckduckgo':
                                desc_elem = item.ele('.result__snippet', timeout=0.5)
                            else:
                                desc_elem = None

                            snippet = desc_elem.text.strip() if desc_elem and desc_elem.text else ""

                            links.append({
                                'url': href,
                                'title': title[:100],
                                'snippet': snippet[:200]
                            })
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[WebSearch:{engine}] DOM 解析失败: {e}")

        if len(links) == 0:
            logger.info(f"[WebSearch:{engine}] DOM 解析无结果，尝试正则 fallback")
            links = self._extract_links_regex(page.html, engine)

        return links

    def _extract_links_from_bing(self, page):
        links = []
        try:
            result_items = page.eles('css:#b_results > li.b_algo', timeout=3)
            logger.info("[WebSearch] DOM 解析找到 %d 个搜索结果项", len(result_items))
            for item in result_items:
                try:
                    h2 = item.ele('css:h2', timeout=0.5)
                    if h2:
                        link_elem = h2.ele('css:a', timeout=0.5)
                    else:
                        link_elem = item.ele('css:a', timeout=0.5)
                    if link_elem:
                        href = link_elem.attr('href')
                        title = link_elem.text.strip()
                        if href and title and len(title) > 5:
                            desc_elem = item.ele('css:.b_caption p', timeout=0.5)
                            snippet = desc_elem.text.strip() if desc_elem else ""
                            links.append({
                                'url': href,
                                'title': title[:100],
                                'snippet': snippet[:200]
                            })
                except Exception:
                    continue
        except Exception as e:
            logger.debug("[WebSearch] DOM 解析失败: %s", e)

        if len(links) == 0:
            logger.info("[WebSearch] DOM 解析无结果，尝试正则 fallback")
            links = self._extract_links_regex(page.html)

        return links

    def _extract_links_regex(self, html: str, engine: str = 'bing'):
        """正则 fallback 解析器"""
        links = []
        try:
            patterns = []
            if engine == 'bing':
                patterns = [
                    re.compile(r'<li[^>]*>.*?h="ID=SERP[^>]+>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                    re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            elif engine == 'baidu':
                patterns = [
                    re.compile(r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                    re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            elif engine == 'google':
                patterns = [
                    re.compile(r'<div[^>]*class="g"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            elif engine == 'duckduckgo':
                patterns = [
                    re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            else:
                patterns = [re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', re.DOTALL)]

            for pattern in patterns:
                matches = pattern.findall(html)
                if matches:
                    for url, text in matches[:20]:
                        clean_text = re.sub(r'<[^>]+>', '', text).strip()
                        if len(clean_text) > 5 and url.startswith('http'):
                            links.append({
                                'url': url,
                                'title': clean_text[:100],
                                'snippet': ''
                            })
                    if links:
                        break
        except Exception as e:
            logger.debug(f"[WebSearch:{engine}] 正则解析失败: {e}")
        return links

    def resolve_url(self, url: str, page=None) -> str:
        """统一 URL 解析函数"""
        if 'bing.com' in url and '/ck/a?' in url:
            decoded = self._decode_bing_url(url)
            if decoded:
                return decoded
            if page:
                try:
                    return self._extract_real_url_from_bing_short_link(url, page)
                except Exception:
                    pass
        return url

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

    def _decode_bing_url(self, short_url: str) -> str:
        """尝试从 Bing 短链中解析真实 URL（不跳转）"""
        try:
            parsed = urllib.parse.urlparse(short_url)
            query = urllib.parse.parse_qs(parsed.query)

            if 'u' in query:
                encoded = query['u'][0]
                encoded = urllib.parse.unquote(encoded)
                missing_padding = len(encoded) % 4
                if missing_padding:
                    encoded += '=' * (4 - missing_padding)
                decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')
                if decoded.startswith("http"):
                    return decoded
        except Exception:
            pass
        return ""

    def _score_result(self, title: str, snippet: str, url: str, query: str) -> float:
        """综合评分：关键词匹配 + 域名权重"""
        score = 0.0
        text = (title + " " + snippet).lower()
        q_words = query.lower().split()
        if q_words:
            match_count = sum(1 for w in q_words if w in text)
            score += match_count * 2
        score += self._score_domain(url)
        return score

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

    def _cmd_search(self, keywords: str, max_results: int = 10, wait_time: int = 1, engine: str = 'auto') -> dict:
        """
        用指定搜索引擎搜索关键词，返回链接列表

        Args:
            keywords: 搜索关键词（必填）
            max_results: 最大结果数（默认 10）
            wait_time: 等待秒数（默认 1）
            engine: 搜索引擎选择，默认 auto（自动选择），可选: auto, bing, baidu, google, duckduckgo, searxng

        Returns:
            {"success": bool, "results": [...], "count": int, "keywords": str}
        """
        try:
            import time
            current_time = time.time()
            elapsed = current_time - self._last_search_time
            if elapsed < self._min_search_interval:
                wait_needed = self._min_search_interval - elapsed
                logger.info("[WebSearch] 搜索频率限制，需等待 %.1f 秒", wait_needed)
                time.sleep(wait_needed)
            self._last_search_time = time.time()

            if not keywords or not keywords.strip():
                return {"success": False, "error": "缺少必填参数 'keywords'"}

            keywords = keywords.strip()

            if engine == 'auto':
                engine = self._select_best_engine()
                logger.info(f"[WebSearch:auto] 自动选择搜索引擎: {engine}")

            if engine not in self.SEARCH_ENGINES:
                return {
                    "success": False,
                    "error": f"不支持的搜索引擎: {engine}",
                    "available_engines": list(self.SEARCH_ENGINES.keys())
                }

            engine_config = self.SEARCH_ENGINES[engine]

            clean_keywords = keywords.replace('年预测', '').replace('年展望', '').strip()
            if not clean_keywords:
                clean_keywords = keywords

            page = self._get_or_create_page()
            search_query = clean_keywords
            encoded_query = urllib.parse.quote(search_query)

            if engine == 'bing':
                if re.search(r'\b(20\d{2})\b', search_query) or re.search(r'\b(20\d{2})年\b', search_query):
                    url = f"https://www.bing.com/search?q={encoded_query}"
                else:
                    url = engine_config['url_template'].format(query=encoded_query)
            elif engine == 'baidu':
                url = engine_config['url_template'].format(query=encoded_query)
            elif engine == 'google':
                url = engine_config['url_template'].format(query=encoded_query)
            elif engine == 'duckduckgo':
                url = engine_config['url_template'].format(query=encoded_query)
            else:
                url = f"https://www.bing.com/search?q={encoded_query}"

            logger.info(f"[WebSearch:{engine}] 搜索 URL: {url}")

            page.get(url, timeout=20)
            page.wait.doc_loaded()
            page.wait(wait_time)
            import random
            random_delay = random.uniform(2, 6)
            logger.info(f"[WebSearch:{engine}] 随机延迟 {random_delay:.1f} 秒防风控")
            page.wait(random_delay)
            logger.info(f"[WebSearch:{engine}] 页面加载完成，当前 URL: {page.url}")

            links = self._extract_links_generic(page, engine)
            logger.info(f"[WebSearch:{engine}] 提取到 {len(links)} 个链接")

            exclude_patterns = ['calendar', '日历', '促销', '打折', 'sale', 'discount',
                               'holiday', '节日', 'gift', '礼物', 'amazon.com/dp',
                               'zhihu.com/search', 'baike.baidu.com/search', 'zhihu.com']

            short_links = []
            normal_links = []
            seen = set()

            for link in links[:max_results * 3]:
                url = link['url']
                title = link.get('title', '')
                snippet = link.get('snippet', '')

                if any(p in title.lower() or p in url.lower() for p in exclude_patterns):
                    continue

                shortlink_pattern = engine_config.get('shortlink_pattern', '')
                if shortlink_pattern and shortlink_pattern in url:
                    short_links.append(link)
                else:
                    normal_links.append(link)

            scored_results = []

            for link in normal_links:
                url = link['url']
                title = link.get('title', '')
                snippet = link.get('snippet', '')

                if url not in seen and not any(x in url for x in ['login', 'signin', 'auth', 'account']):
                    seen.add(url)
                    score = self._score_result(title, snippet, url, keywords)
                    scored_results.append({
                        'title': title,
                        'url': url,
                        'snippet': snippet,
                        'score': score
                    })

            if len(scored_results) < 3 and short_links:
                logger.info(f"[WebSearch:{engine}] 启用 fallback 短链解析")
                for link in short_links[:2]:
                    short_url = link['url']
                    decoded = None

                    if engine == 'bing':
                        decoded = self._decode_bing_url(short_url)

                    if decoded:
                        real_url = decoded
                    else:
                        continue

                    if real_url in seen:
                        continue

                    seen.add(real_url)
                    score = self._score_result(
                        link.get('title', ''),
                        link.get('snippet', ''),
                        real_url,
                        keywords
                    )
                    scored_results.append({
                        'title': link.get('title', ''),
                        'url': real_url,
                        'snippet': link.get('snippet', ''),
                        'score': score
                    })

                    if len(scored_results) >= 5:
                        break

            logger.info(f"[WebSearch:{engine}] 过滤后剩余 {len(scored_results)} 个结果")

            scored_results.sort(key=lambda x: x['score'], reverse=True)

            max_results = min(max_results, 5)

            results = []
            for i, r in enumerate(scored_results[:max_results]):
                reason = []
                if r['score'] >= 8:
                    reason.append("权威来源")
                if any(w in r['title'].lower() for w in keywords.lower().split()):
                    reason.append("标题匹配")

                results.append({
                    'id': i + 1,
                    'title': r['title'],
                    'url': r['url'],
                    'snippet': r.get('snippet', ''),
                    'score': round(r['score'] / 10, 2),
                    'source': 'trusted' if r['score'] >= 7 else 'web',
                    'reason': ' + '.join(reason) or '一般匹配'
                })

            if not results:
                self._engine_health[engine] = 'red'
                return {
                    "success": False,
                    "error": f"搜索引擎 {engine} 搜索结果为空，可能被反爬",
                    "hint": "建议尝试其他搜索引擎或稍后重试",
                    "available_engines": [e for e in self.SEARCH_ENGINES.keys() if e != engine],
                    "engine_health": self._engine_health.copy()
                }

            self._engine_health[engine] = 'green'
            return {
                "success": True,
                "results": results,
                "count": len(results),
                "keywords": keywords,
                "engine": engine,
                "engine_name": engine_config['name'],
                "engine_health": self._engine_health.copy(),
                "instruction": (
                    "从 results 中选择 1-2 个最相关的结果，"
                    "然后调用 web_fetch.fetch(url=...) 获取详细内容。"
                ),
                "constraints": [
                    "不要重复搜索相同关键词",
                    "优先选择 score 高的结果",
                    "最多选择 2 个链接"
                ]
            }

        except Exception as e:
            self._engine_health[engine] = 'red'
            return {"success": False, "error": str(e), "engine_health": self._engine_health.copy()}

    def _cmd_close(self) -> dict:
        """关闭浏览器，释放资源"""
        self._close_page()
        return {"success": True, "message": "浏览器已关闭"}

    def _cmd_help(self, topic: str = "") -> dict:
        """查询组件使用帮助"""
        commands = self.get_commands()
        return {
            "name": self.cell_name,
            "description": "网页搜索工具 - 支持 Bing、百度、Google、DuckDuckGo、SearXNG 多种搜索引擎（默认自动选择）",
            "workflow": [
                "1. web_search.search({\"keywords\": \"搜索词\"}) - 自动选择最佳引擎获取链接列表",
                "2. web_search.search({\"keywords\": \"搜索词\", \"engine\": \"baidu\"}) - 指定搜索引擎",
                "3. 从 results 中选择感兴趣的链接",
                "4. web_fetch.fetch({\"url\": \"选择的链接\"}) - 获取页面详细内容"
            ],
            "available_commands": list(commands.keys()),
            "default_behavior": "auto - 自动选择最佳可用引擎（国内默认 baidu）",
            "available_engines": {
                "auto": "自动选择（默认）",
                "bing": "Bing 搜索",
                "baidu": "百度搜索（国内首选）",
                "google": "Google 搜索",
                "duckduckgo": "DuckDuckGo（需要代理）",
                "searxng": "SearXNG（开源聚合搜索）"
            },
            "engine_health": self._engine_health.copy(),
            "proxy_info": {
                "auto_detected": self._use_proxy,
                "proxy_server": self._proxy_server,
                "auto_detect": "启动时自动检测常见代理端口（Clash/V2Ray/Shadowsocks）",
                "manual_set": 'web_search.set_proxy("http://127.0.0.1:7890")'
            },
            "usage": {
                "search": {
                    "args": {"keywords": "搜索关键词", "max_results": "最大结果数", "wait_time": "等待秒数", "engine": "auto/搜索引擎"},
                    "example_auto": '{"keywords": "Python教程"}',
                    "example_baidu": '{"keywords": "Python教程", "engine": "baidu"}',
                    "example_bing": '{"keywords": "Python教程", "engine": "bing"}'
                }
            },
            "next_tool": "web_fetch"
        }

    def on_load(self):
        super().on_load()