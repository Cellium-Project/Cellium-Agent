# -*- coding: utf-8 -*-
"""app.channels - 多平台消息通道模块"""

from .base import UnifiedMessage, ChannelAdapter, BaseChannelConfig
from .channel_manager import ChannelManager

import importlib

_LAZY_EXPORTS = {
    "QQAdapter": "app.channels.qq",
    "QQChannelConfig": "app.channels.qq",
    "TelegramAdapter": "app.channels.telegram",
    "TelegramChannelConfig": "app.channels.telegram",
    "FeishuAdapter": "app.channels.feishu",
    "FeishuChannelConfig": "app.channels.feishu",
    "WeixinAdapter": "app.channels.weixin",
    "WeixinChannelConfig": "app.channels.weixin",
}

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
    "WeixinAdapter",
    "WeixinChannelConfig",
    "register_all_channels",
]


def __getattr__(name):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))


def register_all_channels(logger=None):
    """
    从配置文件自动注册所有通道适配器

    Args:
        logger: 可选的日志器

    Returns:
        list: 已注册的通道名称列表
    """
    from .channel_registry import build_channel_registry

    channel_mgr = ChannelManager.get_instance()
    registry = build_channel_registry()
    registered = []

    for platform, info in registry.items():
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
