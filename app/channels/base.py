# -*- coding: utf-8 -*-
"""
Channel Base - 多平台消息抽象层
定义统一消息格式和通道适配器接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any
import logging
import uuid

logger = logging.getLogger(__name__)


@dataclass
class UnifiedMessage:
    platform: str
    user_id: str
    content: str
    message_type: str
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    group_id: Optional[str] = None
    channel_id: Optional[str] = None
    guild_id: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    @property
    def session_id(self) -> str:
        if self.message_type == "group" and self.group_id:
            return f"{self.platform}:group:{self.group_id}"
        if self.message_type == "guild" and self.channel_id:
            guild_part = self.guild_id or "unknown"
            return f"{self.platform}:guild:{guild_part}:{self.channel_id}"
        return f"{self.platform}:{self.user_id}"

    def to_agent_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "user_id": self.user_id,
            "content": self.content,
            "message_type": self.message_type,
            "msg_id": self.msg_id,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
        }


class ChannelAdapter(ABC):
    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass

    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def disconnect(self):
        pass

    @abstractmethod
    async def send_message(self, target_id: str, content: str, message_type: str, **kwargs) -> bool:
        pass

    def build_inject_content(self, message: UnifiedMessage, content: str) -> str:
        return content

    def set_message_handler(self, handler: Callable[[UnifiedMessage], None]):
        self._message_handler = handler

    def _dispatch(self, message: UnifiedMessage):
        if hasattr(self, '_message_handler') and self._message_handler:
            self._message_handler(message)

    def extract_file_info(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        从原始消息数据中提取文件信息
        各平台 Adapter 可重写此方法以支持文件消息

        Returns:
            {
                "filename": str,
                "url": str (optional),
                "size": int (optional),
                "mime_type": str (optional),
            }
            或 None 表示不是文件消息
        """
        return None

    def is_file_only_message(self, message: UnifiedMessage) -> bool:
        return not message.content.strip()

    async def handle_file_message(self, message: UnifiedMessage) -> bool:
        """
        处理文件消息，保存到 session
        返回 True 表示已处理，False 表示不是文件消息
        """
        raw_data = message.raw or {}
        file_info = self.extract_file_info(raw_data)

        if not file_info:
            return False

        try:
            session_id = message.session_id
            from app.agent.loop.session_manager import get_session_manager
            session_mgr = get_session_manager()
            session_info = session_mgr.get_or_create(session_id)

            # 保存文件信息到 session 的临时存储
            if not hasattr(session_info, "pending_files"):
                session_info.pending_files = []
            session_info.pending_files.append({
                "filename": file_info.get("filename", "unknown"),
                "url": file_info.get("url"),
                "size": file_info.get("size", 0),
                "mime_type": file_info.get("mime_type"),
                "msg_id": message.msg_id,
                "platform": self.platform_name,
            })

            return True
        except Exception as e:
            logger.error(f"[{self.platform_name}] 文件消息处理失败: {e}")
            return False
