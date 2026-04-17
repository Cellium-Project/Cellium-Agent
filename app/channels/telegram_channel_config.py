# -*- coding: utf-8 -*-
"""
TelegramChannelConfig - Telegram 通道配置

功能：
  - 提供 Telegram Bot 配置的查询接口
  - 支持热重载（配置文件变更自动生效）
  - 供 ChannelManager 和 TelegramAdapter 在运行时获取最新配置
"""

import os
import threading
import time
from typing import Optional, Dict, Any, List


class TelegramChannelConfig:
    DEFAULT_CONFIG_PATH = "config/agent/channels.yaml"

    def __init__(self, config_path: str = None):
        self._config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 1.0

        self._bot_token: Optional[str] = None
        self._whitelist_user_ids: List[int] = []
        self._whitelist_usernames: List[str] = []
        self._enabled: bool = False
        self._auto_start: bool = True

        self._load_config()

    def _load_config(self):
        import yaml
        try:
            if not os.path.exists(self._config_path):
                self._load_from_env()
                return

            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            channel_cfg = data.get("channels", {}).get("telegram", {})
            self._bot_token = channel_cfg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
            self._whitelist_user_ids = channel_cfg.get("whitelist_user_ids", []) or []
            self._whitelist_usernames = channel_cfg.get("whitelist_usernames", []) or []
            self._enabled = channel_cfg.get("enabled", False)
            self._auto_start = channel_cfg.get("auto_start", True)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[TelegramChannelConfig] Load config failed: {e}")
            self._load_from_env()

    def _load_from_env(self):
        self._bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self._whitelist_user_ids = []
        self._whitelist_usernames = []
        self._enabled = bool(self._bot_token)
        self._auto_start = True

    def _check_cache(self) -> bool:
        if not self._cache:
            return False
        if time.time() - self._cache_time > self._cache_ttl:
            return False
        return True

    def _get_config(self, force_reload: bool = False) -> Dict[str, Any]:
        with self._lock:
            if force_reload or not self._check_cache():
                self._load_config()
                self._cache = {
                    "bot_token": self._mask(self._bot_token),
                    "whitelist_user_ids": self._whitelist_user_ids,
                    "whitelist_usernames": self._whitelist_usernames,
                    "enabled": self._enabled,
                    "auto_start": self._auto_start,
                    "has_credentials": bool(self._bot_token),
                    "config_path": self._config_path,
                }
                self._cache_time = time.time()
            return self._cache.copy()

    def _mask(self, value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "****"
        return value[:4] + "****" + value[-4:]

    def get_config(self, force_reload: bool = False) -> Dict[str, Any]:
        return self._get_config(force_reload=force_reload)

    def get_bot_token(self, force_reload: bool = False) -> str:
        self._get_config(force_reload=force_reload)
        return self._bot_token or ""

    def get_whitelist_user_ids(self, force_reload: bool = False) -> List[int]:
        self._get_config(force_reload=force_reload)
        return self._whitelist_user_ids or []

    def get_whitelist_usernames(self, force_reload: bool = False) -> List[str]:
        self._get_config(force_reload=force_reload)
        return self._whitelist_usernames or []

    def has_credentials(self, force_reload: bool = False) -> bool:
        self._get_config(force_reload=force_reload)
        return bool(self._bot_token)

    def is_enabled(self, force_reload: bool = False) -> bool:
        self._get_config(force_reload=force_reload)
        return self._enabled

    def should_auto_start(self, force_reload: bool = False) -> bool:
        self._get_config(force_reload=force_reload)
        return self._auto_start and self._enabled and bool(self._bot_token)

    def reload(self) -> Dict[str, Any]:
        return self._get_config(force_reload=True)

    @property
    def platform_name(self) -> str:
        return "telegram"
