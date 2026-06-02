# -*- coding: utf-8 -*-
"""
TelegramChannelConfig - Telegram 通道配置
"""

import os
from typing import Dict, Any, List, Optional

from .base import BaseChannelConfig


class TelegramChannelConfig(BaseChannelConfig):
    """Telegram通道配置类"""

    def __init__(self, config_path: str = None):
        self._bot_token: Optional[str] = None
        self._whitelist_user_ids: List[int] = []
        self._whitelist_usernames: List[str] = []
        super().__init__(config_path)

    @property
    def platform_name(self) -> str:
        return "telegram"

    @property
    def credentials(self) -> Dict[str, str]:
        return {"bot_token": self._bot_token or ""}

    def _load_config(self):
        channel_cfg = self._load_yaml_config()
        self._bot_token = channel_cfg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
        self._whitelist_user_ids = channel_cfg.get("whitelist_user_ids", []) or []
        self._whitelist_usernames = channel_cfg.get("whitelist_usernames", []) or []
        self._enabled = channel_cfg.get("enabled", False)
        self._auto_start = channel_cfg.get("auto_start", True)

        # 如果没有配置文件，从环境变量加载
        if not channel_cfg and not self._bot_token:
            self._load_from_env()

    def _load_from_env(self):
        self._bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self._whitelist_user_ids = []
        self._whitelist_usernames = []
        self._enabled = bool(self._bot_token)
        self._auto_start = True

    def _build_cache(self, **extra: Any) -> Dict[str, Any]:
        return super()._build_cache(
            whitelist_user_ids=self._whitelist_user_ids,
            whitelist_usernames=self._whitelist_usernames,
            **extra
        )

    def is_user_allowed(self, user_id: str, username: str = "") -> bool:
        """检查用户是否在白名单中"""
        if not self._whitelist_user_ids and not self._whitelist_usernames:
            return True

        try:
            uid = int(user_id)
            if uid in self._whitelist_user_ids:
                return True
        except ValueError:
            pass

        if username and username.lower() in [u.lower() for u in self._whitelist_usernames]:
            return True

        return False

    # ========== 平台特有方法 ==========

    def get_bot_token(self, force_reload: bool = False) -> str:
        self.get_config(force_reload=force_reload)
        return self._bot_token or ""

    def get_whitelist_user_ids(self, force_reload: bool = False) -> List[int]:
        self.get_config(force_reload=force_reload)
        return self._whitelist_user_ids or []

    def get_whitelist_usernames(self, force_reload: bool = False) -> List[str]:
        self.get_config(force_reload=force_reload)
        return self._whitelist_usernames or []

    @property
    def bot_token(self) -> Optional[str]:
        return self._bot_token

    @property
    def whitelist_user_ids(self) -> List[int]:
        return self._whitelist_user_ids

    @property
    def whitelist_usernames(self) -> List[str]:
        return self._whitelist_usernames