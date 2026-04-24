# -*- coding: utf-8 -*-
"""
网页搜索工具
"""

import logging
import re
import time
import json
import asyncio
import os
import base64
import tempfile
import urllib.parse
import hashlib
import threading
from functools import lru_cache
from typing import Optional, Any
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
            'url_template_recent': {
                'hour': 'https://www.bing.com/search?q={query}&filters=ex1%3a%22ez5%22',  # 1小时内
                'day': 'https://www.bing.com/search?q={query}&filters=ex1%3a%22ez4%22',   # 24小时内
                'week': 'https://www.bing.com/search?q={query}&filters=ex1%3a%22ez3%22',  # 7天内
                'month': 'https://www.bing.com/search?q={query}&filters=ex1%3a%22ez6%22', # 1个月内
            },
            'result_selector': '#b_results > li.b_algo',
            'title_selector': 'css:h2',
            'link_selector': 'css:a',
            'desc_selector': '.b_caption p',
            'shortlink_pattern': 'bing.com/ck/a?',
        },
        'baidu': {
            'name': '百度',
            'url_template': 'https://www.baidu.com/s?wd={query}&rn=20',
            'url_template_recent': {
                'hour': 'https://www.baidu.com/s?wd={query}&rn=20&lm=1',     # 1小时内
                'day': 'https://www.baidu.com/s?wd={query}&rn=20&lm=7',     # 7天内
                'week': 'https://www.baidu.com/s?wd={query}&rn=20&lm=30',    # 30天内
                'month': 'https://www.baidu.com/s?wd={query}&rn=20&lm=183',  # 6个月内
            },
            'result_selector': '#content_left .c-container',
            'title_selector': 'css:h3',
            'link_selector': 'css:a',
            'desc_selector': '.c-abstract, .c-span-last',
            'shortlink_pattern': 'baidu.com/link?url=',
        },
        'google': {
            'name': 'Google',
            'url_template': 'https://www.google.com/search?q={query}&num=20',
            'result_selector': '#rso .MjjYud',
            'title_selector': 'css:h3',
            'link_selector': 'css:a',
            'desc_selector': '.VwiC3b, .IsZvec, .s3v94d',
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
        self._browser = None
        self._options = None
        self._last_search_time = 0
        self._min_search_interval = 5
        self._engine_health = {
            'bing': 'unknown',
            'baidu': 'unknown',
            'google': 'unknown',
            'duckduckgo': 'unknown',
        }
        self._search_cache = {}
        self._user_agent = None
        self._lock = threading.RLock()
        self._browser_port = None
        self._browser_path = None
        self._instance_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
        self._last_used_time: float = 0
        self._idle_timer: Optional[threading.Thread] = None
        self._idle_stop_event = threading.Event()
        self._creation_count = 0

    IDLE_TIMEOUT = 300
    HEALTH_CHECK_TIMEOUT = 5

    @property
    def cell_name(self) -> str:
        return "web_search"

    def execute(self, command: str, *args, **kwargs) -> Any:
        command = command.strip().strip('>"\'')
        method_name = f"_cmd_{command}"
        if hasattr(self, method_name):
            with self._lock:
                return getattr(self, method_name)(*args, **kwargs)
        from app.core.exception import CommandNotFoundError
        raise CommandNotFoundError(command, self.cell_name)

    def get_engine_health(self) -> dict:
        """获取搜索引擎健康状态"""
        return self._engine_health.copy()

    def _get_cache_key(self, keywords: str, engine: str) -> str:
        """生成缓存键"""
        key_str = f"{engine}:{keywords.lower().strip()}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached_result(self, keywords: str, engine: str) -> dict:
        """获取缓存的搜索结果"""
        cache_key = self._get_cache_key(keywords, engine)
        if cache_key in self._search_cache:
            cached = self._search_cache[cache_key]
            age = time.time() - cached.get('_cached_at', 0)
            if age < 300:
                logger.info(f"[WebSearch:cache] 命中缓存 (key={cache_key[:8]}, age={age:.1f}s)")
                return cached.get('data')
        return None

    def _set_cached_result(self, keywords: str, engine: str, data: dict):
        """设置搜索结果缓存"""
        cache_key = self._get_cache_key(keywords, engine)
        data['_cached_at'] = time.time()
        self._search_cache[cache_key] = data
        if len(self._search_cache) > 100:
            oldest = min(self._search_cache.items(), key=lambda x: x[1].get('_cached_at', 0))
            del self._search_cache[oldest[0]]

    async def _search_async(self, keywords: str, max_results: int = 10, wait_time: int = 1, engine: str = 'auto') -> dict:
        """异步搜索入口"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._cmd_search(keywords, max_results, wait_time, engine))

    def _quick_resolve(self, url: str) -> str:
        """快速解析跳转链接"""
        if not url or not isinstance(url, str):
            return url
        if 'baidu.com/link?url=' in url or 'google.com/url?' in url:
            try:
                import requests
                r = requests.head(url, allow_redirects=True, timeout=2, headers={'User-Agent': 'Mozilla/5.0'})
                if r.url and r.url != url:
                    logger.info(f"[WebSearch:resolve] {url[:50]} -> {r.url[:50]}")
                    return r.url
            except Exception as e:
                logger.debug(f"[WebSearch:resolve] 快速解析失败: {e}")
        return url

    def _check_engine_accessible(self, engine: str) -> bool:
        """检测引擎是否可访问"""
        return engine in self.SEARCH_ENGINES

    def _select_best_engine(self) -> str:
        """自动选择最佳的可用搜索引擎"""
        priority_order = ['bing', 'google', 'duckduckgo', 'baidu']

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
            if health in ('unknown', 'red'):
                logger.info(f"[WebSearch:auto] 选择 {engine} (状态: {health}, 尝试搜索)")
                return engine

        logger.info(f"[WebSearch:auto] 默认选择 baidu")
        return 'baidu'

    def _get_options(self):
        """获取浏览器配置选项"""
        from DrissionPage import ChromiumOptions
        import random

        options = ChromiumOptions()
        if self._browser_path is None:
            self._browser_path = find_browser_path()
        browser_path = self._browser_path

        if browser_path:
            options.set_browser_path(browser_path)
            options.auto_port()
            if 'msedge' in browser_path.lower() or 'edge' in browser_path.lower():
                options.set_argument('--no-first-run')
                options.set_argument('--no-default-browser-check')
                options.set_argument('--no-singleton')
        if self._user_agent is None:
            try:
                from fake_useragent import UserAgent
                ua = UserAgent()
                self._user_agent = ua.random
            except Exception:
                self._user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        options.set_argument('--disable-gpu')
        options.set_argument('--no-sandbox')
        options.set_argument('--disable-blink-features=AutomationControlled')
        options.set_argument('--disable-dev-shm-usage')
        options.set_argument('--disable-extensions')
        import tempfile
        _search_data_dir = os.path.join(tempfile.gettempdir(), 'Cellium_WebSearch_Profile')
        os.makedirs(_search_data_dir, exist_ok=True)
        options.set_argument(f'--user-data-dir={_search_data_dir}')
        options.set_argument('--profile-directory=Default')
        options.set_argument('--disable-plugins-discovery')
        options.set_argument('--disable-infobars')
        options.set_argument('--disable-popup-blocking')
        options.set_argument(f'--user-agent={self._user_agent}')
        options.set_argument('--disable-web-security')
        options.set_argument('--disable-bundled-ppapi-flash')
        options.set_argument('--allow-running-insecure-content')
        options.set_argument('--disable-client-side-phishing-detection')
        options.set_argument('--mute-audio')
        options.set_argument('--hide-scrollbars')
        options.set_argument('--disable-background-networking')
        options.set_argument('--disable-canvas-audit')
        # 设置无头模式 - 使用 headless() 方法
        options.headless(on_off=True)
        logger.info(f"[WebSearch] 浏览器配置完成，headless=True，路径: {browser_path}")
        return options

    def _close_page(self):
        with self._lock:
            self._stop_idle_timer()
            self._close_page_internal()

    def __del__(self):
        try:
            self._stop_idle_timer()
            if self._page or self._browser:
                self._close_page_internal()
        except Exception:
            pass

    def _safe_browser_operation(self, operation: callable, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except AttributeError as e:
            logger.warning("[WebSearch] 操作失败 AttributeError: %s，尝试恢复", e)
            self._close_page()
            return None
        except TypeError as e:
            logger.warning("[WebSearch] 操作失败 TypeError: %s，尝试恢复", e)
            self._close_page()
            return None
        except Exception as e:
            logger.warning("[WebSearch] 操作失败: %s", e)
            return None

    def _get_or_create_page(self, force_recreate: bool = False, max_retries: int = 3):
        with self._lock:
            if force_recreate:
                self._close_page_internal()
                self._stop_idle_timer()

            if self._page is not None:
                if self._health_check():
                    self._update_last_used_time()
                    self._start_idle_timer()
                    return self._page
                else:
                    logger.info("[WebSearch] 健康检查失败，关闭旧实例准备重建")
                    self._close_page_internal()

            self._close_page_internal()
            last_error = None

            for attempt in range(max_retries):
                try:
                    from DrissionPage import Chromium, ChromiumOptions

                    if attempt > 0:
                        logger.info(f"[WebSearch] 浏览器重试 ({attempt + 1}/{max_retries})，使用新端口...")
                        self._browser_port = None
                        time.sleep(1)

                    co = self._get_options()
                    self._browser = Chromium(addr_or_opts=co)
                    if self._browser is None:
                        raise RuntimeError("Chromium 浏览器创建失败")
                    
                    browser_ready = False
                    for warmup in range(3):
                        try:
                            time.sleep(0.3)
                            test_url = self._browser.address
                            if not test_url:
                                continue
                            _ = self._browser.latest_tab
                            if self._page is None:
                                self._page = self._browser.latest_tab
                            test_page_url = self._page.url if self._page else None
                            if self._page and hasattr(self._page, 'driver'):
                                _ = self._page.driver
                            browser_ready = True
                            break
                        except (AttributeError, TypeError) as we:
                            logger.debug(f"[WebSearch] 浏览器预热 {warmup+1}/5: {we}")
                            continue
                    
                    if not browser_ready:
                        raise RuntimeError("浏览器启动后无法建立连接")
                    
                    try:
                        if hasattr(self._browser, 'address') and self._browser.address:
                            addr_parts = self._browser.address.split(':')
                            if len(addr_parts) == 2:
                                self._browser_port = int(addr_parts[1])
                    except Exception as e:
                        logger.debug(f"[WebSearch] 获取端口失败: {e}")
                    
                    if self._page is None:
                        self._page = self._browser.latest_tab
                    if self._page is None:
                        self._page = self._browser.new_tab()
                        if self._page is None:
                            raise RuntimeError("无法获取浏览器标签页")

                    self._creation_count += 1
                    self._update_last_used_time()
                    self._start_idle_timer()
                    logger.info(f"[WebSearch] 浏览器页面创建成功 (端口: {self._browser_port}, 累计创建: {self._creation_count})")
                    return self._page
                except AttributeError as e:
                    last_error = e
                    logger.warning(f"[WebSearch] 浏览器创建失败 AttributeError (尝试 {attempt + 1}/{max_retries}): {e}")
                    self._close_page_internal()
                except TypeError as e:
                    last_error = e
                    logger.warning(f"[WebSearch] 浏览器创建失败 TypeError (尝试 {attempt + 1}/{max_retries}): {e}")
                    self._close_page_internal()
                except Exception as e:
                    last_error = e
                    logger.warning(f"[WebSearch] 浏览器创建失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    self._close_page_internal()

            logger.error(f"[WebSearch] 浏览器创建最终失败: {last_error}")
            raise last_error

    def _is_page_alive(self) -> bool:
        """检查页面是否仍然可用"""
        try:
            if self._page is None:
                return False
            _ = self._page.url
            _ = self._page.driver
            return True
        except Exception as e:
            logger.debug(f"[WebSearch] 页面连接检查失败: {e}")
            return False

    def _health_check(self) -> bool:
        if self._page is None or self._browser is None:
            logger.debug("[WebSearch] 健康检查：无浏览器实例")
            return False
        try:
            url = self._page.url
            if url is None:
                logger.debug("[WebSearch] 健康检查：url 为 None")
                return False
            _ = self._page.driver
            logger.debug("[WebSearch] 健康检查通过")
            return True
        except AttributeError as e:
            logger.warning("[WebSearch] 健康检查失败（AttributeError）: %s", e)
            return False
        except TypeError as e:
            logger.warning("[WebSearch] 健康检查失败（TypeError）: %s", e)
            return False
        except Exception as e:
            logger.warning("[WebSearch] 健康检查失败: %s", e)
            return False

    def _update_last_used_time(self):
        self._last_used_time = time.time()

    def _start_idle_timer(self):
        if self._idle_timer is not None and self._idle_timer.is_alive():
            return
        self._idle_stop_event.clear()
        def _idle_monitor():
            while not self._idle_stop_event.is_set():
                time.sleep(60)
                if self._idle_stop_event.is_set():
                    break
                with self._lock:
                    if self._page is None:
                        continue
                    idle_time = time.time() - self._last_used_time
                    if idle_time > self.IDLE_TIMEOUT:
                        logger.info("[WebSearch] 浏览器空闲超时 (%.0f秒)，自动关闭", idle_time)
                        self._close_page_internal()
        self._idle_timer = threading.Thread(target=_idle_monitor, daemon=True, name="WebSearchIdleMonitor")
        self._idle_timer.start()
        logger.debug("[WebSearch] 空闲监控线程已启动")

    def _stop_idle_timer(self):
        if self._idle_timer is not None and self._idle_timer.is_alive():
            self._idle_stop_event.set()
            self._idle_timer.join(timeout=2)
            logger.debug("[WebSearch] 空闲监控线程已停止")

    def _close_page_internal(self):
        if self._page:
            try:
                self._page.quit()
            except Exception as e:
                logger.debug("[WebSearch] 关闭页面失败: %s", e)
            finally:
                self._page = None
        if self._browser:
            try:
                self._browser.quit()
            except Exception as e:
                logger.debug("[WebSearch] 关闭浏览器失败: %s", e)
            finally:
                self._browser = None
        self._browser_port = None
        self._last_used_time = 0

    def _extract_links_generic(self, page, engine: str):
        """通用链接提取方法，适用于所有搜索引擎"""
        links = []
        config = self.SEARCH_ENGINES.get(engine, {})

        try:
            # 增加等待时间确保页面完全加载
            page.wait(2)
            selector = config.get('result_selector', '')
            # 确保选择器有 css: 前缀
            if selector and not selector.startswith('css:'):
                selector = f'css:{selector}'
            result_items = page.eles(selector, timeout=5)
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
                                desc_elem = (item.ele('.b_lineclamp2', timeout=0.3) or
                                            item.ele('.b_lineclamp3', timeout=0.3) or
                                            item.ele('p', timeout=0.3))
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

    def _score_result(self, title: str, snippet: str, url: str, query: str, date_str: str = "") -> float:
        """综合评分：关键词匹配 + 域名权重 + 时间衰减因子"""
        score = 0.0
        text = (title + " " + snippet).lower()
        q_words = query.lower().split()
        if q_words:
            match_count = sum(1 for w in q_words if w in text)
            score += match_count * 2
        score += self._score_domain(url)
        score += self._calc_time_weight(date_str)
        return score

    def _calc_time_weight(self, date_str: str) -> float:
        """根据日期字符串计算时间权重"""
        if not date_str:
            return 0.0
        try:
            import datetime
            current_year = datetime.datetime.now().year
            for year in range(current_year, current_year - 3, -1):
                year_str = str(year)
                if year_str in date_str:
                    return (current_year - year) * -0.5 + 3.0
            for year in range(current_year - 3, current_year - 7, -1):
                year_str = str(year)
                if year_str in date_str:
                    return -1.0
            if any(x in date_str for x in ['小时前', '分钟前', '天前', '日前', '最近']):
                return 3.0
            if '2020' in date_str or '2019' in date_str:
                return -2.0
        except Exception:
            pass
        return 0.0

    def _make_raw_text_brief(self, title: str, snippet: str) -> str:
        text = f"{title} {snippet}".strip()
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s\u4e00-\u9fff.,。?!，、；;:\-()]', '', text)
        return text[:300]

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

    def _cmd_search(self, keywords: str, max_results: int = 10, wait_time: int = 1, time_range: str = "week", **kwargs) -> dict:
        """
        用搜索引擎搜索关键词，返回链接列表

        Args:
            keywords: 搜索关键词（必填）
            max_results: 最大结果数（默认 10）
            wait_time: 等待秒数（默认 1）
            time_range: 时间范围筛选（可选）
                        - hour: 1小时内
                        - day: 24小时内
                        - week: 7天内
                        - month: 1个月内

        Returns:
            {"success": bool, "results": [...]}
        """
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

        user_engine = kwargs.get('engine', 'auto')
        if user_engine and user_engine != 'auto' and user_engine in self.SEARCH_ENGINES:
            engines_to_try = [user_engine]
            cache_engine = user_engine
        else:
            cache_engine = 'auto'
            priority_order = ['bing', 'google', 'duckduckgo', 'baidu']
            engines_to_try = []

            best_engine = self._select_best_engine()
            if best_engine in priority_order:
                engines_to_try.append(best_engine)

            for eng in priority_order:
                if eng not in engines_to_try and eng in self.SEARCH_ENGINES:
                    engines_to_try.append(eng)

        cache_check_engine = engines_to_try[0] if engines_to_try else cache_engine
        cached = self._get_cached_result(keywords, cache_check_engine)
        if cached:
            cached['from_cache'] = True
            return cached
        
        logger.info(f"[WebSearch] 将尝试以下引擎: {engines_to_try}")

        last_error = None
        for idx, engine in enumerate(engines_to_try):
            try:
                logger.info(f"[WebSearch] 尝试使用引擎: {engine}")
                force_recreate = idx > 0
                result = self._search_with_engine(keywords, max_results, wait_time, time_range, engine, force_recreate_page=force_recreate)

                if result.get('success') and result.get('results'):
                    logger.info(f"[WebSearch] 引擎 {engine} 搜索成功，返回 {len(result['results'])} 条结果")
                    self._engine_health[engine] = 'green'
                    result['engine'] = engine
                    result['tried_engines'] = engines_to_try[:engines_to_try.index(engine) + 1]
                    self._set_cached_result(keywords, engine, result)
                    return result
                else:
                    logger.warning(f"[WebSearch] 引擎 {engine} 返回空结果或失败: {result.get('error', '未知错误')}")
                    self._engine_health[engine] = 'red'
                    last_error = result.get('error', f'{engine} 返回空结果')

            except Exception as e:
                logger.error(f"[WebSearch] 引擎 {engine} 异常: {e}")
                self._engine_health[engine] = 'red'
                last_error = str(e)
                continue
        
        return {
            "success": False, 
            "error": f"所有搜索引擎均失败，最后错误: {last_error}",
            "tried_engines": engines_to_try,
            "engine_health": self._engine_health.copy()
        }

    def _search_with_engine(self, keywords: str, max_results: int, wait_time: int, time_range: str, engine: str, force_recreate_page: bool = False) -> dict:
        """
        使用指定引擎执行搜索

        Args:
            force_recreate_page: 是否强制重建页面（引擎切换时需要）

        Returns:
            {"success": bool, "results": [...], "error": str}
        """
        try:
            engine_config = self.SEARCH_ENGINES[engine]

            clean_keywords = keywords.replace('年预测', '').replace('年展望', '').strip()
            if not clean_keywords:
                clean_keywords = keywords

            page = self._get_or_create_page(force_recreate=force_recreate_page)
            search_query = clean_keywords
            encoded_query = urllib.parse.quote(search_query)

            recent_templates = engine_config.get('url_template_recent', {})
            if time_range and time_range in recent_templates:
                url = recent_templates[time_range].format(query=encoded_query)
                logger.info(f"[WebSearch:{engine}] 使用时间筛选: {time_range}")
            elif engine == 'bing':
                if re.search(r'\b(20\d{2})\b', search_query) or re.search(r'\b(20\d{2})年\b', search_query):
                    url = f"https://www.bing.com/search?q={encoded_query}"
                else:
                    url = engine_config['url_template'].format(query=encoded_query)
            elif engine in ('baidu', 'google', 'duckduckgo'):
                url = engine_config['url_template'].format(query=encoded_query)
            else:
                url = f"https://www.bing.com/search?q={encoded_query}"

            logger.info(f"[WebSearch:{engine}] 搜索 URL: {url}")

            page.get(url, timeout=20)
            page.wait.doc_loaded()
            if wait_time > 0:
                page.wait(wait_time)
            import random
            random_delay = random.uniform(1, 3)
            logger.info(f"[WebSearch:{engine}] 随机延迟 {random_delay:.1f} 秒防风控")
            page.wait(random_delay)
            logger.info(f"[WebSearch:{engine}] 页面加载完成，当前 URL: {page.url}")

            links = self._extract_links_generic(page, engine)
            logger.info(f"[WebSearch:{engine}] 提取到 {len(links)} 个链接")

            # 过滤规则：排除广告和无关页面，但保留主要网站的内容页
            exclude_patterns = [
                'calendar', '日历',
                '促销', '打折', 'sale', 'discount',
                'holiday', '节日', 'gift', '礼物',
                'amazon.com/dp',  # 亚马逊商品页
                '/search?',  # 搜索结果页（不是内容页）
                'baike.baidu.com/search',  # 百度百科搜索页
                'zhihu.com/search',  # 知乎搜索页
                'baidu.com/video',  # 百度视频
                'haokan.baidu.com',  # 百度视频
                'ixigua.com',  # 头条视频
                'bilibili.com/video',  # B站视频
                'v.douyin.com',  # 抖音视频
                'weibo.com/v',  # 微博视频
            ]

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
                    date_str = ""
                    score = self._score_result(title, snippet, url, keywords, date_str)
                    scored_results.append({
                        'title': title,
                        'url': url,
                        'snippet': snippet,
                        'score': score,
                        'raw_text_brief': self._make_raw_text_brief(title, snippet)
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
                        'score': score,
                        'raw_text_brief': self._make_raw_text_brief(link.get('title', ''), link.get('snippet', ''))
                    })

                    if len(scored_results) >= 5:
                        break

            logger.info(f"[WebSearch:{engine}] 过滤后剩余 {len(scored_results)} 个结果")

            scored_results.sort(key=lambda x: x['score'], reverse=True)

            max_results = min(max_results, 5)

            results = []
            for i, r in enumerate(scored_results[:max_results]):
                # 合并 title 和 snippet，限制总长度约200字符
                brief = f"{r['title']} - {r.get('snippet', '')}".strip()
                brief = re.sub(r'\s+', ' ', brief)[:200]

                results.append({
                    'url': r['url'],
                    'brief': brief,
                    'score': round(r['score'] / 10, 1)
                })

            if not results:
                return {
                    "success": False,
                    "error": f"搜索引擎 {engine} 搜索结果为空"
                }

            return {
                "success": True,
                "results": results,
                "time_range": time_range,
                "hint": "选择相关链接，用 web_fetch.read(url='...', action='open') 获取内容"
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
            "description": "网页搜索工具 - 自动选择最佳搜索引擎",
            "workflow": [
                "1. web_search.search({\"keywords\": \"搜索词\"}) - 搜索获取链接列表",
                "2. 从 results 中选择感兴趣的链接",
                "3. web_fetch.read({\"url\": \"链接\", \"action\": \"open\"}) - 获取页面内容"
            ],
            "available_commands": list(commands.keys()),
            "usage": {
                "search": {
                    "args": {"keywords": "搜索关键词", "max_results": "最大结果数", "wait_time": "等待秒数"},
                    "example": '{"keywords": "Python教程"}'
                }
            },
            "next_tool": "web_fetch"
        }

    def on_load(self):
        super().on_load()