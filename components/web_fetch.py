# -*- coding: utf-8 -*-
"""
网页抓取工具 - 用无头浏览器抓取页面正文内容
"""

import logging
import re
import json
import concurrent.futures
from DrissionPage import ChromiumPage, ChromiumOptions

from app.core.interface.base_cell import BaseCell
from app.core.util.browser_utils import find_browser_path

logger = logging.getLogger(__name__)


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
            self._options.set_argument('--headless=new')
            self._options.set_argument('--disable-gpu')
            self._options.set_argument('--no-sandbox')
            self._options.set_argument('--disable-blink-features=AutomationControlled')
            self._options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
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
            options.set_argument('--headless=new')
            options.set_argument('--disable-gpu')
            options.set_argument('--no-sandbox')
            options.set_argument('--disable-blink-features=AutomationControlled')
            options.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

            import tempfile
            temp_dir = tempfile.mkdtemp(prefix='drission_')
            options.set_user_data_path(temp_dir)
            options.auto_port(True)

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
        并行抓取多个 URL

        Args:
            urls: URL 列表（必填）
            wait_time: 每个页面等待秒数（默认 3）
            max_workers: 并行线程数（默认 3）

        Returns:
            {"success": bool, "results": [...], "success_count": int, "fail_count": int}
        """
        try:
            # 兼容字符串格式
            if isinstance(urls, str):
                try:
                    urls = json.loads(urls)
                except json.JSONDecodeError:
                    return {"success": False, "error": "urls 参数格式错误，需要列表"}

            if not urls:
                return {"success": False, "error": "缺少必填参数 'urls'"}

            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_url = {
                    executor.submit(self._fetch_single, url, wait_time): url
                    for url in urls
                }
                for future in concurrent.futures.as_completed(future_to_url):
                    result = future.result()
                    results.append(result)

            success_count = sum(1 for r in results if r['success'])
            fail_count = len(results) - success_count

            return {
                "success": True,
                "results": results,
                "success_count": success_count,
                "fail_count": fail_count,
                "total": len(results)
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