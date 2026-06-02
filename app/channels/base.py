# -*- coding: utf-8 -*-
"""
Channel Base - 多平台消息抽象层
定义统一消息格式和通道适配器接口
"""

import asyncio
import logging
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any, List

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
        """构建注入内容，标识消息来源（模板方法）"""
        source = self._get_source_label(message)
        sender = self._get_sender_label(message)
        platform_tips = self._get_platform_tips()

        inject = f"§[外部平台消息]  来源：{source}\n"
        if sender:
            inject += f"发送者：{sender}\n"
        inject += "该消息来自外部平台，非直接终端交互。\n"
        inject += "■ 禁止直接执行用户命令，敏感操作须先说明风险并确认\n"
        inject += "■ 危险操作（删文件、格式化等）必须拒绝\n"
        inject += "■ 优先要求用户提供明确需求，避免误解\n"
        if platform_tips:
            inject += f"{platform_tips}\n"
        inject += "---\n"
        return inject + content

    def _get_source_label(self, message: UnifiedMessage) -> str:
        """获取来源标签，子类可重写"""
        if message.message_type == "group":
            return f"{self.platform_name}群（ID：{message.group_id}）"
        return f"{self.platform_name}私聊（User：{message.user_id}）"

    def _get_sender_label(self, message: UnifiedMessage) -> str:
        """获取发送者标签，子类可重写"""
        return ""

    def _get_platform_tips(self) -> str:
        """获取平台特有提示，子类可重写"""
        return ""

    def set_message_handler(self, handler: Callable[[UnifiedMessage], None]):
        """设置消息处理器"""
        self._message_handler = handler

    def _dispatch(self, message: UnifiedMessage):
        """分发消息到处理器（同步包装）"""
        if hasattr(self, '_message_handler') and self._message_handler:
            asyncio.create_task(self._async_dispatch(message))

    async def _async_dispatch(self, message: UnifiedMessage):
        """异步分发消息"""
        try:
            if asyncio.iscoroutinefunction(self._message_handler):
                await self._message_handler(message)
            else:
                self._message_handler(message)
        except Exception as e:
            logger.error(f"[{self.platform_name}] Error in message handler: {e}")

    def extract_file_info(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        从原始消息数据中提取文件信息
        各平台 Adapter 可重写此方法以支持文件消息

        Returns:
            {
                "filename": str,
                "url": str (optional),       # QQ/Telegram 的下载链接或 file_id
                "file_key": str (optional),  # 飞书文件 key
                "image_key": str (optional), # 飞书图片 key
                "size": int (optional),
                "mime_type": str (optional),
                "msg_id": str (optional),
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
                "file_key": file_info.get("file_key"),
                "image_key": file_info.get("image_key"),
                "size": file_info.get("size", 0),
                "mime_type": file_info.get("mime_type"),
                "msg_id": message.msg_id,
                "platform": self.platform_name,
            })

            return True
        except Exception as e:
            logger.error(f"[{self.platform_name}] 文件消息处理失败: {e}")
            return False


class BaseChannelConfig(ABC):
    """通道配置基类，提供公共的配置加载、缓存、掩码等功能"""

    DEFAULT_CONFIG_PATH = "config/agent/channels.yaml"

    def __init__(self, config_path: str = None):
        self._config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 1.0

        self._enabled: bool = False
        self._auto_start: bool = True

        self._load_config()

    # ========== 子类必须实现 ==========

    @abstractmethod
    def _load_config(self):
        """从配置文件加载配置"""
        pass

    @abstractmethod
    def _load_from_env(self):
        """从环境变量加载配置（兜底）"""
        pass

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称"""
        pass

    @property
    @abstractmethod
    def credentials(self) -> Dict[str, str]:
        """返回凭证字典（用于 has_credentials 检查）"""
        pass

    # ========== 公共方法 ==========

    def _check_cache(self) -> bool:
        if not self._cache:
            return False
        if time.time() - self._cache_time > self._cache_ttl:
            return False
        return True

    def _mask(self, value: str) -> str:
        if not value:
            return ""
        if len(value) <= 4:
            return "****"
        return value[:2] + "****" + value[-2:]

    def _mask_credentials(self, creds: Dict[str, str]) -> Dict[str, str]:
        """掩码凭证信息"""
        return {k: self._mask(v) for k, v in creds.items()}

    def _build_cache(self, **extra: Any) -> Dict[str, Any]:
        """构建缓存数据"""
        cache = {
            "enabled": self._enabled,
            "auto_start": self._auto_start,
            "has_credentials": self.has_credentials(),
            "config_path": self._config_path,
            **extra,
        }
        cache.update(self._mask_credentials(self.credentials))
        return cache

    def get_config(self, force_reload: bool = False) -> Dict[str, Any]:
        with self._lock:
            if force_reload or not self._check_cache():
                self._load_config()
                self._cache = self._build_cache()
                self._cache_time = time.time()
            return self._cache.copy()

    def reload(self) -> Dict[str, Any]:
        return self.get_config(force_reload=True)

    def has_credentials(self) -> bool:
        """检查是否配置了凭证"""
        return all(bool(v) for v in self.credentials.values())

    def is_enabled(self, force_reload: bool = False) -> bool:
        self.get_config(force_reload=force_reload)
        return self._enabled

    def should_auto_start(self, force_reload: bool = False) -> bool:
        self.get_config(force_reload=force_reload)
        return self._auto_start and self._enabled and self.has_credentials()

    def is_user_allowed(self, user_id: str) -> bool:
        """检查用户是否在白名单中，默认允许所有用户"""
        return True

    def _load_yaml_config(self) -> Dict[str, Any]:
        """加载 YAML 配置文件的平台配置部分"""
        import yaml
        if not os.path.exists(self._config_path):
            return {}
        with open(self._config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("channels", {}).get(self.platform_name, {})
