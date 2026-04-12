# -*- coding: utf-8 -*-
"""
app.channels - 多平台消息通道模块
"""

from .base import UnifiedMessage, ChannelAdapter
from .channel_manager import ChannelManager
from .qq_adapter import QQAdapter

__all__ = ["UnifiedMessage", "ChannelAdapter", "ChannelManager", "QQAdapter"]
