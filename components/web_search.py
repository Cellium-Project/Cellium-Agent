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
import queue
import contextlib
from functools import lru_cache
from typing import Optional, Any, Dict

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
        self._browser_lock = threading.RLock()
        self._pool_lock = threading.Lock()
        self._page_pool: queue.Queue = queue.Queue()
        self._pool_size = 0
        self._page_last_used: Dict[int, dict] = {}
        self._token_buckets: Dict[str, list] = {}
        self._token_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._health_lock = threading.Lock()
        self._browser_port = None
        self._browser_path = None
        self._instance_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
        self._last_used_time: float = 0
        self._idle_timer: Optional[threading.Thread] = None
        self._idle_stop_event = threading.Event()
        self._creation_count = 0

    MAX_POOL_PAGES = 3
    SEARCH_RATE_LIMIT = 2
    SEARCH_RATE_INTERVAL = 5
    IDLE_TIMEOUT = 300
    HEALTH_CHECK_TIMEOUT = 5

    @property
    def cell_name(self) -> str:
        return "web_search"

    def execute(self, command: str, *args, **kwargs) -> Any:
        command = command.strip().strip('>"\'')
        method_name = f"_cmd_{command}"
        if hasattr(self, method_name):
            return getattr(self, method_name)(*args, **kwargs)
        from app.core.exception import CommandNotFoundError
        raise CommandNotFoundError(command, self.cell_name)

    def get_engine_health(self) -> dict:
        """获取搜索引擎健康状态"""
        with self._health_lock:
            return self._engine_health.copy()

    def _set_engine_health(self, engine: str, status: str):
        with self._health_lock:
            self._engine_health[engine] = status

    def _get_cache_key(self, keywords: str, engine: str) -> str:
        """生成缓存键"""
        key_str = f"{engine}:{keywords.lower().strip()}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached_result(self, keywords: str, engine: str) -> dict:
        """获取缓存的搜索结果"""
        cache_key = self._get_cache_key(keywords, engine)
        with self._cache_lock:
            cached = self._search_cache.get(cache_key)
            if not cached:
                return None
            age = time.time() - cached.get('_cached_at', 0)
            if age >= 300:
                return None
            data = dict(cached.get('data') or {})
        logger.info(f"[WebSearch:cache] 命中缓存 (key={cache_key[:8]}, age={age:.1f}s)")
        return data

    def _set_cached_result(self, keywords: str, engine: str, data: dict):
        """设置搜索结果缓存"""
        cache_key = self._get_cache_key(keywords, engine)
        with self._cache_lock:
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
        with self._health_lock:
            health_snapshot = dict(self._engine_health)

        for engine in priority_order:
            if engine not in self.SEARCH_ENGINES:
                continue
            if not self._check_engine_accessible(engine):
                continue
            health = health_snapshot.get(engine, 'unknown')
            if health == 'green':
                logger.info(f"[WebSearch:auto] 选择 {engine} (状态: green)")
                return engine

        for engine in priority_order:
            if engine not in self.SEARCH_ENGINES:
                continue
            if not self._check_engine_accessible(engine):
                continue
            health = health_snapshot.get(engine, 'unknown')
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
            self._user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0'
        
        options.set_argument('--disable-gpu')
        options.set_argument('--no-sandbox')
        options.set_argument('--disable-blink-features=AutomationControlled')
        options.set_argument('--disable-dev-shm-usage')
        options.set_argument('--disable-extensions')
        
        _search_data_dir = os.path.join(tempfile.gettempdir(), 'Cellium_WebSearch_Profile')
        os.makedirs(_search_data_dir, exist_ok=True)
        options.set_argument(f'--user-data-dir={_search_data_dir}')
        options.set_argument('--profile-directory=Default')
        options.set_argument('--disable-plugins-discovery')
        options.set_argument('--disable-infobars')
        options.set_argument('--disable-popup-blocking')
        options.set_argument(f'--user-agent={self._user_agent}')
        options.set_argument('--disable-bundled-ppapi-flash')
        options.set_argument('--mute-audio')
        options.set_argument('--hide-scrollbars')
        options.set_argument('--disable-background-networking')
        
        options.set_argument('--disable-features=IsolateOrigins,site-per-process,ImprovedCookieControls,LazyFrameLoading,GlobalMediaControls,DestroyProfileOnBrowserClose')
        options.set_argument('--enable-features=NetworkService,NetworkServiceInProcess')
        options.set_argument('--disable-site-isolation-trials')
        options.set_argument('--window-size=1920,1080')
        options.set_argument('--start-maximized')
        options.set_argument('--disable-notifications')
        options.set_argument('--disable-translate')
        options.set_argument('--disable-save-password-bubble')
        
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

    def _ensure_browser(self):
        with self._browser_lock:
            if self._browser is not None:
                return self._browser

            last_error = None
            for attempt in range(3):
                try:
                    from DrissionPage import Chromium

                    if attempt > 0:
                        logger.info(f"[WebSearch] 浏览器重试 ({attempt + 1}/3)，使用新端口...")
                        self._browser_port = None
                        self._browser_path = None
                        time.sleep(1)

                    co = self._get_options()
                    browser = Chromium(addr_or_opts=co)
                    if browser is None:
                        raise RuntimeError("Chromium 浏览器创建失败")

                    browser_ready = False
                    for warmup in range(2):
                        try:
                            time.sleep(0.2)
                            if not browser.address:
                                continue
                            latest = browser.latest_tab
                            if latest is None:
                                continue
                            if self._page is None:
                                self._page = latest
                            _ = latest.driver
                            browser_ready = True
                            break
                        except (AttributeError, TypeError) as we:
                            logger.debug(f"[WebSearch] 浏览器预热 {warmup + 1}/2: {we}")
                            continue

                    if not browser_ready:
                        try:
                            browser.quit()
                        except Exception:
                            pass
                        raise RuntimeError("浏览器启动后无法建立连接")

                    try:
                        if browser.address:
                            addr_parts = browser.address.split(':')
                            if len(addr_parts) == 2:
                                self._browser_port = int(addr_parts[1])
                    except Exception as e:
                        logger.debug(f"[WebSearch] 获取端口失败: {e}")

                    self._browser = browser
                    self._creation_count += 1
                    self._update_last_used_time()
                    self._start_idle_timer()
                    logger.info(f"[WebSearch] 浏览器创建成功 (端口: {self._browser_port}, 累计创建: {self._creation_count})")
                    return self._browser
                except AttributeError as e:
                    last_error = e
                    logger.warning(f"[WebSearch] 浏览器创建失败 AttributeError (尝试 {attempt + 1}/3): {e}")
                    self._close_page_internal()
                except TypeError as e:
                    last_error = e
                    logger.warning(f"[WebSearch] 浏览器创建失败 TypeError (尝试 {attempt + 1}/3): {e}")
                    self._close_page_internal()
                except Exception as e:
                    last_error = e
                    logger.warning(f"[WebSearch] 浏览器创建失败 (尝试 {attempt + 1}/3): {e}")
                    self._close_page_internal()

            logger.error(f"[WebSearch] 浏览器创建最终失败: {last_error}")
            raise last_error

    def _create_pool_page(self):
        with self._browser_lock:
            browser = self._ensure_browser()
            page = None
            if self._page is not None:
                page = self._page
                self._page = None
            if page is None:
                page = browser.new_tab()
            if page is None:
                raise RuntimeError("无法创建新标签页")

            try:
                page.run_js("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
                    window.chrome = {runtime: {}};
                """)
            except Exception as js_e:
                logger.debug(f"[WebSearch] 注入反检测脚本失败: {js_e}")

            with self._pool_lock:
                self._page_last_used[id(page)] = {'page': page, 'last_used': time.time()}
            logger.info(f"[WebSearch] 标签页创建成功 (池占用: {self._pool_size}/{self.MAX_POOL_PAGES})")
            return page

    def _acquire_page(self):
        while True:
            try:
                page = self._page_pool.get_nowait()
            except queue.Empty:
                page = None
            if page is not None:
                self._touch_page(page)
                return page

            reserve = False
            with self._pool_lock:
                if self._pool_size < self.MAX_POOL_PAGES:
                    self._pool_size += 1
                    reserve = True

            if reserve:
                try:
                    page = self._create_pool_page()
                except Exception:
                    with self._pool_lock:
                        self._pool_size -= 1
                    raise
                self._touch_page(page)
                return page

            try:
                page = self._page_pool.get(timeout=0.5)
            except queue.Empty:
                continue
            if page is not None:
                self._touch_page(page)
                return page

    def _touch_page(self, page):
        now = time.time()
        with self._pool_lock:
            entry = self._page_last_used.get(id(page))
            if entry is None:
                self._page_last_used[id(page)] = {'page': page, 'last_used': now}
            else:
                entry['last_used'] = now
        self._update_last_used_time()

    def _release_page(self, page):
        if page is None:
            return
        with self._pool_lock:
            entry = self._page_last_used.get(id(page))
            if entry is None:
                return
            entry['last_used'] = time.time()
        self._page_pool.put(page)

    def _drop_page(self, page):
        if page is None:
            return
        with self._pool_lock:
            entry = self._page_last_used.pop(id(page), None)
            if entry is not None and self._pool_size > 0:
                self._pool_size -= 1
        if self._page is page:
            self._page = None
        with self._browser_lock:
            try:
                page.close()
            except Exception as e:
                logger.debug("[WebSearch] 关闭标签页失败: %s", e)
        logger.info(f"[WebSearch] 已丢弃异常标签页 (池占用: {self._pool_size}/{self.MAX_POOL_PAGES})")

    @contextlib.contextmanager
    def _page_slot(self):
        page = self._acquire_page()
        try:
            yield page
        finally:
            self._release_page(page)

    def _is_page_alive(self, page=None) -> bool:
        """检查页面是否仍然可用"""
        page = self._page if page is None else page
        try:
            if page is None:
                return False
            _ = page.url
            _ = page.driver
            return True
        except Exception as e:
            logger.debug(f"[WebSearch] 页面连接检查失败: {e}")
            return False

    def _health_check(self, page=None) -> bool:
        page = self._page if page is None else page
        if page is None or self._browser is None:
            logger.debug("[WebSearch] 健康检查：无浏览器实例")
            return False
        try:
            url = page.url
            if url is None:
                logger.debug("[WebSearch] 健康检查：url 为 None")
                return False
            _ = page.driver
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
                try:
                    all_idle = self._reclaim_idle_pages()
                except Exception as e:
                    logger.debug("[WebSearch] 空闲回收异常: %s", e)
                    continue
                if all_idle:
                    logger.info("[WebSearch] 全部标签页空闲超时，自动关闭浏览器")
                    with self._lock:
                        self._close_page_internal()
                    break
        self._idle_timer = threading.Thread(target=_idle_monitor, daemon=True, name="WebSearchIdleMonitor")
        self._idle_timer.start()
        logger.debug("[WebSearch] 空闲监控线程已启动")

    def _reclaim_idle_pages(self) -> bool:
        pooled = []
        while True:
            try:
                pooled.append(self._page_pool.get_nowait())
            except queue.Empty:
                break

        now = time.time()
        idle_ids = set()
        kept = []
        with self._pool_lock:
            for page in pooled:
                entry = self._page_last_used.get(id(page))
                if entry is None or now - entry.get('last_used', 0) > self.IDLE_TIMEOUT:
                    idle_ids.add(id(page))
                else:
                    kept.append(page)

        for page in pooled:
            if id(page) in idle_ids:
                logger.info("[WebSearch] 标签页空闲超时，回收")
                self._drop_page(page)
        for page in kept:
            self._page_pool.put(page)

        with self._pool_lock:
            return self._pool_size <= 0

    def _stop_idle_timer(self):
        if self._idle_timer is not None and self._idle_timer.is_alive():
            self._idle_stop_event.set()
            self._idle_timer.join(timeout=2)
            logger.debug("[WebSearch] 空闲监控线程已停止")

    def _close_page_internal(self):
        with self._browser_lock:
            drained = []
            while True:
                try:
                    drained.append(self._page_pool.get_nowait())
                except queue.Empty:
                    break
            with self._pool_lock:
                self._page_last_used.clear()
                self._pool_size = 0
            for page in drained:
                try:
                    page.close()
                except Exception as e:
                    logger.debug("[WebSearch] 关闭标签页失败: %s", e)
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
            if engine == 'baidu':
                content_left = page.ele('#content_left', timeout=2)
                if content_left:
                    result_items = content_left.children()
                else:
                    result_items = []
            else:
                selector = config.get('result_selector', '')
                if selector and not selector.startswith('css:'):
                    selector = f'css:{selector}'
                result_items = page.eles(selector, timeout=2)
            
            logger.info(f"[WebSearch:{engine}] DOM 解析找到 {len(result_items)} 个搜索结果项")
            for item in result_items:
                try:
                    if engine == 'bing':
                        h2 = item.ele('css:h2', timeout=0.5)
                        link_elem = h2.ele('css:a', timeout=0.5) if h2 else item.ele('css:a', timeout=0.5)
                    elif engine == 'baidu':
                        h3 = item.ele('css:h3', timeout=0.5)
                        if h3:
                            link_elem = h3.ele('css:a', timeout=0.5)
                        else:
                            all_links = item.eles('css:a', timeout=0.5)
                            link_elem = None
                            for a in all_links:
                                href = a.attr('href') or ''
                                text = a.text.strip() if a.text else ""
                                if href and text and len(text) > 5 and not href.startswith('javascript:'):
                                    link_elem = a
                                    break
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
                            if href.startswith('javascript:') or 'baidu.com/baidu.php' in href:
                                continue
                            
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

    def _extract_links_regex(self, html: str, engine: str = 'bing', strict: bool = False):
        """正则 fallback 解析器

        strict=True 时只用引擎专属规则，不回退到通用 <a> 抓取，
        避免 HTTP 快速通道把导航/推荐链接当成搜索结果。
        """
        links = []
        try:
            generic = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', re.DOTALL)
            patterns = []
            if engine == 'bing':
                patterns = [
                    re.compile(r'<li[^>]*>.*?h="ID=SERP[^>]+>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            elif engine == 'baidu':
                patterns = [
                    re.compile(r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            elif engine == 'google':
                patterns = [
                    re.compile(r'<div[^>]*class="g"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            elif engine == 'duckduckgo':
                patterns = [
                    re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.DOTALL),
                ]
            if not strict or not patterns:
                patterns = patterns + [generic]

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
        if 'baidu.com/link?' in url:
            try:
                import requests
                r = requests.head(url, allow_redirects=True, timeout=1, headers={'User-Agent': self._user_agent or 'Mozilla/5.0'})
                if r.url and r.url != url:
                    return r.url
            except Exception as e:
                logger.debug(f"[WebSearch] 百度短链接解析失败: {e}")
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

    def _wait_search_slot(self, engine: str):
        key = engine or 'auto'
        capacity = float(self.SEARCH_RATE_LIMIT)
        interval = float(self.SEARCH_RATE_INTERVAL)
        while True:
            with self._token_lock:
                now = time.time()
                bucket = self._token_buckets.get(key)
                if bucket is None:
                    bucket = [capacity, now]
                    self._token_buckets[key] = bucket
                else:
                    bucket[0] = min(capacity, bucket[0] + (now - bucket[1]) * capacity / interval)
                    bucket[1] = now
                if bucket[0] >= 1.0:
                    bucket[0] -= 1.0
                    return
                wait_needed = (1.0 - bucket[0]) * interval / capacity
            logger.info("[WebSearch:%s] 令牌桶限流，等待 %.2f 秒", key, wait_needed)
            time.sleep(max(0.01, min(wait_needed, 0.5)))

    def _build_engine_url(self, keywords: str, engine: str, time_range: str = "week",
                          page_index: int = 0) -> str:
        """构建搜索引擎 URL（含时间筛选与翻页参数）"""
        cfg = self.SEARCH_ENGINES.get(engine) or self.SEARCH_ENGINES['bing']
        encoded = urllib.parse.quote(keywords)
        recent = cfg.get('url_template_recent', {})
        if time_range and time_range in recent:
            url = recent[time_range].format(query=encoded)
        elif engine == 'bing' and re.search(r'\b20\d{2}\b', keywords):
            url = f"https://www.bing.com/search?q={encoded}"
        else:
            url = cfg['url_template'].format(query=encoded)

        if page_index > 0:
            sep = '&' if '?' in url else '?'
            if engine == 'bing':
                url += f"{sep}first={1 + page_index * 10}"
            elif engine == 'baidu':
                url += f"{sep}pn={page_index * 10}"
            elif engine == 'google':
                url += f"{sep}start={page_index * 10}"
            elif engine == 'duckduckgo':
                url += f"{sep}s={page_index * 30}"
        return url

    def _http_search(self, keywords: str, engine: str, time_range: str = "week",
                     max_results: int = 10) -> dict:
        """HTTP 快速搜索：直接拉引擎 HTML 正则解析，浏览器路径的加速前置"""
        try:
            import httpx
        except Exception:
            return {"success": False, "error": "httpx 不可用"}

        headers = {
            "User-Agent": self._user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        pages = 1 if max_results <= 10 else min(2, (max_results + 9) // 10)
        collected, seen = [], set()
        try:
            with httpx.Client(follow_redirects=True, timeout=8, headers=headers) as client:
                for page_idx in range(pages):
                    resp = client.get(self._build_engine_url(keywords, engine, time_range, page_idx))
                    if resp.status_code >= 400:
                        break
                    for link in self._extract_links_regex(resp.text, engine, strict=True):
                        url = (link.get('url') or '').strip()
                        if not url or url in seen:
                            continue
                        seen.add(url)
                        collected.append(link)
                    if len(collected) >= max_results * 2:
                        break
        except Exception as e:
            return {"success": False, "error": str(e)}

        if not collected:
            return {"success": False, "error": "HTTP 模式未解析到结果"}

        scored = []
        for link in collected[: max_results * 3]:
            title = link.get('title', '')
            url = link.get('url', '')
            snippet = link.get('snippet', '')
            scored.append({
                'title': title,
                'url': url,
                'snippet': snippet,
                'score': self._score_result(title, snippet, url, keywords, ""),
                'raw_text_brief': self._make_raw_text_brief(title, snippet),
            })
        scored.sort(key=lambda x: x['score'], reverse=True)
        return {"success": True, "results": scored[:max_results], "from_http": True}

    def _cmd_search(self, keywords: str, max_results: int = 10, wait_time: int = 1, time_range: str = "week", engine: str = "auto", site: str = None, filetype: str = None, **kwargs) -> dict:
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
            engine: 搜索引擎（默认 auto 自动选择）
                        - auto: 自动选择最优引擎
                        - bing, baidu, google, duckduckgo
            site: 限定站点（可选），如 github.com
            filetype: 限定文件类型（可选），如 pdf

        Returns:
            {"success": bool, "results": [...]}
        """
        if not keywords or not keywords.strip():
            return {"success": False, "error": "缺少必填参数 'keywords'"}

        keywords = keywords.strip()
        if site:
            keywords = f"{keywords} site:{str(site).strip()}"
        if filetype:
            keywords = f"{keywords} filetype:{str(filetype).strip().lstrip('.')}"

        user_engine = engine
        if user_engine and user_engine != 'auto' and user_engine in self.SEARCH_ENGINES:
            engines_to_try = [user_engine]
            cache_engine = user_engine
        else:
            cache_engine = 'auto'
            priority_order = ['baidu', 'bing', 'duckduckgo', 'google']
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
        for engine in engines_to_try:
            self._wait_search_slot(engine)
            logger.info(f"[WebSearch] 尝试使用引擎: {engine}")

            # 先走 HTTP 直连（快），结果足够就不启动浏览器
            fast = self._http_search(keywords, engine, time_range, max_results)
            if fast.get('success') and len(fast.get('results') or []) >= min(3, max_results):
                logger.info(f"[WebSearch] 引擎 {engine} HTTP 快速搜索成功")
                self._set_engine_health(engine, 'green')
                fast['engine'] = engine
                fast['tried_engines'] = [engine]
                self._set_cached_result(keywords, engine, fast)
                return fast

            result = None
            for attempt in range(2):
                page = None
                try:
                    page = self._acquire_page()
                    if not self._health_check(page):
                        logger.info("[WebSearch] 标签页健康检查失败，丢弃后重新取页")
                        self._drop_page(page)
                        page = None
                        continue
                    result = self._search_with_engine(keywords, max_results, wait_time, time_range, engine, page)
                except Exception as e:
                    logger.error(f"[WebSearch] 引擎 {engine} 页面异常 (尝试 {attempt + 1}/2): {e}")
                    if page is not None:
                        self._drop_page(page)
                        page = None
                    result = {"success": False, "error": str(e), "page_error": True}

                if result.get('page_error'):
                    if page is not None:
                        self._drop_page(page)
                        page = None
                    continue

                if page is not None:
                    self._release_page(page)
                    page = None
                break

            result = result or {"success": False, "error": f"{engine} 未获得可用标签页"}

            if result.get('success') and result.get('results'):
                # 去重：同一 URL 只保留评分最高/最先出现的一条
                seen = set()
                unique = []
                for item in result['results']:
                    key = (item.get('url') or '').strip().rstrip('/')
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    unique.append(item)
                result['results'] = unique[:max_results]
                logger.info(f"[WebSearch] 引擎 {engine} 搜索成功，返回 {len(result['results'])} 条结果")
                self._set_engine_health(engine, 'green')
                result.pop('page_error', None)
                result['engine'] = engine
                result['tried_engines'] = engines_to_try[:engines_to_try.index(engine) + 1]
                self._set_cached_result(keywords, engine, result)
                return result

            logger.warning(f"[WebSearch] 引擎 {engine} 返回空结果或失败: {result.get('error', '未知错误')}")
            self._set_engine_health(engine, 'red')
            last_error = result.get('error', f'{engine} 返回空结果')

        return {
            "success": False,
            "error": f"所有搜索引擎均失败，最后错误: {last_error}",
            "tried_engines": engines_to_try,
            "engine_health": self.get_engine_health()
        }

    def _search_with_engine(self, keywords: str, max_results: int, wait_time: int, time_range: str, engine: str, page) -> dict:
        """
        使用指定引擎执行搜索

        Args:
            page: 页池分配的标签页

        Returns:
            {"success": bool, "results": [...], "error": str}
        """
        try:
            engine_config = self.SEARCH_ENGINES[engine]

            clean_keywords = keywords.replace('年预测', '').replace('年展望', '').strip()
            if not clean_keywords:
                clean_keywords = keywords

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

            page.get(url, timeout=10)
            try:
                page.wait.doc_loaded(timeout=5)
            except Exception as e:
                logger.warning(f"[WebSearch:{engine}] 文档加载超时: {e}")
            if wait_time > 0:
                page.wait(wait_time)
            import random
            random_delay = random.uniform(0.3, 0.8)
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
                for link in short_links[:3]:
                    short_url = link['url']
                    decoded = None

                    if engine == 'bing':
                        decoded = self._decode_bing_url(short_url)
                    elif engine == 'baidu':
                        decoded = self.resolve_url(short_url)

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
                brief = f"{r['title']} - {r.get('snippet', '')}".strip()
                brief = re.sub(r'\s+', ' ', brief)[:200]

                results.append({
                    'url': r['url'],
                    'title': r['title'],
                    'snippet': r.get('snippet', ''),
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
            logger.warning(f"[WebSearch:{engine}] 搜索异常，标签页将被丢弃: {e}")
            return {"success": False, "error": str(e), "page_error": True}

    def _cmd_engines(self) -> dict:
        """查看可用搜索引擎及健康状态"""
        health = self.get_engine_health()
        return {
            "success": True,
            "engines": [
                {
                    "engine": key,
                    "name": cfg.get("name", key),
                    "health": health.get(key, "unknown"),
                }
                for key, cfg in self.SEARCH_ENGINES.items()
            ],
        }

    def _cmd_resolve(self, url: str) -> dict:
        """把搜索结果里的跳转链接（Bing ck/a、百度 link）还原成真实 URL

        Args:
            url: 搜索返回的链接
        """
        if not url or not url.strip():
            return {"success": False, "error": "需要提供 url"}
        raw = url.strip()
        resolved = ""
        if "bing.com" in raw and "/ck/a?" in raw:
            resolved = self._decode_bing_url(raw)
        if not resolved:
            resolved = self.resolve_url(raw)
        if resolved == raw:
            try:
                import requests

                resp = requests.get(
                    raw,
                    allow_redirects=True,
                    timeout=8,
                    headers={"User-Agent": self._user_agent or "Mozilla/5.0"},
                )
                if resp.url and resp.url != raw:
                    resolved = resp.url
                resp.close()
            except Exception as e:
                return {"success": False, "url": raw, "error": f"解析失败: {e}"}
        return {
            "success": True,
            "url": raw,
            "resolved": resolved,
            "changed": resolved != raw,
        }

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
                    "args": {"keywords": "搜索关键词", "max_results": "最大结果数", "wait_time": "等待秒数", "time_range": "hour/day/week/month", "engine": "auto/bing/baidu/google/duckduckgo"},
                    "example": '{"keywords": "Python教程"}'
                },
                "resolve": {
                    "args": {"url": "搜索返回的跳转链接"},
                    "example": '{"url": "https://www.bing.com/ck/a?..."} 还原为真实 URL'
                },
                "engines": {
                    "args": {},
                    "example": "查看各引擎健康状态"
                }
            },
            "next_tool": "web_fetch"
        }

    def on_load(self):
        super().on_load()