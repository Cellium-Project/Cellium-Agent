# -*- coding: utf-8 -*-
"""微信 iLink Bot 通道配置"""

import os
from typing import Dict, Any, Optional

from ..base import BaseChannelConfig


class WeixinChannelConfig(BaseChannelConfig):

    def __init__(self, config_path: str = None):
        self._state_dir: Optional[str] = None
        self._bot_agent: str = "Cellium Agent"
        self._base_url: Optional[str] = None
        super().__init__(config_path)

    @property
    def platform_name(self) -> str:
        return "weixin"

    @property
    def credentials(self) -> Dict[str, str]:
        # 微信通过扫码登录，无需预配置凭证
        # 有 state_dir 即视为可启动
        return {"state_dir": self._state_dir or ""}

    def has_credentials(self) -> bool:
        return bool(self._state_dir)

    def _load_config(self):
        channel_cfg = self._load_yaml_config()
        self._state_dir = channel_cfg.get("state_dir") or os.environ.get("WEIXIN_STATE_DIR", "data/weixin")
        self._bot_agent = channel_cfg.get("bot_agent", "Cellium Agent")
        self._base_url = channel_cfg.get("base_url") or os.environ.get("WEIXIN_BASE_URL")
        self._enabled = channel_cfg.get("enabled", False)
        self._auto_start = channel_cfg.get("auto_start", True)

        if not channel_cfg and not self._state_dir:
            self._load_from_env()

    def _load_from_env(self):
        self._state_dir = os.environ.get("WEIXIN_STATE_DIR", "data/weixin")
        self._base_url = os.environ.get("WEIXIN_BASE_URL")
        self._enabled = True
        self._auto_start = True

    def _build_cache(self, **extra: Any) -> Dict[str, Any]:
        return super()._build_cache(
            bot_agent=self._bot_agent,
            **extra,
        )

    def get_state_dir(self, force_reload: bool = False) -> str:
        self.get_config(force_reload=force_reload)
        return self._state_dir or "data/weixin"

    def get_bot_agent(self, force_reload: bool = False) -> str:
        self.get_config(force_reload=force_reload)
        return self._bot_agent or "Cellium Agent"

    def get_base_url(self, force_reload: bool = False) -> Optional[str]:
        self.get_config(force_reload=force_reload)
        return self._base_url

    @property
    def enabled(self) -> bool:
        return self._enabled
