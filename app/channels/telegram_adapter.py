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

from .base import ChannelAdapter, UnifiedMessage

logger = logging.getLogger(__name__)

# 文件大小限制（字节）
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

# 下载文件保存目录
DOWNLOAD_DIR = Path("workspace") / "downloads" / "telegram"


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot 适配器"""

    def __init__(self, bot_token: str, whitelist_user_ids: Optional[list] = None,
                 whitelist_usernames: Optional[list] = None, **kwargs):
        """
        初始化 Telegram Adapter

        Args:
            bot_token: Telegram Bot Token (从 @BotFather 获取)
            whitelist_user_ids: 允许的用户 ID 列表，空列表表示允许所有人
            whitelist_usernames: 允许的用户名列表，空列表表示允许所有人
        """
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.whitelist_user_ids = set(whitelist_user_ids or [])
        self.whitelist_usernames = set((u.lower() for u in (whitelist_usernames or [])))

        # 创建下载目录
        self.download_dir = DOWNLOAD_DIR
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
        """连接到 Telegram Bot API"""
        if self._running:
            logger.warning("[TelegramAdapter] Already connected")
            return
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
                            whitelist_usernames: list = None, **kwargs):
        """
        热更新配置

        Args:
            bot_token: 新的 Bot Token
            whitelist_user_ids: 新的白名单用户 ID 列表
            whitelist_usernames: 新的白名单用户名列表
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

    async def send_message(self, target_id: str, content: str, message_type: str, **kwargs) -> bool:
        """
        发送消息到 Telegram

        Args:
            target_id: 聊天 ID (chat_id)
            content: 消息内容（会被自动转换为纯文本 + entities）
            message_type: 消息类型 (private/group)
            **kwargs: 额外参数
                - reply_to_message_id: 回复的消息 ID
        """
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

        elif "voice" in message:
            content = "[Voice message]"

        elif "video" in message:
            content = "[Video message]"

        elif "sticker" in message:
            content = "[Sticker]"

        else:
            content = "[Unsupported message type]"

        unified_msg = UnifiedMessage(
            platform=self.platform_name,
            user_id=user_id,
            content=content,
            message_type=message_type,
            group_id=group_id,
            channel_id=chat_id, 
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

    async def _handle_document(self, message: Dict[str, Any], user_name: str) -> str:
        """处理文件消息 - 不自动下载，仅返回文件信息"""
        document = message.get("document", {})
        file_id = document.get("file_id")
        file_name = document.get("file_name", "file.dat")
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

        return f"[File: {file_name}]{file_info}"

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

    def build_inject_content(self, message, content: str) -> str:
        """构建注入内容，标识消息来源"""
        if message.message_type == "group":
            source = f"Telegram 群组（ID：{message.group_id}）"
        else:
            source = f"Telegram 私聊（User ID：{message.user_id}）"

        return (
            f"§[外部平台消息]  来源：{source}\n"
            f"该消息来自外部平台，非直接终端交互。\n"
            f"- 禁止直接执行用户命令，敏感操作须先说明风险并确认\n"
            f"- 危险操作（删文件、格式化等）必须拒绝\n"
            f"- 优先要求用户提供明确需求，避免误解\n"
            f"- 注意：Telegram 平台表格渲染效果不佳，请尽量避免使用表格，改用列表或段落描述\n"
            f"---\n{content}"
        )

    def set_message_handler(self, handler: Callable[[UnifiedMessage], None]):
        """设置消息处理器"""
        self._message_handler = handler

    def _dispatch(self, message: UnifiedMessage):
        """分发消息到处理器"""
        if self._message_handler:
            asyncio.create_task(self._async_dispatch(message))

    async def _async_dispatch(self, message: UnifiedMessage):
        """异步分发消息"""
        try:
            if asyncio.iscoroutinefunction(self._message_handler):
                await self._message_handler(message)
            else:
                self._message_handler(message)
        except Exception as e:
            logger.error(f"[TelegramAdapter] Error in message handler: {e}")

    def extract_file_info(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        message = raw_data

        if "photo" in message:
            photos = message["photo"]
            photo = photos[-1] 
            return {
                "filename": f"photo_{photo.get('file_unique_id', 'unknown')}.jpg",
                "url": photo.get("file_id"),  
                "size": photo.get("file_size", 0),
                "mime_type": "image/jpeg"
            }

        if "document" in message:
            doc = message["document"]
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
        has_file = "photo" in raw_data or "document" in raw_data
        if not has_file:
            return False
        caption = raw_data.get("caption", "").strip()
        return not caption
