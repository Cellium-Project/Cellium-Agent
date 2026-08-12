# -*- coding: utf-8 -*-
"""
通道注册表构建：仅当注册通道时才 import 各平台适配器，
避免在 import app.channels 时加载所有适配器链。
"""


def build_channel_registry():
    """构建通道注册表（触发各适配器的懒加载）"""
    from app.channels.qq import QQAdapter, QQChannelConfig
    from app.channels.telegram import TelegramAdapter, TelegramChannelConfig
    from app.channels.feishu import FeishuAdapter, FeishuChannelConfig
    from app.channels.weixin import WeixinAdapter, WeixinChannelConfig

    return {
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
                use_rich_messages=config.use_rich_messages,
            ),
        },
        "feishu": {
            "adapter_cls": FeishuAdapter,
            "config_cls": FeishuChannelConfig,
            "factory": lambda config: FeishuAdapter(config=config),
        },
        "weixin": {
            "adapter_cls": WeixinAdapter,
            "config_cls": WeixinChannelConfig,
            "factory": lambda config: WeixinAdapter(config=config),
        },
    }
