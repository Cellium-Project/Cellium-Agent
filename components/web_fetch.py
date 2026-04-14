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

    def __init__(self):
        super().__init__()
        self._page = None
        self._browser = None  # 保存浏览器对象（CDP 模式）
        self._headless = True  # 默认 headless 模式
        self._current_url = None  # 当前缓存的URL
        self._working_browser_path: Optional[str] = None
        self._working_browser_name: Optional[str] = None
        self._browser_probe_failed = False

    @property
    def cell_name(self) -> str:
        return "web_fetch"

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
        options = ChromiumOptions()
        resolved_browser_path = browser_path or self._working_browser_path or find_browser_path()
        if resolved_browser_path:
            options.set_browser_path(resolved_browser_path)
        options.set_argument('--disable-gpu')
        options.set_argument('--no-sandbox')
        options.set_argument('--disable-blink-features=AutomationControlled')
        options.set_argument('--disable-dev-shm-usage')
        options.set_argument('--disable-extensions')
        options.set_argument('--profile-directory=Default')
        options.set_argument('--disable-plugins-discovery')
        options.set_argument('--disable-infobars')
        options.set_argument('--disable-popup-blocking')
        options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

        is_headless = force_headless if force_headless is not None else self._headless
        options.headless(is_headless)

        if is_headless:
            options.no_imgs(True)
            options.set_argument('--blink-settings=imagesEnabled=false')
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
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                response = client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                })
                response.raise_for_status()
                html = response.text

            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, flags=re.IGNORECASE | re.DOTALL)
            title = self._clean_text(title_match.group(1)) if title_match else ""

            body_match = re.search(r'<body[^>]*>(.*?)</body>', html, flags=re.IGNORECASE | re.DOTALL)
            body_html = body_match.group(1) if body_match else html
            text = self._clean_text(body_html)[:2000]

            return {
                "success": True,
                "url": str(response.url),
                "title": title,
                "text": text,
                "mode": "http_fallback",
                "hint": "当前已降级为 HTTP 模式，scroll/find/structure/screenshot 等浏览器交互能力可能不可用",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _set_headless(self, headless: bool):
        """设置 headless 模式（需要重启浏览器生效）"""
        if self._headless != headless:
            self._headless = headless
            # 关闭现有浏览器，下次会重新创建
            if self._page:
                self._close_page()
            logger.info(f"[WebFetch] headless 模式已设置为: {headless}")

    def _get_page(self):
        if self._page is None or not self._is_page_alive():
            if self._page:
                try:
                    self._page.quit()
                except Exception:
                    pass
                self._page = None
            browser_path = self._get_working_browser_path()
            if not browser_path:
                raise RuntimeError("没有可用的浏览器后端")
            try:
                from DrissionPage import Chromium
                # 使用 _build_options 获取完整配置，确保 headless 设置一致
                co = self._build_options(browser_path=browser_path)
                self._browser = Chromium(addr_or_opts=co)
                # 确保浏览器创建成功
                if self._browser is None:
                    raise RuntimeError("Chromium 浏览器创建失败")
                # 获取最新标签页
                self._page = self._browser.latest_tab
                if self._page is None:
                    # 尝试创建新标签页
                    self._page = self._browser.new_tab()
                    if self._page is None:
                        raise RuntimeError("无法获取浏览器标签页")
                logger.info("[WebFetch] 浏览器页面创建成功")
            except Exception as e:
                logger.error(f"[WebFetch] 浏览器创建失败: {e}")
                self._working_browser_path = None
                self._working_browser_name = None
                self._browser_probe_failed = False
                # 清理资源
                if self._browser:
                    try:
                        self._browser.quit()
                    except:
                        pass
                    self._browser = None
                self._page = None
                raise RuntimeError(f"浏览器初始化失败: {e}")
        return self._page

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

    def _close_page(self):
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

    def _new_page(self):
        """创建新页面（强制新建）"""
        self._close_page()
        browser_path = self._get_working_browser_path()
        if not browser_path:
            raise RuntimeError("没有可用的浏览器后端")
        from DrissionPage import Chromium, ChromiumOptions
        co = ChromiumOptions()
        co.set_browser_path(browser_path)
        if self._headless:
            co.set_argument('--headless')
        self._browser = Chromium(addr_or_opts=co)
        self._page = self._browser.latest_tab
        return self._page

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
                        return self._http_fetch_text(url)
                    try:
                        page = self._get_page()
                        if page is None:
                            logger.warning("[WebFetch] 浏览器页面获取失败，降级到 HTTP 模式")
                            return self._http_fetch_text(url)
                        page.get(url, timeout=30)
                        page.wait(wait_time)
                        self._current_url = url
                        self._cache_page(url, page)
                    except Exception as e:
                        logger.error(f"[WebFetch] 浏览器操作失败: {e}")
                        # 清理失败的浏览器状态
                        self._close_page()
                        return self._http_fetch_text(url)

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

    def _cmd_fetch(self, url: str, wait_time: int = 3) -> dict:
        """fetch 命令兼容层，实际调用 read"""
        return self._cmd_read(url=url, action="open", wait_time=wait_time)

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

    def _cmd_insight(self, url: str, mode: str = "auto", query: str = None) -> Dict[str, Any]:
        """
        理解层抓取：返回结构化摘要而非全文

        Args:
            url: 页面URL（必填）
            mode: "structure" | "search" | "summary" | "auto"（默认auto）
                  structure - 提取heading树+链接统计
                  search    - 关键词定位锚点
                  summary   - 快速摘要（标题+前几段）
                  auto      - 根据query自动选择
        Returns:
            结构化分析结果，上限约2KB
        """
        if not url:
            return {"success": False, "error": "缺少必填参数 'url'"}

        page = None
        try:
            if not self._get_working_browser_path():
                return {
                    **self._browser_unavailable_error(),
                    "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
                }
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
            return {"success": False, "error": f"Insight failed: {str(e)}"}
        finally:
            if page:
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
            
            message = f"已切换到 {mode} 模式"
            if had_open_page:
                message += "，已关闭当前页面以便下次使用新模式打开"
            else:
                message += "，下次打开页面时生效"
            
            return {
                "success": True,
                "headless": headless,
                "message": message,
                "had_open_page": had_open_page
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_get_screenshot(self, full_page: bool = False, selector: str = None) -> dict:
        """
        获取当前页面截图

        Args:
            full_page: 是否截取整页（默认 False）
            selector: CSS 选择器或 xpath，指定则只截取该元素

        Returns:
            {"success": bool, "file_path": str, "base64_image": str, "format": str}
        """
        import concurrent.futures

        def _do_screenshot():
            if not self._get_working_browser_path():
                return {
                    **self._browser_unavailable_error(),
                    "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
                }
            page = self._get_page()

            # 固定保存路径：workspace/web_fetch_screenshots/域名_时间戳.png
            try:
                from urllib.parse import urlparse
                parsed = urlparse(page.url)
                domain = parsed.netloc.replace(':', '_') if parsed.netloc else 'unknown'
                # 清理域名中的特殊字符
                domain = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in domain)
                if not domain:
                    domain = 'unknown'
            except Exception:
                domain = 'unknown'

            workspace_dir = os.path.join("workspace", "web_fetch_screenshots")
            os.makedirs(workspace_dir, exist_ok=True)
            timestamp = int(time.time())
            file_path = os.path.join(workspace_dir, f"{domain}_{timestamp}.png")

            # 如果指定了 selector，截取特定元素
            if selector:
                try:
                    element = page.ele(selector, timeout=3)
                    if element:
                        element.get_screenshot(path=file_path)
                        element_desc = selector
                    else:
                        return {"success": False, "error": f"未找到元素: {selector}"}
                except Exception as e:
                    return {"success": False, "error": f"截取元素失败: {str(e)}"}
            else:
                page.get_screenshot(path=file_path, full_page=full_page)
                element_desc = "full_page" if full_page else "viewport"

            # 同时返回 base64
            with open(file_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

            return {
                "success": True,
                "file_path": file_path,
                "element": element_desc if selector else None,
                "base64_image": encoded_string,
                "format": "png"
            }

        # 使用线程池执行截图，防止浏览器卡死导致主线程阻塞
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_screenshot)
                return future.result(timeout=30)  # 30秒超时
        except concurrent.futures.TimeoutError:
            logger.error("[WebFetch] 截图超时，浏览器可能无响应")
            # 尝试关闭浏览器，下次会重新创建
            self._close_page()
            return {"success": False, "error": "截图超时（30秒），浏览器已重置"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_find_qrcode(self) -> dict:
        """
        智能查找页面中的二维码元素
        返回可能包含二维码的元素选择器列表
        """
        import concurrent.futures

        def _do_find():
            if not self._get_working_browser_path():
                return {
                    **self._browser_unavailable_error(),
                    "hint": "该命令需要浏览器操控能力。若确认本机没有可用浏览器，可在最后一步提示下载内置 runtime。",
                }
            page = self._get_page()
            found_elements = []

            # 优化策略1：先用 JavaScript 批量获取所有图片信息（一次 DOM 查询）
            try:
                js_result = page.run_js("""
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

                # 优化策略2：在内存中筛选，减少 DOM 操作
                for item in js_result:
                    w, h = item['width'], item['height']
                    src, alt, cls, id = item['src'], item['alt'], item['className'], item['id']

                    # 检查是否是二维码（多种特征匹配）
                    is_qr = False
                    selector = None

                    # 特征1：URL/alt/class/id 包含 qr 相关关键词
                    qr_keywords = ['qr', 'qrcode', '二维码', '扫码', 'login']
                    text_to_check = f"{src} {alt} {cls} {id}".lower()
                    if any(kw in text_to_check for kw in qr_keywords):
                        is_qr = True
                        selector = f".{cls.split()[0]}" if cls else (f"#{id}" if id else f"img[src*='{src[:30]}']")

                    # 特征2：正方形且尺寸合适（100-400px）
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
                # 回退：简化版的选择器查询
                try:
                    elements = page.eles('css:.qrcode, #qrcode, img[src*="qr"], img[alt*="二维码"]')
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

            # 如果没找到，提供建议
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

        # 使用线程池执行，防止浏览器卡死导致主线程阻塞
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_find)
                return future.result(timeout=10)  # 缩短到10秒超时
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
            action: 'click' | 'input' | 'scroll_up' | 'scroll_down' | 'move_to' | 'find_qrcode' | 'find_button'
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
        return self._control_action(action, selector, value, x, y, wait_after)

    def _cmd_find_button(self, keyword: str = None) -> dict:
        """
        智能查找页面中的按钮/可点击元素

        Args:
            keyword: 按钮文本关键词（如"扫码"、"登录"），不传则返回所有按钮

        Returns:
            {"success": bool, "buttons": [{"text": str, "selector": str, "tag": str}]}
        """
        import concurrent.futures

        def _do_find():
            page = self._get_page()
            buttons = []

            # 用 JS 批量获取所有可点击元素
            js_code = """
                const results = [];
                const elements = document.querySelectorAll('button, a, input[type="button"], input[type="submit"], [role="button"], .btn, [class*="button"], [class*="btn"]');
                elements.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.bottom > 0) {
                        const text = (el.innerText || el.value || el.alt || el.title || '').trim();
                        if (text || el.id || el.className) {
                            results.push({
                                tag: el.tagName.toLowerCase(),
                                text: text.slice(0, 100),
                                id: el.id,
                                className: el.className,
                                href: el.href || ''
                            });
                        }
                    }
                });
                return results;
            """

            try:
                elements = page.run_js(js_code)
                keyword_lower = (keyword or '').lower()

                # 收集所有候选按钮并打分
                candidates = []
                for el in elements:
                    text = el.get('text', '')
                    cls = el.get('className', '')
                    elem_id = el.get('id', '')
                    tag = el.get('tag', 'button')
                    href = el.get('href', '')

                    # 构建选择器（优先级：id > 特定类名 > 文本 xpath）
                    if elem_id:
                        selector = f"#{elem_id}"
                        selector_quality = 3  # ID 选择器最可靠
                    elif cls:
                        # 过滤掉通用的类名，选择更具体的
                        class_list = cls.split()
                        specific_classes = [c for c in class_list if not self._is_generic_class(c)]
                        if specific_classes:
                            selector = f".{specific_classes[0]}"
                            selector_quality = 2
                        else:
                            # 使用 tag + 类名组合
                            selector = f"{tag}.{class_list[0]}"
                            selector_quality = 1
                    else:
                        # 用文本内容构建 xpath（最不可靠）
                        safe_text = text[:20].replace("'", "\\'")
                        selector = f"//{tag}[contains(text(), '{safe_text}')]"
                        selector_quality = 0

                    # 计算匹配分数
                    score = selector_quality * 10
                    if keyword_lower:
                        text_lower = text.lower()
                        cls_lower = cls.lower()
                        # 完全匹配关键词加分
                        if keyword_lower == text_lower:
                            score += 100
                        elif keyword_lower in text_lower:
                            score += 50
                        elif keyword_lower in cls_lower:
                            score += 20
                        # 按钮类标签加分
                        if tag in ['button', 'input']:
                            score += 10
                        # 有 href 的链接加分（如果是搜索相关）
                        if href and any(k in href.lower() for k in ['search', 'query', 'submit']):
                            score += 5

                    candidates.append({
                        "text": text,
                        "selector": selector,
                        "tag": tag,
                        "score": score,
                        "selector_quality": selector_quality
                    })

                # 按分数排序
                candidates.sort(key=lambda x: x["score"], reverse=True)

                # 如果有关键词，只返回匹配的
                if keyword_lower:
                    buttons = [c for c in candidates if keyword_lower in c["text"].lower() or keyword_lower in c.get("selector", "").lower()][:10]
                else:
                    buttons = candidates[:10]

            except Exception as e:
                logger.debug(f"[WebFetch] 查找按钮失败: {e}")

            if not buttons and keyword:
                return {
                    "success": True,
                    "buttons": [],
                    "hint": f"未找到包含'{keyword}'的按钮，建议先调用 find_button 查看所有可用按钮"
                }

            return {
                "success": True,
                "buttons": buttons,
                "hint": "使用 control(action='click', selector='选择器') 点击按钮"
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
                    # 先尝试等待元素出现（处理动态加载）
                    try:
                        page.wait.ele_displayed(selector, timeout=3)
                    except:
                        pass  # 继续尝试查找
                    
                    element = page.ele(selector, timeout=5)
                    if not element:
                        return {
                            "success": False,
                            "error": f"Element not found: {selector}",
                            "hint": "选择器格式错误或不支持。建议：1) 先用 find_button 查找按钮获取正确的选择器 2) 使用 CSS 选择器如 #id 或 .class 3) 或使用 XPath 如 //button[contains(text(),'文本')]"
                        }
                    
                    # 检查元素是否可见
                    try:
                        if hasattr(element, 'states') and not element.states.is_displayed:
                            return {"success": False, "error": f"Element not visible: {selector}", "hint": "元素存在但不可见，可能需要滚动页面或等待加载"}
                    except:
                        pass  # 某些元素可能没有 states 属性
                    
                    # 滚动到元素（优先使用 JS，更可靠）
                    try:
                        page.run_js("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
                        page.wait(0.3)  # 等待滚动完成
                    except:
                        try:
                            element.scroll.to_see()
                        except:
                            pass  # 如果滚动失败也继续尝试点击
                    
                    # 使用多种点击方式
                    click_success = False
                    
                    # 方式1：DrissionPage 原生点击
                    try:
                        element.click()
                        click_success = True
                    except Exception as click_err:
                        logger.debug(f"[WebFetch] 原生点击失败: {click_err}")
                    
                    # 方式2：JS 点击（备用）
                    if not click_success:
                        try:
                            # 尝试通过元素直接点击
                            page.run_js("arguments[0].click();", element)
                            click_success = True
                        except Exception as js_click_err:
                            logger.debug(f"[WebFetch] JS 元素点击失败: {js_click_err}")
                    
                    # 方式3：通过选择器点击（最后备用）
                    if not click_success:
                        try:
                            # 转换选择器用于 JS（简单处理）
                            js_selector = selector.replace("'", "\\'")
                            page.run_js(f"document.querySelector('{js_selector}').click();")
                            click_success = True
                        except Exception as selector_click_err:
                            logger.debug(f"[WebFetch] JS 选择器点击失败: {selector_click_err}")
                            raise Exception(f"点击失败，已尝试多种方式: {selector}")
                elif x is not None and y is not None:
                    page.click((x, y))
            elif action == 'input':
                if selector:
                    elem = page.ele(selector)
                    if not elem:
                        return {"success": False, "error": f"Element not found: {selector}"}
                    elem.input(value)
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