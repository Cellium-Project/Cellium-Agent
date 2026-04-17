# -*- coding: utf-8 -*-
"""
app.channels - 多平台消息通道模块
"""

from .base import UnifiedMessage, ChannelAdapter
from .channel_manager import ChannelManager
from .qq_adapter import QQAdapter
from .qq_channel_config import QQChannelConfig
from .telegram_adapter import TelegramAdapter
from .telegram_channel_config import TelegramChannelConfig

__all__ = [
    "UnifiedMessage",
    "ChannelAdapter",
    "ChannelManager",
    "QQAdapter",
    "QQChannelConfig",
    "TelegramAdapter",
    "TelegramChannelConfig"
]
