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
from typing import Dict, Any
from DrissionPage import ChromiumPage, ChromiumOptions

from app.core.interface.base_cell import BaseCell
from app.core.util.browser_utils import find_browser_path

logger = logging.getLogger(__name__)

_MAX_PARALLEL_TABS = 5


class WebFetch(BaseCell):
    """
    网页抓取工具 - 无头浏览器抓取页面正文

    功能说明:
      - fetch: 用无头浏览器抓取页面正文内容（支持 JS 渲染）
    """

    def __init__(self):
        super().__init__()
        self._page = None
        self._options = None

    @property
    def cell_name(self) -> str:
        return "web_fetch"

    def _get_options(self):
        if self._options is None:
            self._options = ChromiumOptions()
            browser_path = find_browser_path()
            if browser_path:
                self._options.set_browser_path(browser_path)
            self._options.set_argument('--disable-gpu')
            self._options.set_argument('--no-sandbox')
            self._options.set_argument('--disable-blink-features=AutomationControlled')
            self._options.set_argument('--disable-dev-shm-usage')
            self._options.set_argument('--disable-extensions')
            self._options.set_argument('--profile-directory=Default')
            self._options.set_argument('--disable-plugins-discovery')
            self._options.set_argument('--disable-infobars')
            self._options.set_argument('--start-maximized')
            self._options.set_argument('--disable-popup-blocking')
            self._options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            self._options.headless(True)
        return self._options

    def _get_page(self):
        if self._page is None:
            self._page = ChromiumPage(self._get_options(), timeout=15)
        return self._page

    def _close_page(self):
        if self._page:
            try:
                self._page.quit()
            except Exception as e:
                logger.debug("[WebFetch] 关闭浏览器失败: %s", e)
            finally:
                self._page = None

    def _new_page(self):
        """创建新页面"""
        try:
            self._close_page()
        except Exception:
            pass
        self._page = ChromiumPage(self._get_options(), timeout=15)
        return self._page

    def _clean_text(self, text):
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _cmd_fetch(self, url: str, wait_time: int = 5) -> dict:
        """
        用无头浏览器抓取页面正文内容

        Args:
            url: 要抓取的 URL（必填）
            wait_time: 等待秒数（默认 5）

        Returns:
            {"success": bool, "url": str, "title": str, "text": str, "error": str}
        """
        result = {
            'url': url,
            'title': '',
            'text': '',
            'html': '',
            'success': False,
            'error': ''
        }

        try:
            if not url or not url.strip():
                return {"success": False, "error": "缺少必填参数 'url'"}

            url = url.strip()
            page = self._new_page()

            page.get(url, timeout=30)
            page.wait(wait_time)

            result['title'] = page.title
            result['html'] = page.html[:50000]

            selectors = [
                ('tag', 'article'),
                ('tag', 'main'),
                ('css', '.article-content'),
                ('css', '.post-content'),
                ('css', '.entry-content'),
                ('css', '.content'),
                ('css', '#content'),
                ('css', '.article-body'),
                ('css', '.news-content'),
                ('css', '.article'),
                ('css', '.post'),
                ('css', '.entry'),
            ]

            best_text = ''
            for sel_type, sel_value in selectors:
                try:
                    if sel_type == 'tag':
                        elem = page.ele(f'tag:{sel_value}', timeout=1)
                    else:
                        elem = page.ele(f'css:.{sel_value}', timeout=1)
                    if elem:
                        txt = self._clean_text(elem.text)
                        if len(txt) > len(best_text):
                            best_text = txt
                except Exception:
                    continue  # 选择器未匹配，尝试下一个

            if not best_text:
                try:
                    body = page.ele('tag:body', timeout=2)
                    if body:
                        best_text = self._clean_text(body.text)
                except Exception as e:
                    logger.debug("[WebFetch] 获取 body 失败: %s", e)

            result['text'] = best_text[:5000]
            result['success'] = True

        except Exception as e:
            result['error'] = f'浏览器错误: {e}'

        return result

    def _fetch_single(self, url: str, wait_time: float = 5) -> dict:
        """抓取单个 URL（用于线程池）"""
        result = {
            'url': url,
            'title': '',
            'text': '',
            'success': False,
            'error': ''
        }
        page = None
        try:
            options = ChromiumOptions()
            browser_path = find_browser_path()
            if browser_path:
                options.set_browser_path(browser_path)
            options.set_argument('--disable-gpu')
            options.set_argument('--no-sandbox')
            options.set_argument('--disable-blink-features=AutomationControlled')
            options.set_argument('--disable-dev-shm-usage')
            options.set_argument('--disable-extensions')
            options.set_argument('--disable-popup-blocking')
            options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

            page = ChromiumPage(options, timeout=15)
            
            page.get(url, timeout=30)
            page.wait(wait_time)
            
            result['title'] = page.title
            
            selectors = [
                ('tag', 'article'), ('tag', 'main'),
                ('css', '.article-content'), ('css', '.post-content'),
                ('css', '.entry-content'), ('css', '.content'),
                ('css', '#content'), ('css', '.article-body'),
            ]
            
            best_text = ''
            for sel_type, sel_value in selectors:
                try:
                    if sel_type == 'tag':
                        elem = page.ele(f'tag:{sel_value}', timeout=1)
                    else:
                        elem = page.ele(f'css:.{sel_value}', timeout=1)
                    if elem:
                        txt = self._clean_text(elem.text)
                        if len(txt) > len(best_text):
                            best_text = txt
                except Exception:
                    continue  # 选择器未匹配，尝试下一个
            
            if not best_text:
                try:
                    body = page.ele('tag:body', timeout=2)
                    if body:
                        best_text = self._clean_text(body.text)
                except Exception as e:
                    logger.debug("[WebFetch] _fetch_single 获取 body 失败: %s", e)
            
            result['text'] = best_text[:5000]
            result['success'] = True
            
        except Exception as e:
            result['error'] = str(e)
        finally:
            # 确保页面关闭，释放资源
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
        
        return result

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
        """提取页面结构：heading树 + 链接统计"""
        headings = []
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for el in page.eles(f'tag:{tag}'):
                text = el.text.strip()[:100]
                if text:
                    headings.append({"tag": tag, "text": text})
            if len(headings) >= 20:
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

    def _cmd_close(self) -> dict:
        """关闭浏览器，释放资源"""
        self._close_page()
        return {"success": True, "message": "浏览器已关闭"}

    def _cmd_get_screenshot(self, full_page: bool = False) -> dict:
        """
        获取当前页面截图并返回 Base64

        Args:
            full_page: 是否截取整页（默认 False）

        Returns:
            {"success": bool, "base64_image": str, "format": str}
        """
        temp_path = None
        try:
            page = self._get_page()
            temp_path = f"temp_screenshot_{int(time.time())}.png"
            page.get_screenshot(path=temp_path, full_page=full_page)
            with open(temp_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            return {"success": True, "base64_image": encoded_string, "format": "png"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def _cmd_do_action(self, action: str, selector: str = None, value: str = None, x: int = None, y: int = None, wait_after: float = 1.0) -> dict:
        """
        执行具体的交互动作

        Args:
            action: 'click' | 'input' | 'scroll_up' | 'scroll_down' | 'move_to'
            selector: CSS 选择器或 xpath
            value: 输入值或滚动方向
            x, y: 坐标（可选）
            wait_after: 动作后等待秒数（默认 1.0）

        Returns:
            {"success": bool, "current_url": str, "error": str}
        """
        try:
            page = self._get_page()
            if action == 'click':
                if selector:
                    element = page.ele(selector, timeout=5)
                    if not element:
                        return {"success": False, "error": f"Element not found: {selector}"}
                    if hasattr(element, 'states') and not element.states.is_displayed:
                        return {"success": False, "error": f"Element not visible: {selector}"}
                    element.scroll.to_see()
                    element.click()
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
            "description": "网页抓取工具 - 无头浏览器抓取页面正文（支持并行）",
            "available_commands": list(commands.keys()),
            "usage": {
                "fetch": {
                    "description": "抓取单个页面",
                    "args": {"url": "页面URL", "wait_time": "等待秒数"},
                    "example": '{"url": "https://github.com"}'
                },
                "fetch_many": {
                    "description": "并行抓取多个页面",
                    "args": {"urls": "URL列表", "wait_time": "等待秒数", "max_workers": "并行线程数"},
                    "example": '{"urls": ["https://a.com", "https://b.com"], "max_workers": 3}'
                }
            }
        }

    def on_load(self):
        super().on_load()