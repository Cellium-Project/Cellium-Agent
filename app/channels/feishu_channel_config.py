# -*- coding: utf-8 -*-
"""
FeishuChannelConfig - 飞书通道配置
"""

import os
from typing import Dict, Any, List, Optional

from .base import BaseChannelConfig


class FeishuChannelConfig(BaseChannelConfig):
    """飞书通道配置类"""

    def __init__(self, config_path: str = None):
        self._app_id: Optional[str] = None
        self._app_secret: Optional[str] = None
        self._whitelist_users: List[str] = []
        super().__init__(config_path)

    @property
    def platform_name(self) -> str:
        return "feishu"

    @property
    def credentials(self) -> Dict[str, str]:
        return {"app_id": self._app_id or "", "app_secret": self._app_secret or ""}

    def _load_config(self):
        channel_cfg = self._load_yaml_config()
        self._app_id = channel_cfg.get("app_id") or os.environ.get("FEISHU_APP_ID")
        self._app_secret = channel_cfg.get("app_secret") or os.environ.get("FEISHU_APP_SECRET")
        self._enabled = channel_cfg.get("enabled", True)
        self._auto_start = channel_cfg.get("auto_start", True)
        self._whitelist_users = channel_cfg.get("whitelist_users", [])

        # 如果没有配置文件，从环境变量加载
        if not channel_cfg and not (self._app_id and self._app_secret):
            self._load_from_env()

    def _load_from_env(self):
        self._app_id = os.environ.get("FEISHU_APP_ID")
        self._app_secret = os.environ.get("FEISHU_APP_SECRET")
        self._enabled = bool(self._app_id and self._app_secret)
        self._auto_start = True
        self._whitelist_users = []

    def _build_cache(self, **extra: Any) -> Dict[str, Any]:
        return super()._build_cache(whitelist_users=self._whitelist_users, **extra)

    def is_user_allowed(self, user_id: str) -> bool:
        """检查用户是否在白名单中"""
        if not self._whitelist_users:
            return True
        return user_id in self._whitelist_users

    # ========== 平台特有方法 ==========

    def get_app_id(self, force_reload: bool = False) -> str:
        self.get_config(force_reload=force_reload)
        return self._app_id or ""

    def get_app_secret(self, force_reload: bool = False) -> str:
        self.get_config(force_reload=force_reload)
        return self._app_secret or ""

    def get_whitelist_users(self, force_reload: bool = False) -> List[str]:
        self.get_config(force_reload=force_reload)
        return self._whitelist_users

    @property
    def app_id(self) -> Optional[str]:
        return self._app_id

    @property
    def app_secret(self) -> Optional[str]:
        return self._app_secret

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._app_id) and bool(self._app_secret)

    @property
    def auto_start(self) -> bool:
        return self._auto_start

    @property
    def whitelist_users(self) -> List[str]:
        return self._whitelist_users
