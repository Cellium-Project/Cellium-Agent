# -*- coding: utf-8 -*-
"""
QQChannelConfig - QQ 通道配置
"""

import os
from typing import Dict, Any, Optional

from ..base import BaseChannelConfig


class QQChannelConfig(BaseChannelConfig):
    """QQ通道配置类"""

    def __init__(self, config_path: str = None):
        self._app_id: Optional[str] = None
        self._app_secret: Optional[str] = None
        self._intents: int = 1107296256
        super().__init__(config_path)

    @property
    def platform_name(self) -> str:
        return "qq"

    @property
    def credentials(self) -> Dict[str, str]:
        return {"app_id": self._app_id or "", "app_secret": self._app_secret or ""}

    def has_credentials(self) -> bool:
        return True

    def _load_config(self):
        channel_cfg = self._load_yaml_config()
        self._app_id = channel_cfg.get("app_id") or os.environ.get("QQ_BOT_APP_ID")
        self._app_secret = channel_cfg.get("app_secret") or os.environ.get("QQ_BOT_APP_SECRET")
        self._intents = channel_cfg.get("intents", 1107296256)
        self._enabled = channel_cfg.get("enabled", True)
        self._auto_start = channel_cfg.get("auto_start", True)

        # 如果没有配置文件，从环境变量加载
        if not channel_cfg and not (self._app_id and self._app_secret):
            self._load_from_env()

    def _load_from_env(self):
        self._app_id = os.environ.get("QQ_BOT_APP_ID")
        self._app_secret = os.environ.get("QQ_BOT_APP_SECRET")
        self._enabled = bool(self._app_id and self._app_secret)
        self._auto_start = True
        self._intents = 1107296256

    def _build_cache(self, **extra: Any) -> Dict[str, Any]:
        return super()._build_cache(intents=self._intents, **extra)

    # ========== 平台特有方法 ==========

    def get_app_id(self, force_reload: bool = False) -> str:
        self.get_config(force_reload=force_reload)
        return self._app_id or ""

    def get_app_secret(self, force_reload: bool = False) -> str:
        self.get_config(force_reload=force_reload)
        return self._app_secret or ""

    def get_intents(self, force_reload: bool = False) -> int:
        self.get_config(force_reload=force_reload)
        return self._intents

    @property
    def app_id(self) -> Optional[str]:
        return self._app_id

    @property
    def app_secret(self) -> Optional[str]:
        return self._app_secret

    @property
    def intents(self) -> int:
        return self._intents
