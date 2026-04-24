# -*- coding: utf-8 -*-
"""
网页抓取工具 - 用无头浏览器抓取页面正文内容
"""

import logging
import re
import json
import os
import time
import base64
import concurrent.futures
import hashlib
import threading
from typing import Dict, Any, Optional, Tuple

import httpx
from DrissionPage import ChromiumPage, ChromiumOptions

from app.core.interface.base_cell import BaseCell
from app.core.util.browser_utils import find_browser_path, get_browser_candidates
from app.core.util.browser_runtime import get_runtime_download_spec

logger = logging.getLogger(__name__)

_MAX_PARALLEL_TABS = 5


class WebFetch(BaseCell):
    """
    网页抓取工具 - 无头浏览器抓取页面正文

    功能说明:
      - fetch: 用无头浏览器抓取页面正文内容（支持 JS 渲染）
      - read: 支持分页读取，同一URL在短时间内会复用缓存
    """

    #页面缓存：url -> {timestamp, page, content}
    _page_cache: Dict[str, Dict[str, Any]] = {}
    _cache_ttl = 300  # 缓存有效期 5 分钟
    MAX_WAIT_TIME = 30  # 最大等待时间（秒）

    # 通用类名列表
    GENERIC_CLASSES = {
        'btn', 'button', 'link', 'item', 'active', 'disabled', 'selected',
        'hover', 'focus', 'open', 'show', 'hide', 'hidden', 'visible',
        'container', 'wrapper', 'content', 'inner', 'outer', 'box',
        'left', 'right', 'center', 'top', 'bottom', 'middle',
        'col', 'row', 'col-xs', 'col-sm', 'col-md', 'col-lg',
        'pull-left', 'pull-right', 'text-left', 'text-right', 'text-center',
        'clearfix', 'float-left', 'float-right', 'd-none', 'd-block', 'd-inline'
    }

    MAX_HTTP_CACHE_SIZE = 5 * 1024 * 1024  # 5MB
    IDLE_TIMEOUT = 300  # 空闲超时时间（秒），5分钟
    HEALTH_CHECK_TIMEOUT = 5  # 健康检查超时时间（秒）

    def __init__(self):
        super().__init__()
        self._page = None
        self._browser = None
        self._headless = True
        self._current_url = None
        self._working_browser_path: Optional[str] = None
        self._http_content_cache: Optional[Dict[str, Any]] = None
        self._working_browser_name: Optional[str] = None
        self._browser_probe_failed = False
        self._browser_port = None
        self._instance_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
        self._lock = threading.RLock()
        self._last_used_time: float = 0
        self._idle_timer: Optional[threading.Thread] = None
        self._idle_stop_event = threading.Event()
        self._creation_count = 0

    @property
    def cell_name(self) -> str:
        return "web_fetch"

    def execute(self, command: str, *args, **kwargs) -> Any:
        command = command.strip().strip('>"\'')
        method_name = f"_cmd_{command}"
        if hasattr(self, method_name):
            with self._lock:
                return getattr(self, method_name)(*args, **kwargs)
        from app.core.exception import CommandNotFoundError
        raise CommandNotFoundError(command, self.cell_name)

    def _is_generic_class(self, class_name: str) -> bool:
        """判断类名是否过于通用，不适合作为选择器"""
        # 检查完全匹配
        if class_name.lower() in self.GENERIC_CLASSES:
            return True
        # 检查前缀匹配（如 col-md-6）
        for prefix in ['col-', 'col-xs-', 'col-sm-', 'col-md-', 'col-lg-', 'd-', 'm-', 'p-', 'text-']:
            if class_name.startswith(prefix):
                return True
        return False

    def _build_options(self, browser_path: Optional[str] = None, force_headless: bool = None):
        """
        获取浏览器配置

        Args:
            browser_path: 指定浏览器路径（None 则自动查找）
            force_headless: 强制指定 headless 模式（None 则使用实例设置）
        """
        import random

        options = ChromiumOptions()
        resolved_browser_path = browser_path or self._working_browser_path or find_browser_path()
        if resolved_browser_path:
            options.set_browser_path(resolved_browser_path)
            options.auto_port()
            if 'msedge' in resolved_browser_path.lower() or 'edge' in resolved_browser_path.lower():
                options.set_argument('--no-first-run')
                options.set_argument('--no-default-browser-check')
                options.set_argument('--no-singleton')

        options.set_argument('--disable-gpu')
        options.set_argument('--no-sandbox')
        options.set_argument('--disable-blink-features=AutomationControlled')
        options.set_argument('--disable-dev-shm-usage')
        options.set_argument('--disable-extensions')
        import tempfile
        _fetch_data_dir = os.path.join(tempfile.gettempdir(), 'Cellium_WebFetch_Profile')
        os.makedirs(_fetch_data_dir, exist_ok=True)
        options.set_argument(f'--user-data-dir={_fetch_data_dir}')
        options.set_argument('--profile-directory=Default')
        options.set_argument('--disable-plugins-discovery')
        options.set_argument('--disable-infobars')
        options.set_argument('--disable-popup-blocking')
        options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

        is_headless = force_headless if force_headless is not None else self._headless
        options.headless(is_headless)

        if is_headless:
            options.set_argument('--disable-remote-fonts')
            options.set_argument('--mute-audio')
            options.set_argument('--disable-background-networking')

        return options

    def _get_options(self, force_headless: bool = None):
        return self._build_options(force_headless=force_headless)

    def _probe_browser(self, browser_path: str) -> Tuple[bool, Optional[str]]:
        page = None
        try:
            page = ChromiumPage(self._build_options(browser_path=browser_path), timeout=10)
            _ = page.url
            return True, None
        except Exception as e:
            return False, str(e)
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass

    def _get_working_browser_path(self) -> Optional[str]:
        if self._working_browser_path:
            return self._working_browser_path

        # 直接使用 find_browser_path() 获取 Edge 路径
        browser_path = find_browser_path()
        if browser_path:
            self._working_browser_path = browser_path
            self._working_browser_name = "edge"
            logger.info(f"[WebFetch] 使用浏览器: {browser_path}")
        else:
            logger.warning("[WebFetch] 未找到可用浏览器")
            self._browser_probe_failed = True

        return self._working_browser_path

    def _browser_unavailable_error(self) -> dict:
        download_spec = get_runtime_download_spec()
        return {
            "success": False,
            "error": "浏览器模式不可用：已探测的浏览器均启动失败，请先使用 open/fetch 的 HTTP fallback 或修复本机浏览器环境",
            "can_download_browser": bool(download_spec.get("can_download")),
            "download_browser_runtime": download_spec,
        }

    def _resolve_browser_backend(self, require_browser: bool = False) -> Dict[str, Any]:
        browser_path = self._get_working_browser_path()
        download_spec = get_runtime_download_spec()
        if browser_path:
            return {
                "status": "ready",
                "browser_path": browser_path,
                "browser_source": self._working_browser_name,
                "can_download_browser": False,
                "download_browser_runtime": None,
            }
        if require_browser:
            return {
                "status": "downloadable",
                "browser_path": None,
                "browser_source": None,
                "can_download_browser": bool(download_spec.get("can_download")),
                "download_browser_runtime": download_spec,
                "reason": "no_working_browser_found",
            }
        return {
            "status": "http_only",
            "browser_path": None,
            "browser_source": None,
            "can_download_browser": False,
            "download_browser_runtime": None,
            "reason": "fall_back_to_http",
        }

    def _http_fetch_text(self, url: str, timeout: int = 20) -> dict:
        try:
            import random

            browser_headers = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ]

            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                response = client.get(url, headers={
                    "User-Agent": random.choice(browser_headers),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Cache-Control": "max-age=0",
                })
                response.raise_for_status()
                html = response.text

            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, flags=re.IGNORECASE | re.DOTALL)
            title = self._clean_text(title_match.group(1)) if title_match else ""

            body_match = re.search(r'<body[^>]*>(.*?)</body>', html, flags=re.IGNORECASE | re.DOTALL)
            body_html = body_match.group(1) if body_match else html
            full_text = self._clean_text(body_html)

            chunks = [full_text[i:i+1500] for i in range(0, len(full_text), 1500)]
            total_pages = len(chunks)
            total_size = sum(len(c.encode('utf-8')) for c in chunks)

            if total_size > self.MAX_HTTP_CACHE_SIZE:
                logger.warning(f"[WebFetch] HTTP 内容过大 ({total_size/1024/1024:.1f}MB > {self.MAX_HTTP_CACHE_SIZE/1024/1024:.0f}MB)，跳过缓存")
                self._http_content_cache = None
                return {
                    "success": True,
                    "url": str(response.url),
                    "title": title,
                    "text": full_text[:2000],
                    "mode": "http",
                    "truncated": True,
                    "hint": f"内容过大 ({total_size/1024/1024:.1f}MB)，仅返回前 2000 字符",
                }
            else:
                self._http_content_cache = {
                    "url": str(response.url),
                    "title": title,
                    "chunks": chunks,
                    "current_page": 0,
                }
                return {
                    "success": True,
                    "url": str(response.url),
                    "title": title,
                    "text": chunks[0] if chunks else "",
                    "mode": "http",
                    "current_page": 0,
                    "total_pages": total_pages,
                    "hint": f"共 {total_pages} 页，当前第 1 页 | fetch(action='next') 下一页 | fetch(action='goto', page=N) 跳转",
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _set_headless(self, headless: bool):
        if self._headless != headless:
            old_mode = "headless" if self._headless else "可视化"
            new_mode = "headless" if headless else "可视化"
            self._headless = headless
            
            had_browser = self._browser is not None
            self._close_page()
            self._current_url = None
            self._browser_port = None
            
            logger.info(f"[WebFetch] 模式切换: {old_mode} → {new_mode}，浏览器已关闭")

    def _get_page(self, force_recreate: bool = False, max_retries: int = 3):
        """获取或创建页面（带复用、健康检查和重试机制）

        Args:
            force_recreate: 强制重建浏览器页面
            max_retries: 最大重试次数
        """
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
                    logger.info("[WebFetch] 健康检查失败，关闭旧实例准备重建")
                    self._close_page_internal()

            self._close_page_internal()
            browser_path = self._get_working_browser_path()
            if not browser_path:
                raise RuntimeError("没有可用的浏览器后端")

            last_error = None
            for attempt in range(max_retries):
                try:
                    from DrissionPage import Chromium

                    if attempt == 0 and self._browser_port:
                        try:
                            self._browser = Chromium(f"127.0.0.1:{self._browser_port}")
                            self._page = self._browser.latest_tab
                            if self._health_check():
                                logger.info(f"[WebFetch] 复用已有浏览器 (端口: {self._browser_port})")
                                self._update_last_used_time()
                                self._start_idle_timer()
                                return self._page
                            else:
                                logger.warning("[WebFetch] 复用的浏览器健康检查失败")
                                self._close_page_internal()
                        except Exception as e:
                            logger.debug(f"[WebFetch] 连接已有浏览器失败: {e}")
                            self._close_page_internal()
                    elif attempt > 0:
                        logger.info(f"[WebFetch] 浏览器重试 ({attempt + 1}/{max_retries})，使用新端口...")
                        self._browser_port = None
                        time.sleep(1)

                    co = self._build_options(browser_path=browser_path)
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
                            logger.debug(f"[WebFetch] 浏览器预热 {warmup+1}/3: {we}")
                            continue
                    
                    if not browser_ready:
                        raise RuntimeError("浏览器启动后无法建立连接")
                    
                    try:
                        if hasattr(self._browser, 'address') and self._browser.address:
                            addr_parts = self._browser.address.split(':')
                            if len(addr_parts) == 2:
                                self._browser_port = int(addr_parts[1])
                    except Exception as e:
                        logger.debug(f"[WebFetch] 获取端口失败: {e}")
                    
                    if self._page is None:
                        self._page = self._browser.latest_tab
                    if self._page is None:
                        self._page = self._browser.new_tab()
                        if self._page is None:
                            raise RuntimeError("无法获取浏览器标签页")
                    
                    self._creation_count += 1
                    self._update_last_used_time()
                    self._start_idle_timer()
                    logger.info(f"[WebFetch] 浏览器页面创建成功 (端口: {self._browser_port}, 累计创建: {self._creation_count})")
                    return self._page
                except AttributeError as e:
                    last_error = e
                    logger.warning(f"[WebFetch] 浏览器创建失败 AttributeError (尝试 {attempt + 1}/{max_retries}): {e}")
                    self._close_page_internal()
                except TypeError as e:
                    last_error = e
                    logger.warning(f"[WebFetch] 浏览器创建失败 TypeError (尝试 {attempt + 1}/{max_retries}): {e}")
                    self._close_page_internal()
                except Exception as e:
                    last_error = e
                    logger.warning(f"[WebFetch] 浏览器创建失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    self._close_page_internal()

            logger.error(f"[WebFetch] 浏览器创建最终失败: {last_error}")
            self._working_browser_path = None
            self._working_browser_name = None
            self._browser_probe_failed = False
            raise RuntimeError(f"浏览器初始化失败: {last_error}")

    def _is_page_alive(self) -> bool:
        """检查页面是否仍然可用"""
        try:
            if self._page:
                _ = self._page.url
                _ = self._page.driver
                return True
        except Exception as e:
            logger.debug(f"[WebFetch] 页面连接检查失败: {e}")
        return False

    def _health_check(self) -> bool:
        """健康检查：ping 浏览器实例是否正常响应
        
        Returns:
            True: 浏览器健康可用
            False: 浏览器异常，需要重建
        """
        if self._page is None or self._browser is None:
            logger.debug("[WebFetch] 健康检查：无浏览器实例")
            return False
        
        try:
            url = self._page.url
            if url is None:
                logger.debug("[WebFetch] 健康检查：url 为 None")
                return False
            
            _ = self._page.driver
            logger.debug("[WebFetch] 健康检查通过")
            return True
        except AttributeError as e:
            logger.warning("[WebFetch] 健康检查失败（AttributeError）: %s", e)
            return False
        except TypeError as e:
            logger.warning("[WebFetch] 健康检查失败（TypeError）: %s", e)
            return False
        except Exception as e:
            logger.warning("[WebFetch] 健康检查失败: %s", e)
            return False

    def _update_last_used_time(self):
        """更新最后使用时间"""
        self._last_used_time = time.time()

    def _start_idle_timer(self):
        """启动空闲清理定时器（后台线程）"""
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
                        logger.info("[WebFetch] 浏览器空闲超时 (%.0f秒)，自动关闭", idle_time)
                        self._close_page_internal()
        
        self._idle_timer = threading.Thread(target=_idle_monitor, daemon=True, name="WebFetchIdleMonitor")
        self._idle_timer.start()
        logger.debug("[WebFetch] 空闲监控线程已启动")

    def _stop_idle_timer(self):
        """停止空闲清理定时器"""
        if self._idle_timer is not None and self._idle_timer.is_alive():
            self._idle_stop_event.set()
            self._idle_timer.join(timeout=2)
            logger.debug("[WebFetch] 空闲监控线程已停止")

    def _close_page_internal(self):
        """内部关闭方法（不加锁，由调用者保证线程安全）"""
        if self._page:
            try:
                self._page.quit()
            except Exception as e:
                logger.debug("[WebFetch] 关闭页面失败: %s", e)
            finally:
                self._page = None
        if self._browser:
            try:
                self._browser.quit()
            except Exception as e:
                logger.debug("[WebFetch] 关闭浏览器失败: %s", e)
            finally:
                self._browser = None
        self._browser_port = None
        self._last_used_time = 0

    def _close_page(self):
        """关闭浏览器页面（公开方法，带锁）"""
        with self._lock:
            self._stop_idle_timer()
            self._close_page_internal()

    def __del__(self):
        """析构时确保关闭浏览器"""
        try:
            self._stop_idle_timer()
            if self._page or self._browser:
                self._close_page_internal()
        except Exception:
            pass

    def _safe_browser_operation(self, operation: callable, *args, **kwargs):
        """安全执行浏览器操作，失败时自动恢复
        
        Args:
            operation: 要执行的操作函数
            *args, **kwargs: 操作参数
            
        Returns:
            操作结果，失败返回 None
        """
        try:
            return operation(*args, **kwargs)
        except AttributeError as e:
            logger.warning("[WebFetch] 操作失败 AttributeError: %s，尝试恢复", e)
            self._close_page()
            return None
        except TypeError as e:
            logger.warning("[WebFetch] 操作失败 TypeError: %s，尝试恢复", e)
            self._close_page()
            return None
        except Exception as e:
            logger.warning("[WebFetch] 操作失败: %s", e)
            return None

    def _new_page(self):
        """创建新页面（强制新建）"""
        return self._get_page(force_recreate=True)

    def _clean_text(self, text):
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _get_cached_page(self, url: str) -> tuple:
        """获取缓存的页面，返回 (page, is_cached)"""
        now = time.time()
        # 清理过期缓存
        expired = [k for k, v in self._page_cache.items() if now - v['timestamp'] > self._cache_ttl]
        for k in expired:
            try:
                self._page_cache[k]['page'].quit()
            except Exception:
                pass
            del self._page_cache[k]
            logger.info(f"[WebFetch] 清理过期缓存: {k}")

        # 检查当前URL是否匹配缓存
        if url in self._page_cache:
            cache_entry = self._page_cache[url]
            try:
                # 验证页面是否仍然有效
                _ = cache_entry['page'].url
                logger.info(f"[WebFetch] 使用缓存页面: {url}")
                return cache_entry['page'], True
            except Exception:
                # 页面失效，删除缓存
                del self._page_cache[url]

        return None, False

    def _cache_page(self, url: str, page):
        """缓存页面"""
        self._page_cache[url] = {
            'timestamp': time.time(),
            'page': page,
            'url': url
        }
        logger.info(f"[WebFetch] 缓存页面: {url}")

    def _cmd_read(self, url: str = None, action: str = "open", keyword: str = None, wait_time: int = 3) -> dict:
        """
        统一的页面阅读命令

        Args:
            url: 页面URL（action="open"时必填）
            action: 操作类型（默认 "open"）
                    "open" - 打开URL，返回摘要（会缓存页面）
                    "scroll" - 向下滚动，返回新视口内容（复用缓存）
                    "find" - 搜索关键词并定位（复用缓存）
                    "structure" - 获取页面目录结构（复用缓存）
            keyword: 搜索关键词（action="find"时必填）
            wait_time: 等待秒数（默认 3）

        Returns:
            {"success": bool, "text": str, "url": str}
        """
        try:
            if wait_time is None:
                wait_time = 3
            elif wait_time > self.MAX_WAIT_TIME:
                logger.warning(f"[WebFetch] wait_time={wait_time}s 过大，已限制为 {self.MAX_WAIT_TIME}s")
                wait_time = self.MAX_WAIT_TIME
            elif wait_time < 0:
                wait_time = 0

            if action == "open":
                if not url:
                    return {"success": False, "error": "open 操作需要提供 url"}
                url = url.strip().strip('`"\'')
                if not url.startswith('http'):
                    return {"success": False, "error": f"无效URL: {url}"}

                cached_page, is_cached = self._get_cached_page(url)
                if is_cached:
                    page = cached_page
                    self._page = page
                    self._current_url = url
                else:
                    browser_path = self._get_working_browser_path()
                    if not browser_path:
                        return {
                            "success": False,
                            "error": "浏览器不可用，请使用 fetch 命令（HTTP 模式）获取页面内容",
                            "hint": "提示：fetch 命令会以 HTTP 方式获取页面文本（无 JavaScript/交互支持）",
                            "url": url,
                        }
                    try:
                        page = self._get_page()
                        if page is None:
                            logger.warning("[WebFetch] 浏览器页面获取失败")
                            return {
                                "success": False,
                                "error": "浏览器启动失败，请使用 fetch 命令（HTTP 模式）获取页面内容",
                                "hint": "提示：fetch 命令会以 HTTP 方式获取页面文本（无 JavaScript/交互支持）",
                                "url": url,
                            }
                        page.get(url, timeout=30)
                        page.wait(wait_time)
                        self._current_url = url
                        self._cache_page(url, page)
                    except Exception as e:
                        logger.error(f"[WebFetch] 浏览器操作失败: {e}")
                        self._close_page()
                        return {
                            "success": False,
                            "error": f"浏览器操作失败: {e}，请使用 fetch 命令（HTTP 模式）",
                            "hint": "提示：fetch 命令会以 HTTP 方式获取页面文本（无 JavaScript/交互支持）",
                            "url": url,
                        }

                # 返回摘要
                body = page.ele('tag:body', timeout=2)
                paragraphs = []
                if body:
                    for p in page.eles('tag:p'):
                        txt = p.text.strip()
                        if txt and len(txt) > 20:
                            paragraphs.append(txt)
                        if len(paragraphs) >= 3:
                            break
                text = '\n\n'.join(paragraphs)[:1500]
                return {
                    "success": True,
                    "url": page.url,
                    "title": page.title,
                    "text": text,
                    "hint": "如需继续阅读: read(action='scroll') 或 read(action='find', keyword='xxx')"
                }

            elif action == "scroll":
                page = self._page
                if not page or not self._current_url:
                    if not self._get_working_browser_path():
                        return self._browser_unavailable_error()
                    return {"success": False, "error": "没有可滚动的页面，请先执行 read(action='open', url='...')"}

                page.scroll.down(600)
                page.wait(1)

                # 获取视口内容
                js_code = """
                return Array.from(document.querySelectorAll('p, h1, h2, h3, li'))
                    .filter(el => {
                        const rect = el.getBoundingClientRect();
                        return rect.top >= 0 && rect.bottom <= window.innerHeight && el.innerText.length > 5;
                    })
                    .map(el => el.innerText).join('\\n');
                """
                visible_text = page.run_js(js_code)
                return {
                    "success": True,
                    "url": page.url,
                    "text": visible_text[:2000],
                    "hint": "继续: read(action='scroll') 或 read(action='find', keyword='xxx')"
                }

            elif action == "find":
                if not keyword:
                    return {"success": False, "error": "find 操作需要提供 keyword"}

                page = self._page
                if not page or not self._current_url:
                    if not self._get_working_browser_path():
                        return self._browser_unavailable_error()
                    return {"success": False, "error": "没有可搜索的页面，请先执行 read(action='open', url='...')"}

                element = page.ele(f'text:{keyword}', timeout=3)
                if element:
                    element.scroll.to_see()
                    parent = element.parent()
                    context = parent.text if parent else element.text
                    return {
                        "success": True,
                        "found": True,
                        "url": page.url,
                        "text": context[:1000] if context else "",
                        "hint": "继续: read(action='scroll') 或 read(action='find', keyword='xxx')"
                    }
                return {
                    "success": True,
                    "found": False,
                    "url": page.url,
                    "text": "",
                    "hint": "未找到关键词，尝试其他词或使用 read(action='structure') 查看目录"
                }

            elif action == "structure":
                page = self._page
                if not page or not self._current_url:
                    if not self._get_working_browser_path():
                        return self._browser_unavailable_error()
                    return {"success": False, "error": "没有可分析的页面，请先执行 read(action='open', url='...')"}

                structure = self._extract_page_structure(page)
                headings_text = '\n'.join([f"[{h['level']}] {h['text']}" for h in structure.get('structure', {}).get('headings', [])[:15]])
                return {
                    "success": True,
                    "url": page.url,
                    "text": headings_text,
                    "hint": "使用 read(action='find', keyword='章节标题') 跳转到对应位置"
                }

            else:
                return {"success": False, "error": f"未知操作: {action}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_fetch(self, url: str = None, action: str = "open", page: int = None, keyword: str = None, wait_time: int = 3) -> dict:
        """fetch 命令 - HTTP 模式获取页面内容"""
        if action == "open":
            if not url:
                return {"success": False, "error": "fetch 需要提供 url"}
            url = url.strip().strip('`"\'')
            if not url.startswith('http'):
                return {"success": False, "error": f"无效URL: {url}"}
            return self._http_fetch_text(url)

        cache = self._http_content_cache
        if not cache:
            return {"success": False, "error": "没有缓存的 HTTP 内容，请先使用 fetch(url='...') 获取页面"}

        total_pages = len(cache["chunks"])

        if action == "next":
            next_page = cache["current_page"] + 1
            if next_page >= total_pages:
                return {"success": False, "error": "已是最后一页", "current_page": cache["current_page"], "total_pages": total_pages}
            cache["current_page"] = next_page
            return {
                "success": True,
                "url": cache["url"],
                "title": cache["title"],
                "text": cache["chunks"][next_page],
                "mode": "http",
                "current_page": next_page,
                "total_pages": total_pages,
                "hint": f"共 {total_pages} 页，当前第 {next_page + 1} 页 | fetch(action='next') 下一页 | fetch(action='prev') 上一页 | fetch(action='goto', page=N) 跳转",
            }

        elif action == "prev":
            prev_page = cache["current_page"] - 1
            if prev_page < 0:
                return {"success": False, "error": "已是第一页", "current_page": cache["current_page"], "total_pages": total_pages}
            cache["current_page"] = prev_page
            return {
                "success": True,
                "url": cache["url"],
                "title": cache["title"],
                "text": cache["chunks"][prev_page],
                "mode": "http",
                "current_page": prev_page,
                "total_pages": total_pages,
                "hint": f"共 {total_pages} 页，当前第 {prev_page + 1} 页 | fetch(action='next') 下一页 | fetch(action='prev') 上一页 | fetch(action='goto', page=N) 跳转",
            }

        elif action == "goto":
            if page is None:
                return {"success": False, "error": "goto 操作需要提供 page 参数"}
            target = page - 1
            if target < 0 or target >= total_pages:
                return {"success": False, "error": f"页码超出范围 (1-{total_pages})", "current_page": cache["current_page"], "total_pages": total_pages}
            cache["current_page"] = target
            return {
                "success": True,
                "url": cache["url"],
                "title": cache["title"],
                "text": cache["chunks"][target],
                "mode": "http",
                "current_page": target,
                "total_pages": total_pages,
                "hint": f"共 {total_pages} 页，当前第 {page} 页 | fetch(action='next') 下一页 | fetch(action='prev') 上一页 | fetch(action='goto', page=N) 跳转",
            }

        elif action == "search":
            if not keyword:
                return {"success": False, "error": "search 操作需要提供 keyword 参数"}
            matches = []
            for i, chunk in enumerate(cache["chunks"]):
                if keyword.lower() in chunk.lower():
                    pos = chunk.lower().find(keyword.lower())
                    context_start = max(0, pos - 50)
                    context_end = min(len(chunk), pos + len(keyword) + 50)
                    matches.append({
                        "page": i + 1,
                        "preview": "..." + chunk[context_start:context_end] + "...",
                    })
            if not matches:
                return {
                    "success": True,
                    "found": False,
                    "keyword": keyword,
                    "total_pages": total_pages,
                    "hint": f"未找到 '{keyword}'，共 {total_pages} 页 | fetch(action='next') 下一页 | fetch(action='goto', page=N) 跳转",
                }
            return {
                "success": True,
                "found": True,
                "keyword": keyword,
                "match_count": len(matches),
                "matches": matches,
                "hint": f"在 {len(matches)} 页中找到 '{keyword}' | 使用 fetch(action='goto', page=N) 跳转到对应页面",
            }

        else:
            return {"success": False, "error": f"未知操作: {action}，支持: open/next/prev/goto/search"}

    def _cmd_fetch_many(self, urls: list, wait_time: int = 3, max_workers: int = 3) -> dict:
        """
        并行抓取多个 URL（单浏览器多标签模式）

        Args:
            urls: URL 列表（必填）
            wait_time: 每个页面等待秒数（默认 3）
            max_workers: 并行线程数（默认 3）

        Returns:
            {"success": bool, "results": [...], "success_count": int, "fail_count": int}
        """
        try:
            if isinstance(urls, str):
                try:
                    urls = json.loads(urls)
                except json.JSONDecodeError:
                    return {"success": False, "error": "urls 参数格式错误，需要列表"}

            if not urls:
                return {"success": False, "error": "缺少必填参数 'urls'"}

            if max_workers > _MAX_PARALLEL_TABS:
                max_workers = _MAX_PARALLEL_TABS
                logger.warning("[WebFetch] max_workers 超过限制，已限制为 %d", max_workers)

            if wait_time is None:
                wait_time = 3
            elif wait_time > self.MAX_WAIT_TIME:
                logger.warning(f"[WebFetch] wait_time={wait_time}s 过大，已限制为 {self.MAX_WAIT_TIME}s")
                wait_time = self.MAX_WAIT_TIME
            elif wait_time < 0:
                wait_time = 0

            logger.info("[WebFetch] 启动单浏览器多标签抓取，共 %d 个 URL", len(urls))
            browser = None
            try:
                browser = self._get_page()
                results = []

                def _task(url):
                    tab = None
                    try:
                        tab = browser.new_tab(url)
                        tab.wait(wait_time)
                        title = tab.title
                        text = self._clean_text(tab.ele('tag:body').text)
                        return {"url": url, "title": title, "text": text[:5000], "success": True}
                    except Exception as e:
                        return {"url": url, "success": False, "error": str(e)}
                    finally:
                        if tab:
                            try:
                                tab.close()
                            except Exception:
                                pass

                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    results = list(executor.map(_task, urls))
            finally:
                if browser:
                    try:
                        browser.quit()
                    except Exception:
                        pass

            success_count = sum(1 for r in results if r['success'])
            fail_count = len(results) - success_count
            logger.info("[WebFetch] 抓取完成，成功 %d / %d", success_count, len(results))

            return {
                "success": True,
                "results": results,
                "success_count": success_count,
                "fail_count": fail_count,
                "total": len(results)
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_insight(self, url: str = None, mode: str = "auto", query: str = None) -> Dict[str, Any]:
        if not url:
            url = self._current_url
            logger.info(f"[WebFetch:insight] 未提供url，使用当前页面: {url}")

        if not url:
            return {"success": False, "error": "缺少 url 参数，且没有当前页面。请先使用 fetch(url='...') 打开一个页面。"}

        page = None
        try:
            if not self._get_working_browser_path():
                return {
                    **self._browser_unavailable_error(),
                    "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
                }

            if url == self._current_url and self._page and self._is_page_alive():
                page = self._page
                logger.info(f"[WebFetch:insight] 复用当前页面: {url}")
            else:
                page = self._new_page()
                page.get(url, timeout=30)
                page.wait(3)

            if mode == "auto":
                mode = "structure" if not query else "search"

            result = {
                "url": url,
                "title": page.title,
            }

            if mode == "structure":
                result.update(self._extract_page_structure(page))
            elif mode == "search":
                result.update(self._stream_search_page(page, query))
            else:
                result.update(self._page_summary(page))

            return {"success": True, "type": "insight", **result}
        except Exception as e:
            error_str = str(e)
            logger.error(f"[WebFetch:insight] 浏览器操作失败: {error_str}")
            return {"success": False, "error": f"Insight failed: {error_str}"}
        finally:
            if page and page != self._page:
                try:
                    page.quit()
                except Exception:
                    pass

    def _extract_page_structure(self, page) -> Dict[str, Any]:
        """提取页面结构：heading树 + 链接统计（带ID和xpath便于Agent跳转）"""
        headings = []
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for i, el in enumerate(page.eles(f'tag:{tag}')):
                text = el.text.strip()[:100]
                if text:
                    headings.append({
                        "id": len(headings),
                        "level": tag,
                        "text": text,
                        "xpath": el.xpath,
                        "index": i
                    })
            if len(headings) >= 30:
                break

        links = page.eles('tag:a')
        link_stats = {
            "total": len(links),
            "internal": 0,
            "external": 0,
        }
        current_domain = page.url.split('/')[2] if '/' in page.url else ''
        for link in links[:50]:
            try:
                href = link.attr('href') or ''
                if href.startswith('http'):
                    if current_domain in href:
                        link_stats["internal"] += 1
                    else:
                        link_stats["external"] += 1
                elif href.startswith('/') or href.startswith('#'):
                    link_stats["internal"] += 1
            except Exception:
                pass

        imgs = page.eles('tag:img')
        return {
            "structure": {
                "headings": headings,
                "links": link_stats,
                "images_count": len(imgs),
            }
        }

    def _stream_search_page(self, page, query: str) -> Dict[str, Any]:
        """流式关键词搜索：只返回前10个锚点"""
        if not query:
            return {"success": False, "error": "search 模式需要提供 query 参数"}

        hits = []
        target = query.lower()
        body = page.ele('tag:body')
        if not body:
            return {"search_hints": hits, "query": query}

        text = body.text
        lines = text.split('\n')

        for i, line in enumerate(lines):
            if target in line.lower():
                hits.append({
                    "line": i + 1,
                    "context": line.strip()[:150]
                })
            if len(hits) >= 10:
                break

        return {"search_hints": hits, "query": query}

    def _page_summary(self, page) -> Dict[str, Any]:
        """快速摘要：标题 + 前几段文本"""
        body = page.ele('tag:body')
        paragraphs = []
        if body:
            for p in page.eles('tag:p'):
                txt = p.text.strip()
                if txt and len(txt) > 20:
                    paragraphs.append(txt)
                if len(paragraphs) >= 5:
                    break

        return {
            "summary": {
                "paragraphs": paragraphs[:5],
                "hint": "内容较长，建议使用 search 模式定位具体段落"
            }
        }

    def _cmd_get_viewport_text(self) -> dict:
        """
        仅获取当前视口（屏幕显示区域）内的可见文本
        实现"分页"阅读，避免一次性返回全文
        """
        try:
            if not self._get_working_browser_path():
                return {
                    **self._browser_unavailable_error(),
                    "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
                }
            page = self._get_page()
            # 执行 JS 获取仅在可视区域内的文本
            js_code = """
            return Array.from(document.querySelectorAll('p, h1, h2, h3, li, span, div'))
                .filter(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.top >= 0 && rect.bottom <= window.innerHeight && el.innerText.length > 5;
                })
                .map(el => el.innerText).join('\\n');
            """
            visible_text = page.run_js(js_code)
            # 获取滚动位置（使用 JS）
            scroll_y = page.run_js("return window.scrollY || window.pageYOffset;")

            return {
                "success": True,
                "current_url": page.url,
                "title": page.title,
                "text": visible_text[:2000],  # 限制单次返回长度
                "scroll_y": scroll_y,
                "hint": "如需查看更多内容，请使用 read(action='scroll') 或 control(action='scroll_down') 滚动"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_find_in_page(self, keyword: str) -> dict:
        """
        在页面中搜索关键词，并自动滚动到该位置，返回上下文
        实现"关键字定位"而非全文抓取
        
        策略：
        1. 优先使用 DrissionPage 的 text 定位（最可靠）
        2. 如果失败，回退到 JS 实现
        """
        try:
            if not self._get_working_browser_path():
                return {
                    **self._browser_unavailable_error(),
                    "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
                }
            page = self._get_page()
            
            # 方法1：优先使用 DrissionPage 的 text 定位
            try:
                # 使用 text 选择器查找元素
                element = page.ele(f'text:{keyword}', timeout=3)
                
                if element:
                    # 滚动到元素（使用 JS 避免 DrissionPage 的 scroll 问题）
                    page.run_js("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
                    page.wait(0.5)  # 等待滚动完成
                    
                    # 获取元素信息
                    try:
                        tag = element.tag
                        text = element.text
                        elem_id = element.attr('id')
                        elem_class = element.attr('class')
                    except:
                        tag = 'unknown'
                        text = ''
                        elem_id = ''
                        elem_class = ''
                    
                    # 获取上下文（父元素文本）
                    try:
                        parent = element.parent()
                        context_text = parent.text if parent else text
                    except:
                        context_text = text
                    
                    return {
                        "success": True,
                        "found": True,
                        "keyword": keyword,
                        "context": (context_text or text)[:800],
                        "element_info": {
                            "tag": tag,
                            "id": elem_id,
                            "class": elem_class,
                            "text_preview": text[:100] if text else ""
                        },
                        "current_url": page.url,
                        "note": f"已自动滚动到关键词 '{keyword}' 所在位置"
                    }
            except Exception as dp_error:
                # DrissionPage 方法失败，记录日志并回退到 JS
                logger.debug(f"[WebFetch] DrissionPage 搜索失败，回退到 JS: {dp_error}")
            
            # 方法2：使用 JS 实现（备用方案）
            js_code = f"""
                (function() {{
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );
                    const keyword = '{keyword.replace("'", "\\'")}';
                    let node;
                    while (node = walker.nextNode()) {{
                        if (node.textContent.includes(keyword)) {{
                            const element = node.parentElement;
                            if (element) {{
                                element.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                                return {{
                                    found: true,
                                    text: node.textContent.trim(),
                                    tagName: element.tagName,
                                    id: element.id,
                                    className: element.className
                                }};
                            }}
                        }}
                    }}
                    return {{found: false}};
                }})();
            """
            
            result = page.run_js(js_code)
            
            if result and result.get('found'):
                # 获取更广泛的上下文
                context_js = f"""
                    (function() {{
                        const keyword = '{keyword.replace("'", "\\'")}';
                        const elements = document.querySelectorAll('p, div, span, h1, h2, h3, h4, h5, h6, li, td');
                        for (let el of elements) {{
                            if (el.textContent.includes(keyword)) {{
                                return el.textContent.trim().substring(0, 800);
                            }}
                        }}
                        return '';
                    }})();
                """
                context_text = page.run_js(context_js) or result.get('text', '')
                
                return {
                    "success": True,
                    "found": True,
                    "keyword": keyword,
                    "context": context_text[:800] if isinstance(context_text, str) else result.get('text', '')[:800],
                    "element_info": result,
                    "current_url": page.url,
                    "note": f"已自动滚动到关键词 '{keyword}' 所在位置 (JS模式)"
                }
            
            return {
                "success": True,
                "found": False,
                "keyword": keyword,
                "error": "未找到相关关键词",
                "hint": "尝试使用不同的关键词，或使用 get_structure 查看页面目录"
            }
        except Exception as e:
            error_msg = str(e)
            # 处理特定错误
            if "位置及大小" in error_msg:
                return {
                    "success": False,
                    "error": "页面元素定位失败，可能是动态内容未完全加载",
                    "hint": "建议先等待页面加载完成，或使用 read(action='scroll') 滚动后再搜索"
                }
            return {"success": False, "error": f"搜索失败: {error_msg}"}

    def _cmd_close(self) -> dict:
        """关闭浏览器，释放资源"""
        self._close_page()
        return {"success": True, "message": "浏览器已关闭"}

    def _cmd_set_mode(self, headless: bool = True) -> dict:
        """
        设置浏览器模式（headless 或可视化）

        Args:
            headless: True=无头模式（后台运行），False=可视化模式（显示窗口，用于扫码）

        Returns:
            {"success": bool, "headless": bool, "message": str}
        """
        try:
            # 检查是否已经是目标模式
            if self._headless == headless:
                mode = "headless（后台）" if headless else "可视化（显示窗口）"
                return {
                    "success": True,
                    "headless": headless,
                    "message": f"已经是 {mode} 模式，无需切换",
                    "note": "如果浏览器已打开，需要关闭后重新打开才能生效"
                }
            
            # 检查是否有打开的页面需要关闭
            had_open_page = self._page is not None
            
            self._set_headless(headless)
            mode = "headless（后台）" if headless else "可视化（显示窗口）"
            
            try:
                page = self._get_page(force_recreate=True)
                browser_info = f" (端口: {self._browser_port})" if self._browser_port else ""
                message = f"✅ 已切换到 {mode} 模式，浏览器已启动{browser_info}"
            except Exception as e:
                message = f"✅ 已切换到 {mode} 模式，浏览器已关闭"
                message += f"\n⚠️ 预启动浏览器失败: {str(e)}"
                message += "\n下次使用命令时会自动以新模式启动浏览器"
            
            return {
                "success": True,
                "headless": headless,
                "message": message,
                "had_open_page": had_open_page
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_get_screenshot(self, full_page: bool = False, selector: str = None) -> dict:
        import concurrent.futures

        if not self._get_working_browser_path():
            return {
                **self._browser_unavailable_error(),
                "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
            }

        if not self._page or not self._current_url:
            return {
                "success": False,
                "error": "没有可截图的页面。请先使用 fetch(url='...') 或 open(url='...') 打开一个页面",
                "hint": "screenshot 需要先有打开的页面才能截取",
            }

        try:
            page = self._get_page()
        except Exception as e:
            return {"success": False, "error": f"浏览器不可用: {str(e)}"}

        try:
            page.set.window.size(1920, 1080)
        except Exception:
            pass

        try:
            from urllib.parse import urlparse
            parsed = urlparse(page.url)
            domain = parsed.netloc.replace(':', '_') if parsed.netloc else 'unknown'
            domain = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in domain)
            if not domain:
                domain = 'unknown'
        except Exception:
            domain = 'unknown'

        workspace_dir = os.path.join("workspace", "web_fetch_screenshots")
        os.makedirs(workspace_dir, exist_ok=True)
        timestamp = int(time.time())
        file_path = os.path.join(workspace_dir, f"{domain}_{timestamp}.png")

        captured_page = page
        captured_selector = selector
        captured_file_path = file_path

        def _do_screenshot():
            if captured_selector:
                try:
                    element = captured_page.ele(captured_selector, timeout=3)
                    if element:
                        element.get_screenshot(path=captured_file_path)
                        return {"success": True, "element": captured_selector}
                    else:
                        return {"success": False, "error": f"未找到元素: {captured_selector}"}
                except Exception as e:
                    return {"success": False, "error": f"截取元素失败: {str(e)}"}
            else:
                try:
                    captured_page.get_screenshot(path=captured_file_path, full_page=full_page)
                    element_desc = "full_page" if full_page else "viewport"
                    return {"success": True, "element": element_desc}
                except Exception as e:
                    return {"success": False, "error": f"截图失败: {str(e)}"}

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_screenshot)
                result = future.result(timeout=30)
                if result.get("success"):
                    with open(file_path, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    return {
                        "success": True,
                        "file_path": file_path,
                        "element": result.get("element"),
                        "base64_image": encoded_string,
                        "format": "png"
                    }
                return result
        except concurrent.futures.TimeoutError:
            logger.error("[WebFetch] 截图超时")
            self._close_page()
            return {"success": False, "error": "截图超时（30秒），浏览器已重置"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_find_qrcode(self) -> dict:
        import concurrent.futures

        if not self._get_working_browser_path():
            return {
                **self._browser_unavailable_error(),
                "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
            }

        if not self._page or not self._current_url:
            return {
                "success": False,
                "error": "没有可查找的页面。请先使用 fetch(url='...') 或 open(url='...') 打开一个页面",
                "hint": "find_qrcode 需要先有打开的页面才能查找",
            }

        try:
            page = self._get_page()
        except Exception as e:
            return {"success": False, "error": f"浏览器不可用: {str(e)}"}

        captured_page = page

        def _do_find():
            found_elements = []

            try:
                js_result = captured_page.run_js("""
                    return Array.from(document.querySelectorAll('img, canvas')).map(el => {
                        const rect = el.getBoundingClientRect();
                        return {
                            tag: el.tagName.toLowerCase(),
                            width: rect.width,
                            height: rect.height,
                            src: el.src || '',
                            alt: el.alt || '',
                            className: el.className || '',
                            id: el.id || '',
                            visible: rect.width > 0 && rect.height > 0 &&
                                    rect.top < window.innerHeight && rect.bottom > 0
                        };
                    }).filter(item => item.visible && item.width > 50 && item.height > 50);
                """)

                for item in js_result:
                    w, h = item['width'], item['height']
                    src, alt, cls, id = item['src'], item['alt'], item['className'], item['id']

                    is_qr = False
                    selector = None

                    qr_keywords = ['qr', 'qrcode', '二维码', '扫码', 'login']
                    text_to_check = f"{src} {alt} {cls} {id}".lower()
                    if any(kw in text_to_check for kw in qr_keywords):
                        is_qr = True
                        selector = f".{cls.split()[0]}" if cls else (f"#{id}" if id else f"img[src*='{src[:30]}']")

                    elif w > 100 and h > 100 and abs(w - h) < 30:
                        is_qr = True
                        selector = f"img[src*='{src[:30]}']" if src else f"{item['tag']}"

                    if is_qr and selector:
                        found_elements.append({
                            'selector': selector,
                            'tag': item['tag'],
                            'size': f"{int(w)}x{int(h)}",
                            'src': src[:80] if src else None,
                            'hint': alt if alt else '可能是二维码'
                        })

                        if len(found_elements) >= 5:
                            break

            except Exception as e:
                logger.debug(f"[WebFetch] JS 查询失败，回退到传统方式: {e}")
                try:
                    elements = captured_page.eles('css:.qrcode, #qrcode, img[src*="qr"], img[alt*="二维码"]')
                    for el in elements:
                        try:
                            if el.states.is_displayed:
                                rect = el.rect
                                if rect.get('width', 0) > 50 and rect.get('height', 0) > 50:
                                    found_elements.append({
                                        'selector': f".{el.attr('class')}" if el.attr('class') else f"#{el.attr('id')}",
                                        'tag': el.tag,
                                        'size': f"{rect.get('width')}x{rect.get('height')}"
                                    })
                        except Exception:
                            continue
                except Exception:
                    pass

            if not found_elements:
                return {
                    "success": True,
                    "found_count": 0,
                    "elements": [],
                    "hint": "未找到二维码，建议：1) 先截图查看页面内容 get_screenshot() 2) 或滚动页面查找 read(action='scroll') 3) 部分网站需先点击'扫码登录'按钮"
                }

            return {
                "success": True,
                "found_count": len(found_elements),
                "elements": found_elements[:5],
                "hint": "使用 get_screenshot(selector='选择器') 截取二维码"
            }

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_find)
                return future.result(timeout=10)
        except concurrent.futures.TimeoutError:
            logger.error("[WebFetch] 查找二维码超时，浏览器可能无响应")
            self._close_page()
            return {"success": False, "error": "查找二维码超时（10秒），浏览器已重置"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_control(self, action: str, selector: str = None, value: str = None, x: int = None, y: int = None, wait_after: float = 1.0) -> dict:
        """
        浏览器页面操控命令（点击、输入、滚动等）

        Args:
            action: 'click' | 'input' | 'scroll_up' | 'scroll_down' | 'move_to' | 'find_qrcode' | 'find_button' | 'execute_script'
            selector: CSS 选择器或 xpath（click/input 时必填）
            value: 输入值（input 时必填）或按钮关键词（find_button 时）
            x, y: 坐标（可选）
            wait_after: 动作后等待秒数（默认 1.0）

        Returns:
            {"success": bool, "current_url": str, "error": str, "buttons": list}
        """
        if action == 'find_qrcode':
            return self._cmd_find_qrcode()
        if action == 'find_button':
            return self._cmd_find_button(keyword=value)
        if action == 'execute_script':
            return self._cmd_execute_script(value, wait_after)
        if action == 'js_action':
            return self._cmd_js_action(selector, action=value, wait_after=wait_after)
        return self._control_action(action, selector, value, x, y, wait_after)

    def _cmd_js_action(self, selector: str = None, action: str = None, wait_after: float = 1.0) -> dict:
        """
        统一的 JS 操作命令（纯 JS 实现，最可靠）

        Args:
            selector: CSS 选择器、XPath 或元素文本
            action: 'click' | 'input' | 'scroll_to' | 'get_text' | 'get_attribute'
            value: input 时要输入的值，或 get_attribute 的属性名
            wait_after: 操作后等待秒数

        Returns:
            {"success": bool, "result": Any, "error": str}
        """
        if not selector:
            return {"success": False, "error": "需要提供 selector 参数"}
        if not action:
            return {"success": False, "error": "需要提供 action 参数"}

        try:
            page = self._get_page()

            if action == 'click':
                return self._js_click(page, selector, wait_after)
            elif action == 'input':
                if not value:
                    return {"success": False, "error": "input 操作需要提供 value 参数"}
                return self._js_input(page, selector, value, wait_after)
            elif action == 'scroll_to':
                return self._js_scroll_to(page, selector, wait_after)
            elif action == 'get_text':
                return self._js_get_text(page, selector)
            elif action == 'get_attribute':
                if not value:
                    return {"success": False, "error": "get_attribute 操作需要提供 value 参数（属性名）"}
                return self._js_get_attribute(page, selector, value)
            else:
                return {"success": False, "error": f"不支持的 action: {action}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _js_click(self, page, selector: str, wait_after: float) -> dict:
        """纯 JS 点击元素"""
        # 构建 JS 代码：查找元素并点击
        js_code = f"""
        (function() {{
            var selector = '{selector.replace("'", "\\'")}';
            var el = null;

            // 1. 尝试 CSS 选择器
            try {{
                el = document.querySelector(selector);
            }} catch(e) {{}}

            // 2. 如果是 XPath，尝试 XPath 查询
            if (!el && (selector.startsWith('//') || selector.startsWith('(//'))) {{
                try {{
                    var result = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    el = result.singleNodeValue;
                }} catch(e) {{}}
            }}

            // 3. 如果是纯文本，查找包含该文本的元素
            if (!el) {{
                var allElements = document.querySelectorAll('*');
                for (var i = 0; i < allElements.length; i++) {{
                    var text = (allElements[i].innerText || allElements[i].value || '').trim();
                    if (text.includes(selector)) {{
                        el = allElements[i];
                        break;
                    }}
                }}
            }}

            if (!el) {{
                return {{success: false, error: 'Element not found: ' + selector}};
            }}

            // 滚动到视口中心
            el.scrollIntoView({{behavior: 'smooth', block: 'center'}});

            // 点击元素中心（使用 elementFromPoint 确保点击到目标）
            var rect = el.getBoundingClientRect();
            var x = rect.left + rect.width / 2;
            var y = rect.top + rect.height / 2;
            var clickedEl = document.elementFromPoint(x, y);

            if (clickedEl && (clickedEl === el || el.contains(clickedEl))) {{
                clickedEl.click();
            }} else {{
                el.click();
            }}

            return {{success: true, tag: el.tagName, text: el.innerText || el.value || '', x: x, y: y}};
        }})();
        """
        try:
            result = page.run_js(js_code)
            if result is None:
                page.wait(0.5)
                return {"success": True, "hint": f"点击已执行，页面可能已跳转: {selector}"}
            if result.get('success'):
                page.wait(wait_after)
                return {"success": True, "result": result, "hint": f"已点击: {result.get('tag')} - {result.get('text', '')[:30]}"}
            else:
                return {"success": False, "error": result.get('error', '点击失败'), "selector": selector}
        except Exception as e:
            return {"success": False, "error": f"JS 点击异常: {e}", "selector": selector}

    def _js_input(self, page, selector: str, value: str, wait_after: float) -> dict:
        """纯 JS 输入文本"""
        js_code = f"""
        (function() {{
            var selector = '{selector.replace("'", "\\'")}';
            var value = `{value.replace("`", "\\`")}`;
            var el = null;

            // 查找元素（支持选择器和文本）
            try {{
                el = document.querySelector(selector);
            }} catch(e) {{}}

            if (!el && (selector.startsWith('//') || selector.startsWith('(//'))) {{
                try {{
                    var result = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    el = result.singleNodeValue;
                }} catch(e) {{}}
            }}

            if (!el) {{
                var allElements = document.querySelectorAll('input, textarea, [contenteditable]');
                for (var i = 0; i < allElements.length; i++) {{
                    var placeholder = allElements[i].placeholder || '';
                    var name = allElements[i].name || '';
                    if (placeholder.includes(selector) || name.includes(selector)) {{
                        el = allElements[i];
                        break;
                    }}
                }}
            }}

            if (!el) {{
                return {{success: false, error: 'Element not found: ' + selector}};
            }}

            el.scrollIntoView({{block: 'center'}});

            // 聚焦并清空后输入
            el.focus();
            el.value = '';
            el.dispatchEvent(new Event('input', {{bubbles: true}}));

            // 模拟键盘输入
            for (var i = 0; i < value.length; i++) {{
                var char = value[i];
                var event = new KeyboardEvent('keypress', {{
                    key: char,
                    char: char,
                    bubbles: true
                }});
                el.dispatchEvent(event);
            }}

            el.value = value;
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));

            return {{success: true, tag: el.tagName, value: el.value}};
        }})();
        """
        try:
            result = page.run_js(js_code)
            if result.get('success'):
                page.wait(wait_after)
                return {"success": True, "result": result, "hint": f"已输入: {result.get('value', '')[:20]}..."}
            else:
                return {"success": False, "error": result.get('error', '输入失败')}
        except Exception as e:
            return {"success": False, "error": f"JS 输入异常: {e}"}

    def _js_scroll_to(self, page, selector: str, wait_after: float) -> dict:
        """纯 JS 滚动到元素"""
        js_code = f"""
        (function() {{
            var selector = '{selector.replace("'", "\\'")}';
            var el = null;

            try {{ el = document.querySelector(selector); }} catch(e) {{}}
            if (!el && (selector.startsWith('//'))) {{
                try {{ var r = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null); el = r.singleNodeValue; }} catch(e) {{}}
            }}
            if (!el) {{
                var all = document.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {{
                    if ((all[i].innerText || '').trim().includes(selector)) {{ el = all[i]; break; }}
                }}
            }}

            if (!el) return {{success: false, error: 'Element not found'}};
            el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
            return {{success: true, tag: el.tagName}};
        }})();
        """
        try:
            result = page.run_js(js_code)
            if result.get('success'):
                page.wait(wait_after)
                return {"success": True, "result": result}
            else:
                return {"success": False, "error": result.get('error', '滚动失败')}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _js_get_text(self, page, selector: str) -> dict:
        """纯 JS 获取元素文本"""
        js_code = f"""
        (function() {{
            var selector = '{selector.replace("'", "\\'")}';
            var el = null;
            try {{ el = document.querySelector(selector); }} catch(e) {{}}
            if (!el && selector.startsWith('//')) {{
                try {{ var r = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null); el = r.singleNodeValue; }} catch(e) {{}}
            }}
            if (!el) {{
                var all = document.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {{
                    if ((all[i].innerText || '').trim().includes(selector)) {{ el = all[i]; break; }}
                }}
            }}
            if (!el) return {{success: false, error: 'Element not found'}};
            return {{success: true, text: el.innerText || el.value || '', tag: el.tagName}};
        }})();
        """
        try:
            result = page.run_js(js_code)
            if result.get('success'):
                return {"success": True, "text": result.get('text', ''), "tag": result.get('tag', '')}
            else:
                return {"success": False, "error": result.get('error', '获取文本失败')}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _js_get_attribute(self, page, selector: str, attr: str) -> dict:
        """纯 JS 获取元素属性"""
        js_code = f"""
        (function() {{
            var selector = '{selector.replace("'", "\\'")}';
            var attr = '{attr}';
            var el = null;
            try {{ el = document.querySelector(selector); }} catch(e) {{}}
            if (!el && selector.startsWith('//')) {{
                try {{ var r = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null); el = r.singleNodeValue; }} catch(e) {{}}
            }}
            if (!el) return {{success: false, error: 'Element not found'}};
            return {{success: true, value: el.getAttribute(attr) || el[attr], tag: el.tagName}};
        }})();
        """
        try:
            result = page.run_js(js_code)
            if result.get('success'):
                return {"success": True, "value": result.get('value', ''), "tag": result.get('tag', '')}
            else:
                return {"success": False, "error": result.get('error', '获取属性失败')}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_execute_script(self, script: str = None, wait_after: float = 1.0) -> dict:
        """
        执行 JavaScript 代码

        Args:
            script: JavaScript 代码
            wait_after: 执行后等待秒数

        Returns:
            {"success": bool, "result": Any, "type": str, "error": str}
        """
        if not script:
            return {"success": False, "error": "需要提供 script 参数"}

        try:
            page = self._get_page()
            result = page.run_js(script)
            
            result_type = type(result).__name__
            
            if result is None:
                return {
                    "success": True,
                    "result": None,
                    "type": "null",
                    "hint": f"JS 执行成功，返回 null（可能是未定义的属性或方法）",
                }
            elif isinstance(result, (dict, list)):
                import json
                try:
                    json_str = json.dumps(result, ensure_ascii=False, default=str)
                    if len(json_str) > 1000:
                        json_str = json_str[:1000] + "...(截断)"
                    return {
                        "success": True,
                        "result": result,
                        "type": result_type,
                        "preview": json_str,
                        "length": len(str(result)),
                        "hint": f"JS 已执行，返回 {result_type} 类型",
                    }
                except:
                    pass
            elif isinstance(result, str):
                preview = result if len(result) <= 200 else result[:200] + "..."
                return {
                    "success": True,
                    "result": result,
                    "type": "string",
                    "preview": preview,
                    "length": len(result),
                    "hint": f"JS 已执行，返回字符串（长度={len(result)}）",
                }
            else:
                return {
                    "success": True,
                    "result": result,
                    "type": result_type,
                    "value": str(result),
                    "hint": f"JS 已执行，返回 {result_type} 类型",
                }
                
        except Exception as e:
            error_msg = str(e)
            if "JavaScript" in error_msg or "syntax" in error_msg.lower():
                hint = "JS 语法错误，请检查脚本"
            elif "timeout" in error_msg.lower():
                hint = "JS 执行超时"
            else:
                hint = "JS 执行失败"
            
            return {"success": False, "error": error_msg, "hint": hint}

    def _cmd_find_button(self, keyword: str = None) -> dict:
        """
        智能查找页面中的按钮/可点击元素

        Args:
            keyword: 按钮文本关键词（如"扫码"、"登录"），不传则返回所有按钮

        Returns:
            {"success": bool, "buttons": [{"text": str, "selector": str, "tag": str}]}
        """
        import concurrent.futures

        if not self._get_working_browser_path():
            return {
                **self._browser_unavailable_error(),
                "hint": "该命令需要浏览器操控能力。",
            }

        if not self._page or not self._current_url:
            return {
                "success": False,
                "error": "没有可查找的页面。请先使用 fetch(url='...') 或 open(url='...') 打开一个页面",
            }

        try:
            page = self._get_page()
        except Exception as e:
            return {"success": False, "error": f"浏览器不可用: {str(e)}"}

        captured_page = page
        captured_keyword = keyword

        def _do_find():
            buttons = []

            js_code = """
                const results = [];
                const seen = new Set();

                // 遍历 Shadow DOM 的递归函数
                function traverseShadowRoot(root, depth) {
                    if (depth > 5) return;  // 防止无限递归
                    try {
                        // 获取 shadow root 中的所有可点击元素
                        const selectors = [
                            'button', 'a', 'input', '[role="button"]', '[role="tab"]',
                            '[role="menuitem"]', '[role="link"]', 'div', 'span', 'li', 'label'
                        ];
                        selectors.forEach(sel => {
                            try {
                                root.querySelectorAll(sel).forEach(el => processElement(el, depth));
                            } catch(e) {}
                        });
                        // 递归遍历子 shadow roots
                        try {
                            root.querySelectorAll('*').forEach(el => {
                                if (el.shadowRoot) {
                                    traverseShadowRoot(el.shadowRoot, depth + 1);
                                }
                            });
                        } catch(e) {}
                    } catch(e) {}
                }

                // 处理单个元素
                function processElement(el, depth) {
                    try {
                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) return;
                        if (rect.top >= window.innerHeight || rect.bottom <= 0) return;

                        const style = window.getComputedStyle(el);
                        const cursor = style.cursor;
                        const text = (el.innerText || el.value || el.alt || el.title || '').trim();
                        const tag = el.tagName.toLowerCase();

                        // 只要有文本内容或者是可点击的，就收集
                        if (!text && cursor !== 'pointer' && !el.onclick && !el.getAttribute('href') && !el.getAttribute('role')) return;

                        const elemId = el.id || '';
                        const cls = el.className || '';
                        const key = tag + '|' + cls + '|' + elemId + '|' + text.slice(0, 20);
                        if (seen.has(key)) return;
                        seen.add(key);

                        results.push({
                            tag: tag,
                            text: text.slice(0, 100),
                            id: elemId,
                            className: cls,
                            href: el.href || '',
                            cursor: cursor,
                            hasOnclick: !!el.onclick,
                            role: el.getAttribute('role') || '',
                            inShadowDOM: depth > 0
                        });
                    } catch(e) {}
                }

                // 1. 先处理主文档
                document.querySelectorAll('button, a, input, [role], div, span, li, label').forEach(el => processElement(el, 0));

                // 2. 遍历 Shadow DOM
                traverseShadowRoot(document, 0);

                // 3. 遍历 iframes
                document.querySelectorAll('iframe').forEach(iframe => {
                    try {
                        const iframeDoc = iframe.contentDocument || iframe.contentWindow?.document;
                        if (iframeDoc) {
                            iframeDoc.querySelectorAll('button, a, input, [role], div, span, li, label').forEach(el => {
                                processElement(el, 0);
                                // 如果 iframe 内有 shadow DOM，也遍历
                                if (el.shadowRoot) traverseShadowRoot(el.shadowRoot, 1);
                            });
                        }
                    } catch(e) {}  // 跨域 iframe 可能访问失败
                });

                return results;
            """

            try:
                elements = captured_page.run_js(js_code)
                keyword_lower = (captured_keyword or '').lower()

                # 如果有关键词，先深度搜索包含关键词的元素
                keyword_elements = []
                if keyword_lower:
                    keyword_elements = captured_page.run_js("""
                        const results = [];
                        const keyword = arguments[0];
                        if (!keyword) return results;

                        function deepSearch(root) {
                            try {
                                const allElements = root.querySelectorAll('*');
                                allElements.forEach(el => {
                                    try {
                                        const text = (el.innerText || el.value || '').trim();
                                        if (text && text.toLowerCase().includes(keyword.toLowerCase())) {
                                            const rect = el.getBoundingClientRect();
                                            if (rect.width > 0 && rect.height > 0 &&
                                                rect.top < window.innerHeight && rect.bottom > 0) {
                                                const style = window.getComputedStyle(el);
                                                const tag = el.tagName.toLowerCase();
                                                results.push({
                                                    tag: tag,
                                                    text: text.slice(0, 100),
                                                    id: el.id || '',
                                                    className: el.className || '',
                                                    href: el.href || '',
                                                    cursor: style.cursor,
                                                    hasOnclick: !!el.onclick,
                                                    role: el.getAttribute('role') || '',
                                                    inShadowDOM: false,
                                                    foundByText: true
                                                });
                                            }
                                        }
                                    } catch(e) {}
                                });
                            } catch(e) {}
                        }

                        deepSearch(document);
                        document.querySelectorAll('iframe').forEach(iframe => {
                            try {
                                const iframeDoc = iframe.contentDocument || iframe.contentWindow?.document;
                                if (iframeDoc) deepSearch(iframeDoc);
                            } catch(e) {}
                        });

                        return results;
                    """, keyword_lower)

                candidates = []
                seen = set()

                def add_candidate(el, found_by_text=False):
                    nonlocal candidates, seen
                    text = el.get('text', '')
                    cls = el.get('className', '')
                    elem_id = el.get('id', '')
                    tag = el.get('tag', 'button')
                    href = el.get('href', '')
                    key = f"{tag}|{cls}|{elem_id}|{text[:20]}"
                    if key in seen:
                        return
                    seen.add(key)

                    if elem_id:
                        selector = f"#{elem_id}"
                        selector_quality = 3
                    elif cls:
                        class_list = cls.split()
                        specific_classes = [c for c in class_list if not self._is_generic_class(c)]
                        if specific_classes:
                            selector = f".{specific_classes[0]}"
                            selector_quality = 2
                        else:
                            selector = f"{tag}.{class_list[0]}" if class_list else tag
                            selector_quality = 1
                    else:
                        safe_text = text[:20].replace("'", "\\'")
                        selector = f"//{tag}[contains(text(), '{safe_text}')]"
                        selector_quality = 0

                    score = selector_quality * 10
                    cursor = el.get('cursor', '')
                    has_onclick = el.get('hasOnclick', False)
                    role = el.get('role', '')

                    # cursor: pointer 或有 onclick 的元素优先级更高
                    if cursor == 'pointer' or has_onclick:
                        score += 30
                    # role 属性如果是 tab、button 等，加分
                    if role in ['tab', 'button', 'menuitem', 'link']:
                        score += 15
                    # Shadow DOM 内的元素优先级降低
                    if el.get('inShadowDOM', False):
                        score -= 10
                    # 通过文本搜索找到的，如果关键词完全匹配，加最高分
                    if found_by_text and keyword_lower:
                        text_lower = text.lower()
                        if keyword_lower == text_lower:
                            score += 200
                        elif keyword_lower in text_lower:
                            score += 100

                    candidates.append({
                        "text": text,
                        "selector": selector,
                        "tag": tag,
                        "score": score,
                        "selector_quality": selector_quality
                    })

                # 先添加通过关键词深度搜索找到的元素（优先）
                for el in keyword_elements:
                    add_candidate(el, found_by_text=True)

                # 再添加常规搜索到的元素
                for el in elements:
                    if el not in keyword_elements:
                        add_candidate(el)

                # 按分数排序
                candidates.sort(key=lambda x: x["score"], reverse=True)

                buttons = candidates[:10]

            except Exception as e:
                logger.debug(f"[WebFetch] 查找按钮失败: {e}")

            if not buttons and keyword:
                return {
                    "success": True,
                    "buttons": [],
                    "hint": f"未找到包含'{captured_keyword}'的按钮，建议先调用 find_button 查看所有可用按钮"
                }

            return {
                "success": True,
                "buttons": buttons,
                "hint": "使用 control(action='js_action', selector='选择器', value='click') 点击按钮"
            }

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_find)
                return future.result(timeout=10)
        except concurrent.futures.TimeoutError:
            return {"success": False, "error": "查找按钮超时", "buttons": []}
        except Exception as e:
            return {"success": False, "error": str(e), "buttons": []}

    def _control_action(self, action: str, selector: str = None, value: str = None, x: int = None, y: int = None, wait_after: float = 1.0) -> dict:
        """实际操控逻辑"""
        try:
            page = self._get_page()
            if action == 'click':
                if selector:
                    element = None
                    selector_tried = []

                    # 尝试多种选择器查找方式
                    # 1. 直接用 DrissionPage ele()
                    try:
                        page.wait.ele_displayed(selector, timeout=3)
                        element = page.ele(selector, timeout=5)
                        if element:
                            selector_tried.append(f"Drission ele()")
                    except Exception as e:
                        selector_tried.append(f"Drission ele(): {e}")

                    # 2. 如果是 XPath，尝试用 JS 查找
                    if not element and (selector.startswith('//') or selector.startswith('(//')):
                        try:
                            js_result = page.run_js(f"""
                                var result = document.evaluate('{selector}', document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                return result.singleNodeValue;
                            """)
                            if js_result:
                                # 包装成 Drission 元素
                                from DrissionPage import ChromiumPage
                                element = page.ele(f'xpath:{selector}', timeout=1)
                                selector_tried.append("JS + XPath")
                        except Exception as e:
                            selector_tried.append(f"JS XPath: {e}")

                    # 3. 如果还是找不到，尝试纯 JS 查找文本
                    if not element:
                        try:
                            # 搜索包含目标文本的元素
                            safe_selector = selector.replace("'", "\\'")
                            element = page.ele(f'text:{selector}', timeout=1)
                            selector_tried.append(f"text search")
                        except:
                            pass

                    if not element:
                        return {
                            "success": False,
                            "error": f"Element not found: {selector}",
                            "detail": f"尝试的方式: {' | '.join(selector_tried)}",
                            "hint": "推荐使用 js_action 命令（纯JS实现，更可靠）：control(action='js_action', selector='选择器', value='click')"
                        }
                    
                    # 检查元素是否可见
                    try:
                        if hasattr(element, 'states') and not element.states.is_displayed:
                            return {"success": False, "error": f"Element not visible: {selector}", "hint": "元素存在但不可见，可能需要滚动页面或等待加载"}
                    except:
                        pass  # 某些元素可能没有 states 属性
                    
                    # 滚动到元素
                    try:
                        page.run_js("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
                        page.wait(0.3)
                    except:
                        try:
                            element.scroll.to_see()
                        except:
                            pass

                    click_success = False
                    error_messages = []

                    def get_element_center(el):
                        return page.run_js("""
                            const rect = arguments[0].getBoundingClientRect();
                            return {
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2
                            };
                        """, el)

                    def check_occlusion(x, y, target_el):
                        return page.run_js("""
                            var el = document.elementFromPoint(arguments[0], arguments[1]);
                            return el === arguments[2] || arguments[2].contains(el);
                        """, x, y, target_el)

                    # 方式1：DrissionPage 原生点击
                    try:
                        element.click()
                        click_success = True
                        logger.info("[WebFetch] 原生点击成功")
                    except Exception as e:
                        error_messages.append(f"原生: {e}")
                        logger.debug(f"[WebFetch] 原生点击失败: {e}")

                    # 方式2：JS element.click()
                    if not click_success:
                        try:
                            page.run_js("arguments[0].click();", element)
                            click_success = True
                            logger.info("[WebFetch] JS click() 成功")
                        except Exception as e:
                            error_messages.append(f"JS click: {e}")
                            logger.debug(f"[WebFetch] JS click 失败: {e}")

                    # 方式3：dispatchEvent MouseEvent
                    if not click_success:
                        try:
                            page.run_js("""
                                var el = arguments[0];
                                var evt = new MouseEvent('click', {
                                    bubbles: true,
                                    cancelable: true,
                                    view: window
                                });
                                el.dispatchEvent(evt);
                            """, element)
                            click_success = True
                            logger.info("[WebFetch] dispatchEvent 成功")
                        except Exception as e:
                            error_messages.append(f"dispatch: {e}")
                            logger.debug(f"[WebFetch] dispatchEvent 失败: {e}")

                    # 方式4：坐标 + elementFromPoint
                    if not click_success:
                        try:
                            center = get_element_center(element)
                            x, y = center['x'], center['y']
                            page.run_js(f"""
                                var el = document.elementFromPoint({x}, {y});
                                if (el) el.click();
                            """)
                            click_success = True
                            logger.info(f"[WebFetch] 坐标点击成功: ({x}, {y})")
                        except Exception as e:
                            error_messages.append(f"坐标: {e}")
                            logger.debug(f"[WebFetch] 坐标点击失败: {e}")

                    # 方式5：检测遮挡 + 移除遮挡 + 点击
                    if not click_success:
                        try:
                            center = get_element_center(element)
                            x, y = center['x'], center['y']
                            blocking = page.run_js("""
                                var el = document.elementFromPoint(arguments[0], arguments[1]);
                                if (el && el !== arguments[2] && !arguments[2].contains(el)) {
                                    return {
                                        exists: true,
                                        tag: el.tagName,
                                        text: el.innerText || '',
                                        className: el.className
                                    };
                                }
                                return {exists: false};
                            """, x, y, element)
                            if blocking.get('exists'):
                                logger.info(f"[WebFetch] 检测到遮挡元素: {blocking['tag']} - {blocking.get('text', '')[:20]}")
                                # 尝试隐藏遮挡元素
                                page.run_js("""
                                    var el = document.elementFromPoint(arguments[0], arguments[1]);
                                    if (el && el !== arguments[2] && !arguments[2].contains(el)) {
                                        el.style.display = 'none';
                                    }
                                """, x, y, element)
                                page.wait(0.2)
                                # 再试点击
                                page.run_js("arguments[0].click();", element)
                                click_success = True
                                logger.info("[WebFetch] 移除遮挡后点击成功")
                        except Exception as e:
                            error_messages.append(f"去遮挡: {e}")
                            logger.debug(f"[WebFetch] 去除遮挡失败: {e}")

                    # 方式6：滚动 + 重新获取元素 + 点击
                    if not click_success:
                        try:
                            page.scroll.down(100)
                            page.wait(0.3)
                            page.run_js("arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});", element)
                            page.wait(0.3)
                            page.run_js("arguments[0].click();", element)
                            click_success = True
                            logger.info("[WebFetch] 滚动后再点击成功")
                        except Exception as e:
                            error_messages.append(f"滚动重试: {e}")
                            logger.debug(f"[WebFetch] 滚动重试失败: {e}")

                    if not click_success:
                        raise Exception(f"点击失败，已尝试: {' | '.join(error_messages)}")
                elif x is not None and y is not None:
                    page.click((x, y))
            elif action == 'input':
                if selector:
                    # 使用 JS 实现（更可靠，支持 CSS/XPath/文本）
                    result = self._js_input(page, selector, value, wait_after)
                    if not result.get('success'):
                        result['hint'] = "建议先调用 find_button 查找正确的选择器，或使用 execute_script 执行自定义 JS"
                    return result
            elif action == 'scroll_down':
                page.scroll.down(600)
            elif action == 'scroll_up':
                page.scroll.up(600)
            elif action == 'move_to':
                if selector:
                    page.ele(selector).move_to()
            elif action == 'wait_load':
                page.wait.load()
            elif action == 'wait_doc_loaded':
                page.wait.doc_loaded()
            else:
                return {"success": False, "error": f"Unknown action: {action}"}

            if wait_after > 0:
                page.wait(wait_after)

            return {"success": True, "current_url": page.url}
        except Exception as e:
            error_msg = str(e)
            if "not found" in error_msg.lower() or "none" in error_msg.lower():
                error_msg = f"Element not found: {selector or f'coords=({x},{y})'}"
            return {"success": False, "error": error_msg}

    def _cmd_get_element_tree(self) -> dict:
        """
        获取简化版 DOM 树（交互元素）

        Returns:
            {"elements": [{"tag": str, "text": str, "selector": str}]}
        """
        try:
            page = self._get_page()
            interactive_elements = []
            for tag in ['button', 'input', 'a', 'select']:
                for el in page.eles(f'tag:{tag}'):
                    if el.states.is_displayed:
                        interactive_elements.append({
                            "tag": el.tag,
                            "text": el.text[:50] if el.text else "",
                            "selector": el.xpath
                        })
            return {"elements": interactive_elements}
        except Exception as e:
            return {"success": False, "error": str(e), "elements": []}

    def _cmd_save_cookies(self, path: str = "cookies.json") -> dict:
        """保存当前会话 Cookie 到文件"""
        try:
            page = self._get_page()
            cookies = page.cookies(as_dict=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(cookies, f)
            return {"success": True, "path": path, "count": len(cookies)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_load_cookies(self, path: str = "cookies.json") -> dict:
        """从文件加载 Cookie 到当前会话"""
        try:
            if not os.path.exists(path):
                return {"success": False, "error": f"Cookie 文件不存在: {path}"}
            page = self._get_page()
            with open(path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            page.set.cookies(cookies)
            return {"success": True, "count": len(cookies)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_help(self, topic: str = "") -> dict:
        """查询组件使用帮助"""
        commands = self.get_commands()
        return {
            "name": self.cell_name,
            "description": "网页抓取工具 - 统一使用 read 命令进行页面阅读",
            "available_commands": list(commands.keys()),
            "usage": {
                "read": {
                    "description": "统一的页面阅读命令（推荐）",
                    "args": {
                        "url": "页面URL（action='open'时必填）",
                        "action": "操作类型: open/scroll/find/structure",
                        "keyword": "搜索关键词（action='find'时必填）",
                        "wait_time": "等待秒数"
                    },
                    "examples": [
                        '{"url": "https://example.com", "action": "open"} - 打开页面',
                        '{"action": "scroll"} - 向下滚动',
                        '{"action": "find", "keyword": "CRC16"} - 搜索关键词',
                        '{"action": "structure"} - 获取页面目录'
                    ]
                }
            }
        }

    def on_load(self):
        super().on_load()