# -*- coding: utf-8 -*-
"""
Channel Base - 多平台消息抽象层
定义统一消息格式和通道适配器接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any
import uuid


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
    async def send_message(self, user_id: str, content: str, message_type: str, **kwargs) -> bool:
        pass

    def build_inject_content(self, message: UnifiedMessage, content: str) -> str:
        return content

    def set_message_handler(self, handler: Callable[[UnifiedMessage], None]):
        self._message_handler = handler

    def _dispatch(self, message: UnifiedMessage):
        if hasattr(self, '_message_handler') and self._message_handler:
            self._message_handler(message)
