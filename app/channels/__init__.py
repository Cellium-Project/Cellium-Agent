# -*- coding: utf-8 -*-
"""
app.channels - 多平台消息通道模块
"""

from .base import UnifiedMessage, ChannelAdapter, BaseChannelConfig
from .channel_manager import ChannelManager
from .qq_adapter import QQAdapter
from .qq_channel_config import QQChannelConfig
from .telegram_adapter import TelegramAdapter
from .telegram_channel_config import TelegramChannelConfig
from .feishu_adapter import FeishuAdapter
from .feishu_channel_config import FeishuChannelConfig

__all__ = [
    "UnifiedMessage",
    "ChannelAdapter",
    "BaseChannelConfig",
    "ChannelManager",
    "QQAdapter",
    "QQChannelConfig",
    "TelegramAdapter",
    "TelegramChannelConfig",
    "FeishuAdapter",
    "FeishuChannelConfig",
    "register_all_channels",
]

CHANNEL_REGISTRY = {
    "qq": {
        "adapter_cls": QQAdapter,
        "config_cls": QQChannelConfig,
        "factory": lambda config: QQAdapter(
            app_id=config.get_app_id(),
            app_secret=config.get_app_secret(),
        ),
    },
    "telegram": {
        "adapter_cls": TelegramAdapter,
        "config_cls": TelegramChannelConfig,
        "factory": lambda config: TelegramAdapter(
            bot_token=config.get_bot_token(),
            whitelist_user_ids=config.get_whitelist_user_ids(),
            whitelist_usernames=config.get_whitelist_usernames(),
        ),
    },
    "feishu": {
        "adapter_cls": FeishuAdapter,
        "config_cls": FeishuChannelConfig,
        "factory": lambda config: FeishuAdapter(config=config),
    },
}


def register_all_channels(logger=None):
    """
    从配置文件自动注册所有通道适配器
    
    Args:
        logger: 可选的日志器
        
    Returns:
        list: 已注册的通道名称列表
    """
    channel_mgr = ChannelManager.get_instance()
    registered = []
    
    for platform, info in CHANNEL_REGISTRY.items():
        config_cls = info["config_cls"]
        factory = info["factory"]
        
        try:
            config = config_cls()
            
            if not config.should_auto_start():
                if logger:
                    logger.warning(f"[Channel] {platform} 通道未启用或凭证缺失，跳过加载")
                continue
            
            if channel_mgr.get_adapter(platform):
                if logger:
                    logger.info(f"[Channel] {platform} 适配器已存在，跳过注册")
                registered.append(platform)
                continue
            
            adapter = factory(config)
            channel_mgr.register_adapter(adapter)
            registered.append(platform)
            
            if logger:
                logger.info(f"[Channel] {platform} 适配器已注册")
                
        except ImportError as e:
            if logger:
                logger.warning(f"[Channel] {platform} 适配器加载失败: {e}")
        except Exception as e:
            if logger:
                logger.error(f"[Channel] {platform} 适配器注册失败: {e}")
    
    return registered
