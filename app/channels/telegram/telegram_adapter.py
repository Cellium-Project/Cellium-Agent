# -*- coding: utf-8 -*-
"""
TelegramAdapter - Telegram Bot 通道适配器
"""

import asyncio
import logging
import os
import re
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import httpx

from ..base import ChannelAdapter, UnifiedMessage

logger = logging.getLogger(__name__)

# 文件大小限制（字节）
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

# 下载文件保存目录
def _default_download_dir():
    from app.core.util.runtime_paths import resolve_dir_writable
    return Path(resolve_dir_writable("workspace")) / "downloads" / "telegram"


class _RichStreamSession:
    THROTTLE = 0.7
    MAX_HTML = 32000
    MAX_RESULT_CHARS = 2000
    MAX_RESULT_LINES = 64

    def __init__(self, adapter: "TelegramAdapter", chat_id: str, thread_id: Optional[str] = None):
        self._adapter = adapter
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._message_id = None
        self._segments = []
        self._dirty = False
        self._degraded = False
        self._finished = False
        self._fail_count = 0
        self._last_render = ""
        self._task = None

    @property
    def degraded(self) -> bool:
        return self._degraded

    async def start(self) -> bool:
        self._task = asyncio.create_task(self._loop())
        return True

    async def _ensure_message(self) -> bool:
        if self._message_id:
            return True
        html = self._render()
        if not html:
            return False
        try:
            data = {"chat_id": self._chat_id, "rich_message": {"html": html}}
            if self._thread_id:
                data["message_thread_id"] = int(self._thread_id)
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{self._adapter.base_url}/sendRichMessage", json=data)
                result = resp.json()
            if result.get("ok"):
                self._message_id = result.get("result", {}).get("message_id")
                if self._message_id:
                    self._last_render = html
                    return True
            logger.warning("[TelegramAdapter] rich stream start failed: %s", result.get("description", ""))
        except Exception as e:
            logger.warning("[TelegramAdapter] rich stream start error: %s", e)
        return False

    def on_event(self, event: dict):
        if self._degraded or self._finished:
            return
        etype = event.get("type")
        try:
            if etype == "tool_start":
                self._add_tool_start(event)
            elif etype == "tool_result":
                self._add_tool_result(event)
            else:
                return
            self._dirty = True
        except Exception as e:
            logger.warning("[TelegramAdapter] rich stream on_event error: %s", e)

    def _add_tool_start(self, event: dict):
        self._segments.append({
            "kind": "tool",
            "name": event.get("tool", "unknown"),
            "desc": event.get("description", ""),
            "call_id": event.get("call_id", ""),
            "status": "running",
            "duration": 0,
            "result": "",
            "error": "",
            "diff": "",
        })

    def _add_tool_result(self, event: dict):
        call_id = event.get("call_id", "")
        name = event.get("tool", "unknown")
        target = None
        for seg in reversed(self._segments):
            if seg["kind"] == "tool" and seg["status"] == "running":
                if (call_id and seg["call_id"] == call_id) or (not call_id and seg["name"] == name):
                    target = seg
                    break
        if target is None:
            for seg in reversed(self._segments):
                if seg["kind"] == "tool" and seg["status"] == "running" and seg["name"] == name:
                    target = seg
                    break
        if target is None:
            return
        result = event.get("result", {})
        err = event.get("error") or (result.get("error") if isinstance(result, dict) else "")
        target["status"] = "error" if err else "done"
        target["duration"] = event.get("duration_ms", 0)
        target["result"] = self._extract_result(result)
        if isinstance(result, dict) and isinstance(result.get("diff"), str):
            target["diff"] = result["diff"]
        if err:
            target["error"] = str(err)

    @staticmethod
    def _extract_result(result) -> str:
        if isinstance(result, dict):
            for key in ("content", "output", "text", "result"):
                v = result.get(key)
                if isinstance(v, str) and v.strip():
                    return v
            return ""
        return str(result) if result else ""

    def _clip_result(self, text: str) -> str:
        lines = text.split("\n")
        if len(lines) > self.MAX_RESULT_LINES:
            text = "\n".join(lines[:self.MAX_RESULT_LINES]) + f"\n… 共 {len(lines)} 行"
        if len(text) > self.MAX_RESULT_CHARS:
            text = text[:self.MAX_RESULT_CHARS] + "\n…"
        return text

    def _text_to_html(self, text: str) -> str:
        out = []
        for line in self._clip_result(text).split('\n'):
            html = self._adapter._escape_html(line).replace(' ', '&nbsp;')
            out.append(html or '&nbsp;')
        return '<br/>'.join(out)

    def _render_tool(self, seg: dict) -> str:
        esc = self._adapter._escape_html
        head = f'<b>{esc(seg["name"])}</b>'
        if seg.get("desc"):
            head += f' · {esc(seg["desc"][:60])}'
        if seg["status"] == "running":
            return f'<blockquote>🔧 {head} · 执行中…</blockquote>'
        icon = "✅" if seg["status"] == "done" else "❌"
        head = f'{icon} {head}'
        if seg["duration"]:
            head += f' · {seg["duration"]}ms'
        if seg["status"] == "error" and seg.get("error"):
            return f'<blockquote expandable>{head}<br/>{self._text_to_html(seg["error"])}</blockquote>'
        diff = seg.get("diff") or ""
        if diff.strip():
            lines = diff.split('\n')
            adds = sum(1 for l in lines if l.startswith('+') and not l.startswith('+++'))
            dels = sum(1 for l in lines if l.startswith('-') and not l.startswith('---'))
            head += f' · +{adds} -{dels}'
            return f'<blockquote expandable>{head}<br/>{self._text_to_html(diff)}</blockquote>'
        return f'<blockquote>{head}</blockquote>'

    def _render(self) -> str:
        while True:
            html = "\n".join(self._render_tool(seg) for seg in self._segments)
            if len(html.encode("utf-8")) <= self.MAX_HTML:
                return html
            if len(self._segments) <= 1:
                return html
            self._segments.pop(0)

    async def _push(self) -> bool:
        html = self._render()
        if not html:
            return True
        if not self._message_id:
            return await self._ensure_message()
        if html == self._last_render:
            return True
        data = {"chat_id": self._chat_id, "message_id": self._message_id, "rich_message": {"html": html}}
        for _ in range(2):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(f"{self._adapter.base_url}/editMessageText", json=data)
                    result = resp.json()
                if result.get("ok"):
                    self._last_render = html
                    self._fail_count = 0
                    return True
                desc = result.get("description", "")
                if "message is not modified" in desc:
                    self._last_render = html
                    return True
                retry_after = result.get("parameters", {}).get("retry_after")
                if retry_after:
                    await asyncio.sleep(retry_after + 0.2)
                    continue
                logger.warning("[TelegramAdapter] rich stream push failed: %s", desc)
                break
            except Exception as e:
                logger.warning("[TelegramAdapter] rich stream push error: %s", e)
                break
        self._fail_count += 1
        if self._fail_count >= 2:
            self._degraded = True
        return False

    async def _loop(self):
        try:
            while not self._finished:
                await asyncio.sleep(self.THROTTLE)
                if self._dirty and not self._degraded:
                    self._dirty = False
                    await self._push()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("[TelegramAdapter] rich stream loop error: %s", e)

    async def _recover_full(self):
        try:
            async with httpx.AsyncClient() as client:
                await client.post(f"{self._adapter.base_url}/deleteMessage",
                                  json={"chat_id": self._chat_id, "message_id": self._message_id})
        except Exception:
            pass
        lines = []
        for seg in self._segments:
            icon = "✅" if seg["status"] == "done" else ("❌" if seg["status"] == "error" else "🔧")
            line = f'{icon} {seg["name"]}'
            if seg["duration"]:
                line += f' · {seg["duration"]}ms'
            lines.append(line)
        text = "\n".join(lines)
        if text.strip():
            try:
                await self._adapter.send_message(self._chat_id, text, "private")
            except Exception as e:
                logger.warning("[TelegramAdapter] rich stream recover send failed: %s", e)

    async def finish(self):
        if self._finished:
            return
        self._finished = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if not self._message_id:
            return
        if self._degraded:
            await self._recover_full()
            return
        if not self._render():
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(f"{self._adapter.base_url}/deleteMessage",
                                      json={"chat_id": self._chat_id, "message_id": self._message_id})
            except Exception:
                pass
            return
        if not await self._push():
            await self._recover_full()


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot 适配器"""
    max_text_chunk = 4000

    def __init__(self, bot_token: str, whitelist_user_ids: Optional[list] = None,
                 whitelist_usernames: Optional[list] = None, use_rich_messages: bool = True, **kwargs):
        """
        初始化 Telegram Adapter

        Args:
            bot_token: Telegram Bot Token (从 @BotFather 获取)
            whitelist_user_ids: 允许的用户 ID 列表，空列表表示允许所有人
            whitelist_usernames: 允许的用户名列表，空列表表示允许所有人
            use_rich_messages: 是否使用 Bot API 10.1 Rich Messages
        """
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.whitelist_user_ids = set(whitelist_user_ids or [])
        self.whitelist_usernames = set((u.lower() for u in (whitelist_usernames or [])))
        self._use_rich_messages = use_rich_messages

        # 创建下载目录
        self.download_dir = _default_download_dir()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._last_update_id: Optional[int] = None
        self._message_handler: Optional[Callable[[UnifiedMessage], None]] = None
        self._client: Optional[httpx.AsyncClient] = None

        logger.info("[TelegramAdapter] Initialized")

    @property
    def platform_name(self) -> str:
        return "telegram"

    def _is_user_allowed(self, user_info: Dict[str, Any]) -> bool:
        """检查用户是否在白名单中"""
        if not self.whitelist_user_ids and not self.whitelist_usernames:
            return True

        user_id = user_info.get("id")
        username = user_info.get("username", "")

        if user_id in self.whitelist_user_ids:
            return True
        if username and username.lower() in self.whitelist_usernames:
            return True

        return False

    def _parse_markdown_to_entities(self, text: str) -> tuple[str, list]:
        """
        使用 AST 方式解析 Markdown，生成纯文本 + entities 列表
        返回: (纯文本, entities列表)
        
        entities 格式: [{"type": "bold", "offset": 0, "length": 5}, ...]
        offset 和 length 使用 UTF-16 编码单位
        """
        def ulen(s: str) -> int:
            """计算字符串的 UTF-16 编码单位长度"""
            return len(s.encode("utf-16-le")) // 2
        
        # 定义 Token 类型
        Token = tuple[str, str] 
        tokens: list[tuple] = []
        
        inline_patterns = [
            (r'```(\w+)?\s*\n(.*?)```', 'pre'),     # 代码块
            (r'`([^`]+)`', 'bold'),                  # 行内代码用加粗代替
            (r'\*\*(.+?)\*\*', 'bold'),             # 粗体
            (r'__(.+?)__', 'bold'),                  # 粗体
            (r'\*(.+?)\*', 'italic'),                # 斜体
            (r'_(.+?)_', 'italic'),                  # 斜体
            (r'~~(.+?)~~', 'strikethrough'),         # 删除线
        ]
        
        def parse_inline(line: str) -> list[tuple]:
            """解析行内格式，返回 token 列表"""
            result = []
            remaining = line
            
            while remaining:
                earliest_match = None
                earliest_pattern = None
                earliest_pos = len(remaining)
                
                for pattern, entity_type in inline_patterns:
                    match = re.search(pattern, remaining, re.DOTALL)
                    if match and match.start() < earliest_pos:
                        earliest_pos = match.start()
                        earliest_match = match
                        earliest_pattern = (pattern, entity_type)
                
                if earliest_match:
                    before_text = remaining[:earliest_match.start()]
                    if before_text:
                        result.append(("text", before_text))
                    
                    pattern, entity_type = earliest_pattern
                    if entity_type == 'pre':
                        lang = earliest_match.group(1)
                        code_content = earliest_match.group(2)
                        if lang:
                            result.append(("pre", code_content, lang))
                        else:
                            result.append(("pre", code_content))
                    elif entity_type == 'code':
                        code_content = earliest_match.group(1)
                        result.append(("code", code_content))
                    else:
                        content = earliest_match.group(1)
                        result.append((entity_type, content))
                    
                    remaining = remaining[earliest_match.end():]
                else:
                    if remaining:
                        result.append(("text", remaining))
                    break
            
            return result
        
        lines = text.split('\n')
        all_tokens: list[tuple] = []
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            
            code_block_match = re.match(r'```(\w+)?\s*$', stripped)
            if code_block_match:
                lang = code_block_match.group(1)
                code_lines = []
                i += 1
                while i < len(lines):
                    code_line = lines[i]
                    if code_line.lstrip().startswith('```'):
                        i += 1
                        break
                    code_lines.append(code_line)
                    i += 1

                code_content = '\n'.join(code_lines)
                if lang:
                    all_tokens.append(("pre", code_content, lang))
                else:
                    all_tokens.append(("pre", code_content))
                if i < len(lines):
                    all_tokens.append(("text", "\n"))
                continue
            
            if stripped == '---' or stripped == '***' or stripped == '___':
                # 分隔线：使用短分隔线
                all_tokens.append(("text", "─────\n"))
            elif stripped.count('|') >= 2:
                if re.match(r'^[\s|:-]+$', stripped):
                    pass 
                else:
                    cells = [c.strip() for c in stripped.split('|') if c.strip()]
                    if cells:
                        for j, cell in enumerate(cells):
                            cell_tokens = parse_inline(cell)
                            all_tokens.extend(cell_tokens)
                            if j < len(cells) - 1:
                                all_tokens.append(("text", " | "))
            elif stripped.startswith('### '):
                title_content = stripped[4:]
                title_tokens = parse_inline(title_content)
                all_tokens.append(("bold_start", ""))
                all_tokens.extend(title_tokens)
                all_tokens.append(("bold_end", ""))
            elif stripped.startswith('## '):
                title_content = stripped[3:]
                title_tokens = parse_inline(title_content)
                all_tokens.append(("bold_start", ""))
                all_tokens.extend(title_tokens)
                all_tokens.append(("bold_end", ""))
            elif stripped.startswith('# '):
                title_content = stripped[2:]
                title_tokens = parse_inline(title_content)
                all_tokens.append(("bold_start", ""))
                all_tokens.extend(title_tokens)
                all_tokens.append(("bold_end", ""))
            elif stripped.startswith('> '):
                quote_content = stripped[2:]
                
                if quote_content.startswith('### '):
                    all_tokens.append(("italic_start", ""))
                    all_tokens.append(("bold_start", ""))
                    title_tokens = parse_inline(quote_content[4:])
                    all_tokens.extend(title_tokens)
                    all_tokens.append(("bold_end", ""))
                    all_tokens.append(("italic_end", ""))
                elif quote_content.startswith('## '):
                    all_tokens.append(("italic_start", ""))
                    all_tokens.append(("bold_start", ""))
                    title_tokens = parse_inline(quote_content[3:])
                    all_tokens.extend(title_tokens)
                    all_tokens.append(("bold_end", ""))
                    all_tokens.append(("italic_end", ""))
                elif quote_content.startswith('# '):
                    all_tokens.append(("italic_start", ""))
                    all_tokens.append(("bold_start", ""))
                    title_tokens = parse_inline(quote_content[2:])
                    all_tokens.extend(title_tokens)
                    all_tokens.append(("bold_end", ""))
                    all_tokens.append(("italic_end", ""))
                else:
                    quote_tokens = parse_inline(quote_content)
                    all_tokens.append(("italic_start", ""))
                    all_tokens.extend(quote_tokens)
                    all_tokens.append(("italic_end", ""))
            else:
                inline_tokens = parse_inline(line)
                all_tokens.extend(inline_tokens)
            
            if i < len(lines) - 1:
                next_line = lines[i + 1].lstrip() if i + 1 < len(lines) else ""
                if next_line not in ('---', '***', '___'):
                    if stripped not in ('---', '***', '___'):
                        all_tokens.append(("text", "\n"))
            
            i += 1
        
        final_text = ""
        entities = []
        bold_stack = []  
        italic_stack = [] 
        
        for token in all_tokens:
            token_type = token[0]
            
            if token_type == "text":
                final_text += token[1]
            elif token_type == "bold_start":
                bold_stack.append(ulen(final_text))
            elif token_type == "bold_end":
                if bold_stack:
                    start = bold_stack.pop()
                    length = ulen(final_text) - start
                    if length > 0:
                        entities.append({"type": "bold", "offset": start, "length": length})
                
            elif token_type == "italic_start":
                italic_stack.append(ulen(final_text))
                
            elif token_type == "italic_end":
                if italic_stack:
                    start = italic_stack.pop()
                    length = ulen(final_text) - start
                    if length > 0:
                        entities.append({"type": "italic", "offset": start, "length": length})
                
            elif token_type == "pre":
                code_content = token[1]
                start = ulen(final_text)
                final_text += code_content
                length = ulen(code_content)
                entity = {"type": "pre", "offset": start, "length": length}
                if len(token) > 2:  
                    entity["language"] = token[2]
                entities.append(entity)
                
            elif token_type == "code":
                code_content = token[1]
                start = ulen(final_text)
                final_text += code_content
                length = ulen(code_content)
                entities.append({"type": "code", "offset": start, "length": length})
                
            elif token_type in ("bold", "italic", "strikethrough"):
                content = token[1]
                start = ulen(final_text)
                final_text += content
                length = ulen(content)
                entities.append({"type": token_type, "offset": start, "length": length})
        
        return final_text, entities

    # ============================================================
    # Rich Messages (Bot API 10.1)
    # ============================================================

    @staticmethod
    def _escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def _inline_to_html(self, text: str) -> str:
        text = self._escape_html(text)
        placeholders = {}
        def _store(html):
            idx = len(placeholders)
            placeholders[idx] = html
            return f'\uE000{idx}\uE001'
        def _save_code(m):
            return _store(f'<code>{m.group(1)}</code>')
        text = re.sub(r'`([^`]+)`', _save_code, text)
        text = re.sub(r'\$\$([^\n]+?)\$\$', lambda m: _store(f'<tg-math-block>{m.group(1)}</tg-math-block>'), text)
        text = re.sub(r'\$(\S(?:[^$\n]*?\S)?)\$', lambda m: _store(f'<tg-math>{m.group(1)}</tg-math>'), text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
        text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
        text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'<i>\1</i>', text)
        text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
        for idx in sorted(placeholders):
            text = text.replace(f'\uE000{idx}\uE001', placeholders[idx])
        return text

    def _table_to_html(self, lines: list) -> Optional[str]:
        data_rows = [l for l in lines if not re.match(r'^[\s|:\-]+\s*$', l.strip())]
        if not data_rows:
            return None
        header_cells = [c.strip() for c in data_rows[0].split('|') if c.strip()]
        body_rows = data_rows[1:]
        parts = ['<table>']
        if header_cells:
            parts.append('<tr>' + ''.join(f'<th>{self._inline_to_html(c)}</th>' for c in header_cells) + '</tr>')
        for row in body_rows:
            cells = [c.strip() for c in row.split('|') if c.strip()]
            if cells:
                parts.append('<tr>' + ''.join(f'<td>{self._inline_to_html(c)}</td>' for c in cells) + '</tr>')
        parts.append('</table>')
        return ''.join(parts)

    def _build_nested_list(self, text: str, list_type: str) -> str:
        pattern = r'^(\s*)[-*+]\s+(.+)$' if list_type == 'ul' else r'^(\s*)\d+\.\s+(.+)$'
        buf = []
        i = 0
        lines = text.split('\n')
        while i < len(lines):
            m = re.match(pattern, lines[i])
            if not m:
                i += 1
                continue
            indent = len(m.group(1))
            raw = m.group(2)
            chk = re.match(r'^\[( |x|X)\]\s+(.+)$', raw, re.DOTALL)
            if chk:
                box = '☑' if chk.group(1).lower() == 'x' else '☐'
                content = f'{box} {self._inline_to_html(chk.group(2))}'
            else:
                content = self._inline_to_html(raw)
            sub_lines = []
            j = i + 1
            while j < len(lines):
                sm = re.match(pattern, lines[j])
                if not sm:
                    j += 1
                    continue
                if len(sm.group(1)) > indent:
                    sub_lines.append(lines[j])
                else:
                    break
                j += 1
            inner = content
            if sub_lines:
                inner += self._build_nested_list('\n'.join(sub_lines), list_type)
            buf.append(f'<li>{inner}</li>')
            i = j
        tag = 'ul' if list_type == 'ul' else 'ol'
        return f'<{tag}>{"".join(buf)}</{tag}>'

    def _richify_markdown(self, content: str) -> str:
        if not content:
            return ""
        raw_blocks = re.split(r'\n{2,}', content)
        merged = []
        in_details = False
        fence_open = False
        for blk in raw_blocks:
            if fence_open:
                merged[-1] += '\n\n' + blk
                if merged[-1].count('```') % 2 == 0:
                    fence_open = False
                continue
            if in_details:
                merged[-1] += '\n\n' + blk
                if '</details>' in blk:
                    in_details = False
                continue
            merged.append(blk)
            if blk.count('```') % 2 == 1:
                fence_open = True
            if '<details>' in blk and '</details>' not in blk:
                in_details = True
        raw_blocks = merged
        html_blocks = []

        for block in raw_blocks:
            stripped = block.strip()
            if not stripped:
                continue

            fenced = re.match(r'```(\w*)\s*\n?(.*?)```', stripped, re.DOTALL)
            if fenced:
                html_blocks.append(self._code_to_html(fenced.group(1), fenced.group(2)))
                continue

            math_block = re.match(r'^\$\$\s*\n?(.*?)\n?\s*\$\$$', stripped, re.DOTALL)
            if math_block:
                html_blocks.append(f'<tg-math-block>{self._escape_html(math_block.group(1))}</tg-math-block>')
                continue

            if stripped.startswith('<details>') and stripped.endswith('</details>'):
                det = re.match(r'^<details>\s*(?:<summary>(.*?)</summary>\s*)?(.*?)</details>$', stripped, re.DOTALL)
                if det:
                    summary = self._inline_to_html(det.group(1)) if det.group(1) else '详情'
                    inner = self._richify_markdown(det.group(2))
                    html_blocks.append(f'<details><summary>{summary}</summary>{inner}</details>')
                    continue

            if re.match(r'^[-*_]{3,}\s*$', stripped):
                html_blocks.append('<hr/>')
                continue

            if all(l.startswith('>') for l in stripped.split('\n') if l.strip()):
                quote_lines = [re.sub(r'^>\s?', '', l) for l in stripped.split('\n')]
                quote_html = self._inline_to_html('\n'.join(quote_lines)).replace('\n', '<br/>')
                need_expand = len(quote_html) > 300
                tag = 'blockquote expandable' if need_expand else 'blockquote'
                html_blocks.append(f'<{tag}>{quote_html}</{tag.split()[0]}>')
                continue

            h_match = re.match(r'(#{1,6})\s+(.+?)(?:\s+#{1,6})?\s*$', stripped.split('\n')[0], re.MULTILINE)
            if h_match:
                level = len(h_match.group(1))
                heading_text = self._inline_to_html(h_match.group(2))
                html_blocks.append(f'<h{level}>{heading_text}</h{level}>')
                rest = stripped[len(h_match.group(0)):].strip()
                if rest:
                    rest_lines = rest.split('\n')
                    if all(l.startswith('>') for l in rest_lines if l.strip()):
                        quote_lines = [re.sub(r'^>\s?', '', l) for l in rest_lines]
                        quote_html = self._inline_to_html('\n'.join(quote_lines)).replace('\n', '<br/>')
                        need_expand = len(quote_html) > 300
                        tag = 'blockquote expandable' if need_expand else 'blockquote'
                        html_blocks.append(f'<{tag}>{quote_html}</{tag.split()[0]}>')
                    else:
                        html_blocks.append(self._render_plain(rest))
                continue

            lines = stripped.split('\n')
            list_start = None
            list_prefix = ""
            for i, ln in enumerate(lines):
                if not ln.strip():
                    continue
                if re.match(r'^[-*+]\s+(.+)$', ln):
                    list_start = 'ul'
                    list_prefix = '\n'.join(lines[:i])
                    stripped = '\n'.join(lines[i:])
                    break
                if re.match(r'^\d+\.\s+(.+)$', ln):
                    list_start = 'ol'
                    list_prefix = '\n'.join(lines[:i])
                    stripped = '\n'.join(lines[i:])
                    break

            if list_start == 'ul':
                part = self._build_nested_list(stripped, 'ul')
                if list_prefix:
                    html_blocks.append(f'<p>{self._inline_to_html(list_prefix)}</p>')
                html_blocks.append(part)
                continue

            if list_start == 'ol':
                part = self._build_nested_list(stripped, 'ol')
                if list_prefix:
                    html_blocks.append(f'<p>{self._inline_to_html(list_prefix)}</p>')
                html_blocks.append(part)
                continue

            html_blocks.append(self._render_plain(stripped))

        return '\n'.join(html_blocks)

    def _extract_tables(self, text: str) -> list:
        lines = text.split('\n')
        parts = []
        buf = []
        i = 0
        while i < len(lines):
            line = lines[i]
            nxt = lines[i + 1] if i + 1 < len(lines) else ''
            if '|' in line and i + 1 < len(lines) and '-' in nxt and re.match(r'^\s*\|?[\s:|\-]+\|?\s*$', nxt):
                if buf:
                    parts.append(('text', '\n'.join(buf)))
                    buf = []
                tbl = [line, nxt]
                j = i + 2
                while j < len(lines) and '|' in lines[j]:
                    tbl.append(lines[j])
                    j += 1
                parts.append(('table', tbl))
                i = j
            else:
                buf.append(line)
                i += 1
        if buf:
            parts.append(('text', '\n'.join(buf)))
        return parts

    def _extract_fences(self, text: str) -> list:
        lines = text.split('\n')
        parts = []
        buf = []
        i = 0
        while i < len(lines):
            m = re.match(r'^\s*```(\w*)\s*$', lines[i])
            if m:
                if buf:
                    parts.append(('text', '\n'.join(buf)))
                    buf = []
                lang = m.group(1)
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith('```'):
                    code_lines.append(lines[i])
                    i += 1
                parts.append(('fence', lang, '\n'.join(code_lines)))
                i += 1
            else:
                buf.append(lines[i])
                i += 1
        if buf:
            parts.append(('text', '\n'.join(buf)))
        return parts

    def _code_to_html(self, lang: str, code: str) -> str:
        code = code.rstrip('\n')
        esc = self._escape_html(code)
        if lang == 'math':
            return f'<tg-math-block>{esc}</tg-math-block>'
        return f'<pre>{esc}</pre>'

    def _render_plain(self, text: str) -> str:
        parts = []
        for kind, *payload in self._extract_fences(text):
            if kind == 'fence':
                lang, code = payload
                parts.append(self._code_to_html(lang, code))
                continue
            for kind2, payload2 in self._extract_tables(payload[0]):
                if kind2 == 'table':
                    table = self._table_to_html(payload2)
                    if table:
                        parts.append(table)
                elif payload2.strip():
                    parts.append(f'<p>{self._inline_to_html(payload2.strip()).replace(chr(10), "<br/>")}</p>')
        return '\n'.join(p for p in parts if p)

    async def _send_rich_message(self, target_id: str, content: str, **kwargs) -> bool:
        try:
            html = self._richify_markdown(content)
            if not html:
                return False
            payload = html.encode('utf-8')
            if len(payload) > 32768:
                logger.warning("[TelegramAdapter] Rich message too large (%d bytes), falling back", len(payload))
                return False
            data = {"chat_id": target_id, "rich_message": {"html": html}}
            reply_to = kwargs.get("reply_to_message_id")
            if reply_to:
                data["reply_to_message_id"] = reply_to
            thread_id = kwargs.get("thread_id")
            if thread_id:
                data["message_thread_id"] = int(thread_id)
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{self.base_url}/sendRichMessage", json=data)
                result = resp.json()
            if not result.get("ok"):
                logger.warning("[TelegramAdapter] sendRichMessage failed: %s", result.get("description", ""))
                return False
            return True
        except Exception as e:
            logger.error("[TelegramAdapter] sendRichMessage error: %s", e)
            return False

    async def start_rich_stream(self, target_id: str, thread_id: Optional[str] = None) -> Optional["_RichStreamSession"]:
        session = _RichStreamSession(self, target_id, thread_id=thread_id)
        if await session.start():
            return session
        return None

    def _format_message(self, content: str) -> tuple[str, list]:
        if not content:
            return "", []
        
        logger.debug(f"[TelegramAdapter] _format_message input preview: {content[:200]}...")
        
        text, entities = self._parse_markdown_to_entities(content)
        
        logger.debug(f"[TelegramAdapter] Parsed {len(entities)} entities")
        
        return text, entities

    def _split_message(self, text: str, max_length: int = 4000) -> list:
        if len(text) <= max_length:
            return [text]

        parts = []
        remaining = text

        while remaining:
            if len(remaining) <= max_length:
                parts.append(remaining)
                break

            cut_point = max_length

            for i in range(max_length, max_length - 500, -1):
                if i < 0:
                    break
                if remaining[i] == '\n':
                    cut_point = i + 1
                    break

            parts.append(remaining[:cut_point])
            remaining = remaining[cut_point:]

        return parts

    async def connect(self):
        if self._running:
            logger.warning("[TelegramAdapter] Already connected")
            return

        await self.disconnect()

        me = await self._get_me()
        if not me:
            raise ConnectionError("Failed to connect to Telegram API. Please check your bot token.")

        logger.info(f"[TelegramAdapter] Connected as @{me.get('username')} (ID: {me.get('id')})")

        self._client = httpx.AsyncClient(timeout=60.0)
        self._running = True

        await self._clear_pending_updates_fast()

        self._poll_task = asyncio.create_task(self._poll_updates())
        logger.info("[TelegramAdapter] Message polling started")
    
    async def _clear_pending_updates_fast(self):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self.base_url}/getUpdates",
                    params={"limit": 100},
                    timeout=3.0
                )
                result = response.json()

                if result.get("ok") and result.get("result"):
                    updates = result["result"]
                    if updates:
                        latest_id = updates[-1]["update_id"]
                        await client.get(
                            f"{self.base_url}/getUpdates",
                            params={"offset": latest_id + 1, "limit": 1},
                            timeout=3.0
                        )
                        logger.info(f"[TelegramAdapter] Cleared {len(updates)} pending updates")
        except Exception as e:
            logger.warning(f"[TelegramAdapter] Failed to clear pending updates: {e}")

    async def disconnect(self):
        """断开连接"""
        self._running = False

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._client:
            await self._client.aclose()
            self._client = None

        logger.info("[TelegramAdapter] Disconnected")

    async def update_config(self, bot_token: str = None, whitelist_user_ids: list = None,
                            whitelist_usernames: list = None, use_rich_messages: bool = None, **kwargs):
        """
        热更新配置

        Args:
            bot_token: 新的 Bot Token
            whitelist_user_ids: 新的白名单用户 ID 列表
            whitelist_usernames: 新的白名单用户名列表
            use_rich_messages: 是否使用 Rich Messages
        """
        if bot_token is not None:
            self.bot_token = bot_token
            self.base_url = f"https://api.telegram.org/bot{bot_token}"
            logger.info("[TelegramAdapter] Bot token updated")
        
        if whitelist_user_ids is not None:
            self.whitelist_user_ids = set(whitelist_user_ids)
            logger.info(f"[TelegramAdapter] Whitelist user IDs updated: {len(self.whitelist_user_ids)} users")
        
        if whitelist_usernames is not None:
            self.whitelist_usernames = set(u.lower() for u in whitelist_usernames)
            logger.info(f"[TelegramAdapter] Whitelist usernames updated: {len(self.whitelist_usernames)} users")

        if use_rich_messages is not None:
            self._use_rich_messages = use_rich_messages
            logger.info(f"[TelegramAdapter] Rich Messages {'enabled' if use_rich_messages else 'disabled'}")

    async def send_message(self, target_id: str, content: str, message_type: str, **kwargs) -> bool:
        """
        发送消息到 Telegram

        Args:
            target_id: 聊天 ID (chat_id)
            content: 消息内容
            message_type: 消息类型 (private/group)
            **kwargs: 额外参数
                - reply_to_message_id: 回复的消息 ID
        """
        if self._use_rich_messages:
            ok = await self._send_rich_message(target_id, content, **kwargs)
            if ok:
                return True

        try:
            url = f"{self.base_url}/sendMessage"

            text, entities = self._format_message(content)

            if len(text) > 4000:
                message_parts = self._split_message(text, max_length=4000)
                reply_to = kwargs.get("reply_to_message_id")
                
                for i, part in enumerate(message_parts):
                    data = {
                        "chat_id": target_id,
                        "text": part,
                    }
                    thread_id = kwargs.get("thread_id")
                    if thread_id:
                        data["message_thread_id"] = int(thread_id)
                    
                    if reply_to and i == 0:
                        data["reply_to_message_id"] = reply_to
                    
                    async with httpx.AsyncClient() as client:
                        response = await client.post(url, json=data)
                        result = response.json()
                    
                    if not result.get("ok"):
                        error_msg = result.get('description', 'Unknown error')
                        logger.error(f"[TelegramAdapter] Failed to send message part {i+1}: {error_msg}")
                        return False
                    
                    if i < len(message_parts) - 1:
                        await asyncio.sleep(0.5)
                
                logger.debug(f"[TelegramAdapter] Long message sent to {target_id} ({len(message_parts)} parts, no entities)")
            else:
                data = {
                    "chat_id": target_id,
                    "text": text,
                    "entities": entities,
                }
                thread_id = kwargs.get("thread_id")
                if thread_id:
                    data["message_thread_id"] = int(thread_id)
                
                reply_to = kwargs.get("reply_to_message_id")
                if reply_to:
                    data["reply_to_message_id"] = reply_to
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=data)
                    result = response.json()
                
                if not result.get("ok"):
                    error_msg = result.get('description', 'Unknown error')
                    logger.error(f"[TelegramAdapter] Failed to send message: {error_msg}")
                    logger.debug(f"[TelegramAdapter] Failed text preview: {text[:200]}...")
                    return False
                
                logger.debug(f"[TelegramAdapter] Message sent to {target_id} with {len(entities)} entities")
            
            return True

        except Exception as e:
            logger.error(f"[TelegramAdapter] Error sending message: {e}")
            return False

    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "", **kwargs) -> bool:
        """发送图片"""
        try:
            url = f"{self.base_url}/sendPhoto"
            
            caption_text, caption_entities = self._format_message(caption) if caption else ("", [])
            
            data = {
                "chat_id": chat_id,
                "caption": caption_text,
            }
            if caption_entities:
                data["caption_entities"] = caption_entities

            if "reply_to_message_id" in kwargs:
                data["reply_to_message_id"] = kwargs["reply_to_message_id"]
            if kwargs.get("thread_id"):
                data["message_thread_id"] = int(kwargs["thread_id"])

            def _read_file():
                with open(photo_path, "rb") as f:
                    return f.read()

            photo_data = await asyncio.to_thread(_read_file)
            files = {"photo": (os.path.basename(photo_path), photo_data)}

            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, files=files)
                result = response.json()

            return result.get("ok", False)

        except Exception as e:
            logger.error(f"[TelegramAdapter] Error sending photo: {e}")
            return False

    async def send_document(self, chat_id: str, document_path: str, caption: str = "", **kwargs) -> bool:
        """发送文件"""
        try:
            url = f"{self.base_url}/sendDocument"
            
            caption_text, caption_entities = self._format_message(caption) if caption else ("", [])
            
            data = {
                "chat_id": chat_id,
                "caption": caption_text,
            }
            if caption_entities:
                data["caption_entities"] = caption_entities

            if "reply_to_message_id" in kwargs:
                data["reply_to_message_id"] = kwargs["reply_to_message_id"]
            if kwargs.get("thread_id"):
                data["message_thread_id"] = int(kwargs["thread_id"])

            def _read_file():
                with open(document_path, "rb") as f:
                    return f.read()

            document_data = await asyncio.to_thread(_read_file)
            files = {"document": (os.path.basename(document_path), document_data)}

            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, files=files)
                result = response.json()

            return result.get("ok", False)

        except Exception as e:
            logger.error(f"[TelegramAdapter] Error sending document: {e}")
            return False

    async def _get_me(self) -> Optional[Dict[str, Any]]:
        """获取 Bot 信息"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/getMe")
                result = response.json()
                if result.get("ok"):
                    return result["result"]
                return None
        except Exception as e:
            logger.error(f"[TelegramAdapter] Error getting bot info: {e}")
            return None

    async def _poll_updates(self):
        """长轮询获取消息更新"""
        while self._running:
            try:
                params = {"timeout": 30}
                if self._last_update_id:
                    params["offset"] = self._last_update_id + 1

                async with httpx.AsyncClient(timeout=40.0) as client:
                    response = await client.get(
                        f"{self.base_url}/getUpdates",
                        params=params
                    )
                    updates = response.json()

                if not updates.get("ok"):
                    logger.error(f"[TelegramAdapter] getUpdates failed: {updates}")
                    await asyncio.sleep(5)
                    continue

                for update in updates.get("result", []):
                    self._last_update_id = update["update_id"]

                    await self._handle_update(update)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TelegramAdapter] Poll error: {e}")
                await asyncio.sleep(5)

    async def _handle_update(self, update: Dict[str, Any]):
        """处理单个更新"""
        message = update.get("message")
        if not message:
            return

        chat = message.get("chat", {})
        user_info = message.get("from", {})
        chat_id = str(chat.get("id"))
        user_id = str(user_info.get("id", ""))
        user_name = user_info.get("first_name", "Unknown")

        if user_info.get("is_bot", False):
            logger.debug(f"[TelegramAdapter] Ignoring message from bot (self): {message.get('message_id')}")
            return

        if not self._is_user_allowed(user_info):
            username = user_info.get("username", "N/A")
            logger.warning(f"[TelegramAdapter] Access denied for user {user_id} (@{username})")
            await self._send_denied_message(chat_id)
            return

        if chat.get("type") in ["group", "supergroup"]:
            message_type = "group"
            group_id = chat_id
        else:
            message_type = "private"
            group_id = None

        if "text" in message:
            content = message["text"]
            logger.info(f"[TelegramAdapter] Text message from {user_name}: {content[:50]}...")

        elif "photo" in message:
            # 处理图片
            content = await self._handle_photo(message, user_name)

        elif "document" in message:
            # 处理文件
            content = await self._handle_document(message, user_name)

        elif "animation" in message:
            content = await self._handle_document(message, user_name, key="animation")

        elif "video_note" in message:
            content = await self._handle_document(message, user_name, key="video_note")

        elif "voice" in message:
            content = "[Voice message]"

        elif "video" in message:
            content = "[Video message]"

        elif "sticker" in message:
            content = "[Sticker]"

        else:
            logger.debug(f"[TelegramAdapter] Ignoring service/unsupported message: {sorted(message.keys())[:6]}")
            return

        thread_id = message.get("message_thread_id")
        thread_id = str(thread_id) if thread_id else None

        unified_msg = UnifiedMessage(
            platform=self.platform_name,
            user_id=user_id,
            content=content,
            message_type=message_type,
            group_id=group_id,
            channel_id=chat_id,
            thread_id=thread_id,
            raw=message
        )

        self._dispatch(unified_msg)

    async def _handle_photo(self, message: Dict[str, Any], user_name: str) -> str:
        """处理图片消息 - 不自动下载，仅返回文件信息"""
        photos = message.get("photo", [])
        if not photos:
            return "[Photo]"

        photo = photos[-1]  
        file_id = photo.get("file_id")
        file_size = photo.get("file_size", 0)
        caption = message.get("caption", "")

        logger.info(f"[TelegramAdapter] Photo from {user_name}: {file_size/1024:.1f} KB")

        if file_size > MAX_FILE_SIZE:
            return f"[Photo - {file_size/1024/1024:.1f}MB, exceeds limit]"

        file_info = f"\n📎 **图片信息**：\n"
        file_info += f"  - 文件ID: {file_id}\n"
        file_info += f"  - 大小: {file_size} bytes\n"
        file_info += f"\n💡 使用 telegram_files.download 下载图片\n"

        return f"[Photo]{file_info}" + (f" Caption: {caption}" if caption else "")

    async def _handle_document(self, message: Dict[str, Any], user_name: str, key: str = "document") -> str:
        """处理文件消息 - 不自动下载，仅返回文件信息"""
        document = message.get(key, {})
        file_id = document.get("file_id")
        file_name = document.get("file_name") or {
            "animation": "animation.mp4",
            "video_note": "video_note.mp4",
        }.get(key, "file.dat")
        file_size = document.get("file_size", 0)
        mime_type = document.get("mime_type", "unknown")

        logger.info(f"[TelegramAdapter] Document from {user_name}: {file_name} ({file_size/1024:.1f} KB)")

        if file_size > MAX_FILE_SIZE:
            return f"[File: {file_name} - {file_size/1024/1024:.1f}MB, exceeds limit]"

        file_info = f"\n📎 **文件信息**：\n"
        file_info += f"  - 文件名: {file_name}\n"
        file_info += f"  - 文件ID: {file_id}\n"
        file_info += f"  - 类型: {mime_type}\n"
        file_info += f"  - 大小: {file_size} bytes\n"
        file_info += f"\n💡 使用 telegram_files.download 下载文件\n"

        return f"[File: {file_name}]{file_info}" + (f" Caption: {message.get('caption', '')}" if message.get('caption') else "")

    async def _download_file(self, file_id: str, save_name: str) -> Optional[str]:
        """下载文件到本地"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/getFile",
                    params={"file_id": file_id}
                )
                result = response.json()

            if not result.get("ok"):
                logger.error(f"[TelegramAdapter] Failed to get file path: {result}")
                return None

            file_path = result["result"]["file_path"]

            download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            save_path = self.download_dir / save_name

            async with httpx.AsyncClient() as client:
                async with client.stream("GET", download_url) as response:
                    response.raise_for_status()
                    with open(save_path, "wb") as f:
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)

            logger.info(f"[TelegramAdapter] File downloaded: {save_path}")
            return str(save_path)

        except Exception as e:
            logger.error(f"[TelegramAdapter] Error downloading file: {e}")
            return None

    async def _send_denied_message(self, chat_id: str):
        """发送拒绝访问消息"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": "⛔ You don't have permission to use this bot."
            }
            async with httpx.AsyncClient() as client:
                await client.post(url, json=data)
        except Exception as e:
            logger.error(f"[TelegramAdapter] Error sending denied message: {e}")

    def _get_source_label(self, message) -> str:
        """获取来源标签"""
        if message.message_type == "group":
            return f"Telegram 群组（ID：{message.group_id}）"
        return f"Telegram 私聊（User ID：{message.user_id}）"

    def _get_platform_tips(self) -> str:
        return ""

    def extract_file_info(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_data, dict):
            return None

        message = raw_data

        if "photo" in message:
            photos = message["photo"]
            if not photos or not isinstance(photos, list):
                return None
            photo = photos[-1]
            if not isinstance(photo, dict):
                return None
            return {
                "filename": f"photo_{photo.get('file_unique_id', 'unknown')}.jpg",
                "url": photo.get("file_id"),  
                "size": photo.get("file_size", 0),
                "mime_type": "image/jpeg"
            }

        if "document" in message:
            doc = message["document"]
            if not isinstance(doc, dict):
                return None
            return {
                "filename": doc.get("file_name", "file.dat"),
                "url": doc.get("file_id"), 
                "size": doc.get("file_size", 0),
                "mime_type": doc.get("mime_type", "application/octet-stream")
            }

        return None

    def is_file_only_message(self, message: UnifiedMessage) -> bool:
        """
        判断 Telegram 消息是否是纯文件消息
        """
        raw_data = message.raw or {}
        if not isinstance(raw_data, dict):
            return False
        has_file = "photo" in raw_data or "document" in raw_data
        if not has_file:
            return False
        caption = raw_data.get("caption", "").strip()
        return not caption
