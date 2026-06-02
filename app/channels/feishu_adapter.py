# -*- coding: utf-8 -*-
"""飞书通道适配器 - 将飞书 Bot 适配为统一 ChannelAdapter 接口"""

import asyncio
import json
import logging
import re
from typing import Optional, Dict, Any, Callable, List

from .base import ChannelAdapter, UnifiedMessage
from .feishu_channel_config import FeishuChannelConfig

logger = logging.getLogger(__name__)

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
    )
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    logger.warning("[FeishuAdapter] lark-oapi 未安装，飞书通道不可用。安装: pip install lark-oapi")


class FeishuAdapter(ChannelAdapter):
    """飞书 Bot 适配器"""
    
    def __init__(self, config: FeishuChannelConfig = None, **kwargs):
        if not LARK_AVAILABLE:
            raise ImportError("需要安装 lark-oapi: pip install lark-oapi")
        
        self._config = config or FeishuChannelConfig()
        self._client: Optional[lark.Client] = None
        self._ws_client: Optional[lark.ws.Client] = None
        self._running = False
        self._message_handler: Optional[Callable[[UnifiedMessage], None]] = None
        self._event_task: Optional[asyncio.Task] = None
        
        logger.info("[FeishuAdapter] 初始化完成")
    
    @property
    def platform_name(self) -> str:
        return "feishu"
    
    async def connect(self):
        """连接飞书 WebSocket"""
        if self._running:
            await self.disconnect()
        
        if not self._config.enabled:
            logger.info("[FeishuAdapter] 通道未启用")
            return
        
        try:
            self._client = self._create_client()
            self._running = True
            
            self._event_task = asyncio.create_task(self._run_event_loop())
            
            logger.info("[FeishuAdapter] 已连接飞书 WebSocket")
        except Exception as e:
            logger.error(f"[FeishuAdapter] 连接失败: {e}")
            raise
    
    async def disconnect(self):
        """断开连接"""
        self._running = False
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        self._client = None
        self._ws_client = None
        logger.info("[FeishuAdapter] 已断开")

    async def update_config(self, app_id: str = None, app_secret: str = None,
                            whitelist_users: list = None, **kwargs):
        """
        热更新配置

        Args:
            app_id: 新的 App ID
            app_secret: 新的 App Secret
            whitelist_users: 新的白名单用户列表
        """
        if app_id is not None:
            self._config._app_id = app_id

        if app_secret is not None:
            self._config._app_secret = app_secret

        if whitelist_users is not None:
            self._config._whitelist_users = whitelist_users

        if app_id is not None or app_secret is not None:
            logger.info("[FeishuAdapter] 凭证已变更，准备重新连接...")
            await self.disconnect()
            await self.connect()
            logger.info("[FeishuAdapter] 重新连接完成")

    async def send_message(self, target_id: str, content: str,
                          message_type: str = "text", **kwargs) -> bool:
        """发送消息"""
        if not self._client:
            logger.error("[FeishuAdapter] 客户端未初始化")
            return False
        
        try:
            has_special_tags = '§[' in content
            has_markdown = any([
                '**' in content,
                '```' in content,
                '##' in content,
                '- [' in content,
                '|' in content and '---' in content,
                '> [' in content,
            ])
            
            if has_special_tags or has_markdown:
                converted_content = self._convert_special_tags(content)
                title = kwargs.get("title", "")
                return await self._send_markdown_card(target_id, converted_content, title)
            else:
                return await self._send_text(target_id, content)
        except Exception as e:
            logger.error(f"[FeishuAdapter] 发送消息失败: {e}")
            return False
    
    def _create_client(self) -> lark.Client:
        """创建 lark 客户端"""
        return lark.Client.builder() \
            .app_id(self._config.app_id) \
            .app_secret(self._config.app_secret) \
            .build()
    
    async def _run_event_loop(self):
        """运行事件循环（WebSocket 长连接）"""
        
        def handle_message(data: lark.im.v1.P2ImMessageReceiveV1):
            """消息接收回调"""
            self._handle_message(data)
        
        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(handle_message) \
            .build()
        
        self._ws_client = lark.ws.Client(
            self._config.app_id,
            self._config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.ERROR
        )
        
        while self._running:
            try:
                await asyncio.to_thread(self._ws_client.start)
            except Exception as e:
                logger.error(f"[FeishuAdapter] WebSocket 错误: {e}")
                if self._running:
                    await asyncio.sleep(5)
    
    def _handle_message(self, data):
        """处理消息事件"""
        try:
            data_dict = json.loads(lark.JSON.marshal(data))

            event = data_dict.get("event", {})
            message = event.get("message", {})
            sender = event.get("sender", {})

            msg_id = message.get("message_id")
            chat_id = message.get("chat_id")
            content = message.get("content", "")
            msg_type = message.get("message_type", "unknown")

            sender_id = sender.get("sender_id", {})
            user_id = sender_id.get("user_id", "unknown")
            open_id = sender_id.get("open_id", "unknown")
            sender_name = sender.get("sender_name") or "用户"

            # 处理文件/媒体类型消息
            if msg_type in ("file", "media", "image", "audio", "video"):
                text = self._build_file_message_content(message, msg_type)
            elif msg_type == "text":
                try:
                    text = json.loads(content).get("text", content)
                except:
                    text = content
            else:
                # 其他类型消息，尝试提取文本
                text = content

            if not self._config.is_user_allowed(open_id):
                logger.info(f"[FeishuAdapter] 用户 {open_id} 不在白名单中")
                return

            chat_type = message.get("chat_type", "")
            if chat_type:
                is_group = chat_type == "group"
            else:
                is_group = chat_id.startswith("oc_")

            logger.info(f"[FeishuAdapter] chat_id={chat_id} | chat_type={chat_type} | is_group={is_group} | msg_type={msg_type}")

            unified_msg = UnifiedMessage(
                platform=self.platform_name,
                user_id=open_id,
                content=text,
                message_type="group" if is_group else "private",
                msg_id=msg_id,
                group_id=chat_id if is_group else None,
                raw={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "sender_name": sender_name,
                    "msg_type": msg_type,
                    "content": content,
                    "message_id": msg_id,
                }
            )

            self._dispatch(unified_msg)

            logger.info(
                f"[FeishuAdapter] 收到消息 | sender={sender_name} | type={msg_type} | content={text[:50]}..."
            )

        except Exception as e:
            logger.error(f"[FeishuAdapter] 消息解析失败: {e}")

    def _build_file_message_content(self, message: dict, msg_type: str) -> str:
        """构建文件类型消息的内容"""
        content = message.get("content", "")

        try:
            content_json = json.loads(content)
        except:
            content_json = {}

        if msg_type == "file":
            filename = content_json.get("file_name", "unknown")
            file_key = content_json.get("file_key", "")
            file_size = content_json.get("file_size", 0)
            text = f"[用户发送了一个文件]\n文件名: {filename}\n大小: {file_size} bytes"
            if file_key:
                text += f"\nfile_key: {file_key}"
        elif msg_type == "image":
            image_key = content_json.get("image_key", "")
            text = f"[用户发送了一张图片]\nimage_key: {image_key}"
        elif msg_type == "audio":
            file_key = content_json.get("file_key", "")
            text = f"[用户发送了一个音频]\nfile_key: {file_key}"
        elif msg_type == "video":
            file_key = content_json.get("file_key", "")
            text = f"[用户发送了一个视频]\nfile_key: {file_key}"
        elif msg_type == "media":
            # media 类型可能是文件或图片
            file_key = content_json.get("file_key", "")
            image_key = content_json.get("image_key", "")
            filename = content_json.get("file_name", "unknown")
            if image_key:
                text = f"[用户发送了一张图片]\nimage_key: {image_key}"
            else:
                text = f"[用户发送了一个文件]\n文件名: {filename}\nfile_key: {file_key}"
        else:
            text = f"[用户发送了一个{msg_type}类型的消息]"

        return text

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
            logger.error(f"[FeishuAdapter] Error in message handler: {e}")
    
    async def _send_text(self, target_id: str, text: str) -> bool:
        """发送文本消息"""
        try:
            receive_id_type = "open_id" if target_id.startswith("ou_") else "chat_id"
            req = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(target_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()) \
                .build()
            
            resp = await asyncio.to_thread(
                self._client.im.v1.message.create,
                req
            )
            
            if resp.success():
                logger.debug(f"[FeishuAdapter] 发送文本成功")
                return True
            else:
                logger.error(f"[FeishuAdapter] 发送文本失败: {resp.msg}")
                return False
        except Exception as e:
            logger.error(f"[FeishuAdapter] 发送文本异常: {e}")
            return False
    
    async def _send_markdown_card(self, target_id: str, content: str, title: str) -> bool:
        """发送 Markdown 卡片"""
        try:
            card = self._markdown_to_feishu_card(content, title)
            receive_id_type = "open_id" if target_id.startswith("ou_") else "chat_id"
            
            req = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(target_id)
                    .msg_type("interactive")
                    .content(json.dumps(card))
                    .build()) \
                .build()
            
            resp = await asyncio.to_thread(
                self._client.im.v1.message.create,
                req
            )
            
            if resp.success():
                logger.debug(f"[FeishuAdapter] 发送卡片成功")
                return True
            else:
                logger.error(f"[FeishuAdapter] 发送卡片失败: {resp.msg}")
                return False
        except Exception as e:
            logger.error(f"[FeishuAdapter] 发送卡片异常: {e}")
            return False
    
    def _convert_special_tags(self, text: str) -> str:
        """将特殊标签转换为飞书彩色标记"""
        
        text = re.sub(
            r'§\[外部平台消息\]',
            '<font color="blue">🔔 外部平台消息</font>',
            text
        )
        
        text = re.sub(
            r'§\[工具调用\]',
            '<font color="orange">🔧 工具调用</font>',
            text
        )
        
        text = re.sub(
            r'§\[工具结果\]',
            '<font color="green">✅ 工具结果</font>',
            text
        )
        
        text = re.sub(
            r'§\[警告\]',
            '<font color="red">⚠️ 警告</font>',
            text
        )
        
        text = re.sub(
            r'§\[错误\]',
            '<font color="red">❌ 错误</font>',
            text
        )
        
        text = re.sub(
            r'§\[提示\]',
            '<font color="grey">💡 提示</font>',
            text
        )
        
        return text
    
    def _markdown_to_feishu_card(self, markdown_text: str, title: str = "") -> dict:
        """将 Markdown 文本转换为飞书卡片格式"""
        is_tool_card = any([
            '##### 🔧 正在调用' in markdown_text,
            '###### ✅' in markdown_text and '耗时' in markdown_text,
            '> 💭 **Thinking**' in markdown_text,
            '> ❌ **错误**' in markdown_text,
            '> [' in markdown_text and ']' in markdown_text,
            '⏰ **定时任务触发**' in markdown_text,
            '🔔 **组件事件触发**' in markdown_text,
        ])

        lines = markdown_text.split('\n')
        content_elements: List[Dict[str, Any]] = []

        current_section: List[str] = []
        in_code_block = False
        code_block_lines: List[str] = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith('```'):
                if in_code_block:

                    code_content = '\n'.join(code_block_lines)
                    content_elements.append({
                        "tag": "markdown",
                        "content": f"```\n{code_content}\n```",
                        "text_size": "notation" if is_tool_card else "normal"
                    })
                    code_block_lines = []
                    in_code_block = False
                else:
                    if current_section:
                        section_text = '\n'.join(current_section)
                        content_elements.append({
                            "tag": "markdown",
                            "content": section_text,
                            "text_size": "notation" if is_tool_card else "normal"
                        })
                        current_section = []
                    in_code_block = True
            else:
                if in_code_block:
                    code_block_lines.append(line)
                else:
                    current_section.append(line)

        if in_code_block and code_block_lines:
            code_content = '\n'.join(code_block_lines)
            content_elements.append({
                "tag": "markdown",
                "content": f"```\n{code_content}\n```",
                "text_size": "notation" if is_tool_card else "normal"
            })

        if current_section:
            section_text = '\n'.join(current_section)
            content_elements.append({
                "tag": "markdown",
                "content": section_text,
                "text_size": "notation" if is_tool_card else "normal"
            })

        if not content_elements:
            content_elements = [{
                "tag": "markdown",
                "content": markdown_text,
                "text_size": "notation" if is_tool_card else "normal"
            }]

        card = {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True
            },
            "body": {
                "elements": content_elements
            }
        }

        return card

    def _get_source_label(self, message) -> str:
        """获取来源标签"""
        if message.message_type == "group":
            return f"飞书群（ID：{message.group_id}）"
        return f"飞书私聊（User：{message.user_id}）"

    def _get_sender_label(self, message) -> str:
        """获取发送者标签"""
        if message.raw and isinstance(message.raw, dict):
            return message.raw.get("sender_name", "")
        return ""

    def extract_file_info(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从原始消息数据中提取文件信息"""
        if not raw_data:
            return None

        msg_type = raw_data.get("msg_type", "")

        if msg_type not in ("file", "media", "image", "audio", "video"):
            return None

        try:
            content = json.loads(raw_data.get("content", "{}"))
        except:
            content = {}

        if msg_type == "file" or msg_type == "media":
            return {
                "filename": content.get("file_name", "unknown"),
                "file_key": content.get("file_key"),
                "mime_type": content.get("file_type", "application/octet-stream"),
                "size": content.get("file_size", 0),
                "msg_id": raw_data.get("message_id"),
            }
        elif msg_type == "image":
            return {
                "filename": f"image_{content.get('image_key', 'unknown')}.jpg",
                "image_key": content.get("image_key"),
                "mime_type": "image/jpeg",
                "size": 0,
                "msg_id": raw_data.get("message_id"),
            }
        elif msg_type in ("audio", "video"):
            return {
                "filename": f"{msg_type}_{content.get('file_key', 'unknown')}",
                "file_key": content.get("file_key"),
                "mime_type": content.get("file_type", f"{msg_type}/unknown"),
                "size": content.get("file_size", 0),
                "msg_id": raw_data.get("message_id"),
            }

        return None

    def is_file_only_message(self, message: UnifiedMessage) -> bool:
        """
        判断飞书消息是否是纯文件消息
        """
        raw_data = message.raw or {}
        if not isinstance(raw_data, dict):
            return False

        msg_type = raw_data.get("msg_type", "")
        is_file = msg_type in ("file", "media", "image", "audio", "video")
        if not is_file:
            return False

        return True

    async def _get_token(self) -> str:
        """获取 tenant_access_token"""
        import httpx
        
        resp = await asyncio.to_thread(
            httpx.post,
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._config.app_id, "app_secret": self._config.app_secret}
        )
        result = resp.json()
        token = result.get("tenant_access_token")
        if not token:
            raise Exception(f"获取 token 失败: {result.get('msg', 'unknown')}")
        return token
    
    async def download_file(
        self,
        file_key: str,
        filename: Optional[str] = None,
        sub_dir: str = "downloads/feishu",
        timeout: int = 120,
        message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        下载飞书文件到本地

        Args:
            file_key: 文件的 file_key 或 image_key
            filename: 保存文件名（可选）
            sub_dir: 子目录
            timeout: 超时时间（秒）
            message_id: 消息ID（用户发送的文件/图片必须提供此参数）

        Returns:
            {"file_path": str, "file_size": int, "filename": str} 或 {"error": str}
        """
        try:
            import httpx
            from pathlib import Path

            is_image = file_key.startswith("img_")

            if not filename:
                if is_image:
                    filename = f"image_{file_key[4:12]}.jpg"
                elif file_key.startswith("file_"):
                    filename = f"file_{file_key[5:12]}"
                else:
                    filename = file_key.split("/")[-1] if "/" in file_key else f"file_{file_key[:8]}"

            save_dir = Path("workspace") / sub_dir
            save_dir.mkdir(parents=True, exist_ok=True)
            file_path = save_dir / filename

            token = await self._get_token()

            if message_id:
                if is_image:
                    # 图片下载 API
                    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=image"
                else:
                    # 文件下载 API
                    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
            else:
                return {"error": "下载用户发送的文件需要提供 message_id 参数。请使用 download_pending 命令自动获取所需信息。"}

            headers = {"Authorization": f"Bearer {token}"}
            
            logger.info(f"[FeishuAdapter] 开始下载文件: {file_key}")
            
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", url, headers=headers, timeout=timeout) as resp:
                    if resp.status_code != 200:
                        return {"error": f"下载失败: {resp.status_code}"}
                    
                    downloaded = 0
                    with open(file_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
            
            logger.info(f"[FeishuAdapter] 下载完成: {file_path}, 大小: {downloaded / 1024:.2f} KB")
            
            return {
                "file_path": str(file_path),
                "file_size": downloaded,
                "filename": filename
            }
            
        except Exception as e:
            logger.error(f"[FeishuAdapter] 下载失败: {e}")
            return {"error": str(e)}
    
    async def send_file_message(
        self,
        target_id: str,
        file_path: str,
        is_group: bool = True
    ) -> bool:
        """
        上传并发送文件消息

        Args:
            target_id: 群 Chat ID 或用户 Open ID
            file_path: 本地文件路径
            is_group: 是否是群聊

        Returns:
            是否发送成功
        """
        try:
            import httpx
            from pathlib import Path
            
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                logger.error(f"[FeishuAdapter] 文件不存在: {file_path}")
                return False
            
            token = await self._get_token()
            filename = file_path_obj.name
            
            logger.info(f"[FeishuAdapter] 上传文件: {filename}")
            
            with open(file_path, 'rb') as f:
                files = {
                    'file_type': (None, 'stream'),
                    'file_name': (None, filename),
                    'file': (filename, f, 'application/octet-stream')
                }
                
                resp = await asyncio.to_thread(
                    httpx.post,
                    "https://open.feishu.cn/open-apis/im/v1/files",
                    headers={"Authorization": f"Bearer {token}"},
                    files=files
                )
            
            if resp.status_code != 200:
                logger.error(f"[FeishuAdapter] 上传文件失败: {resp.status_code}")
                return False
            
            result = resp.json()
            if result.get("code", 0) != 0:
                logger.error(f"[FeishuAdapter] 上传文件失败: {result.get('msg', 'unknown')}")
                return False
            
            file_key = result["data"]["file_key"]
            logger.info(f"[FeishuAdapter] 文件上传成功: {file_key}")
            
            msg_resp = await asyncio.to_thread(
                httpx.post,
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id" if is_group else "open_id"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json={
                    "receive_id": target_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": file_key})
                }
            )
            
            if msg_resp.status_code == 200:
                msg_result = msg_resp.json()
                if msg_result.get("code", 0) == 0:
                    logger.info(f"[FeishuAdapter] 文件消息发送成功")
                    return True
                else:
                    logger.error(f"[FeishuAdapter] 发送文件消息失败: {msg_result.get('msg', 'unknown')}")
                    return False
            else:
                logger.error(f"[FeishuAdapter] 发送文件消息失败: {msg_resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"[FeishuAdapter] 发送文件消息失败: {e}")
            return False
    
    async def send_image_message(
        self,
        target_id: str,
        image_path: str,
        is_group: bool = True
    ) -> bool:
        """
        上传并发送图片消息

        Args:
            target_id: 群 Chat ID 或用户 Open ID
            image_path: 本地图片路径
            is_group: 是否是群聊

        Returns:
            是否发送成功
        """
        try:
            import httpx
            from pathlib import Path
            
            image_path_obj = Path(image_path)
            if not image_path_obj.exists():
                logger.error(f"[FeishuAdapter] 图片不存在: {image_path}")
                return False
            
            token = await self._get_token()
            filename = image_path_obj.name
            
            logger.info(f"[FeishuAdapter] 上传图片: {filename}")
            
            with open(image_path, 'rb') as f:
                files = {
                    'image_type': (None, 'message'),
                    'image': (filename, f, 'image/png')
                }
                
                resp = await asyncio.to_thread(
                    httpx.post,
                    "https://open.feishu.cn/open-apis/im/v1/images",
                    headers={"Authorization": f"Bearer {token}"},
                    files=files
                )
            
            if resp.status_code != 200:
                logger.error(f"[FeishuAdapter] 上传图片失败: {resp.status_code}")
                return False
            
            result = resp.json()
            if result.get("code", 0) != 0:
                logger.error(f"[FeishuAdapter] 上传图片失败: {result.get('msg', 'unknown')}")
                return False
            
            image_key = result["data"]["image_key"]
            logger.info(f"[FeishuAdapter] 图片上传成功: {image_key}")
            
            msg_resp = await asyncio.to_thread(
                httpx.post,
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id" if is_group else "open_id"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json={
                    "receive_id": target_id,
                    "msg_type": "image",
                    "content": json.dumps({"image_key": image_key})
                }
            )
            
            if msg_resp.status_code == 200:
                msg_result = msg_resp.json()
                if msg_result.get("code", 0) == 0:
                    logger.info(f"[FeishuAdapter] 图片消息发送成功")
                    return True
                else:
                    logger.error(f"[FeishuAdapter] 发送图片消息失败: {msg_result.get('msg', 'unknown')}")
                    return False
            else:
                logger.error(f"[FeishuAdapter] 发送图片消息失败: {msg_resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"[FeishuAdapter] 发送图片消息失败: {e}")
            return False
