# -*- coding: utf-8 -*-
"""
QQChannelConfig - QQ 通道配置

功能：
  - 提供 QQ Bot 配置的查询接口
  - 支持热重载（配置文件变更自动生效）
  - 供 ChannelManager 和 QQAdapter 在运行时获取最新配置
"""

import os
import threading
import time
from typing import Optional, Dict, Any


class QQChannelConfig:
    DEFAULT_CONFIG_PATH = "config/agent/channels.yaml"

    def __init__(self, config_path: str = None):
        self._config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 1.0

        self._app_id: Optional[str] = None
        self._app_secret: Optional[str] = None
        self._intents: int = 1107296256
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

            channel_cfg = data.get("channels", {}).get("qq", {})
            self._app_id = channel_cfg.get("app_id") or os.environ.get("QQ_BOT_APP_ID")
            self._app_secret = channel_cfg.get("app_secret") or os.environ.get("QQ_BOT_APP_SECRET")
            self._intents = channel_cfg.get("intents", 1107296256)
            self._enabled = channel_cfg.get("enabled", True)
            self._auto_start = channel_cfg.get("auto_start", True)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[QQChannelConfig] Load config failed: {e}")
            self._load_from_env()

    def _load_from_env(self):
        self._app_id = os.environ.get("QQ_BOT_APP_ID")
        self._app_secret = os.environ.get("QQ_BOT_APP_SECRET")
        self._enabled = bool(self._app_id and self._app_secret)
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
                    "app_id": self._mask(self._app_id),
                    "app_secret": self._mask(self._app_secret),
                    "intents": self._intents,
                    "enabled": self._enabled,
                    "auto_start": self._auto_start,
                    "has_credentials": bool(self._app_id and self._app_secret),
                    "config_path": self._config_path,
                }
                self._cache_time = time.time()
            return self._cache.copy()

    def _mask(self, value: str) -> str:
        if not value:
            return ""
        if len(value) <= 4:
            return "****"
        return value[:2] + "****" + value[-2:]

    def get_config(self, force_reload: bool = False) -> Dict[str, Any]:
        return self._get_config(force_reload=force_reload)

    def get_app_id(self, force_reload: bool = False) -> str:
        self._get_config(force_reload=force_reload)
        return self._app_id or ""

    def get_app_secret(self, force_reload: bool = False) -> str:
        self._get_config(force_reload=force_reload)
        return self._app_secret or ""

    def has_credentials(self, force_reload: bool = False) -> bool:
        self._get_config(force_reload=force_reload)
        return bool(self._app_id and self._app_secret)

    def is_enabled(self, force_reload: bool = False) -> bool:
        self._get_config(force_reload=force_reload)
        return self._enabled

    def should_auto_start(self, force_reload: bool = False) -> bool:
        self._get_config(force_reload=force_reload)
        return self._auto_start and self._enabled and bool(self._app_id and self._app_secret)

    def reload(self) -> Dict[str, Any]:
        return self._get_config(force_reload=True)

    @property
    def platform_name(self) -> str:
        return "qq"
