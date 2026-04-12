# -*- coding: utf-8 -*-
"""
QQConfig - QQ 机器人配置组件
提供 QQ Bot 配置的查询接口，供 Agent 在运行时获取配置信息
"""

import os
from app.core.interface.base_cell import BaseCell


class QQConfigCell(BaseCell):
    cell_name = "qq_config"

    def __init__(self):
        super().__init__()
        self._app_id = os.environ.get("QQ_BOT_APP_ID", "")
        self._app_secret = os.environ.get("QQ_BOT_APP_SECRET", "")
        self._intents = 1107296256

    def _cmd_get_config(self) -> dict:
        """获取完整的 QQ Bot 配置"""
        return {
            "app_id": self._mask(self._app_id),
            "app_secret": self._mask(self._app_secret),
            "intents": self._intents,
            "has_credentials": bool(self._app_id and self._app_secret),
        }

    def _cmd_get_app_id(self) -> str:
        """获取 QQ Bot AppID"""
        return self._app_id

    def _cmd_get_app_secret(self) -> str:
        """获取 QQ Bot AppSecret（已脱敏）"""
        return self._mask(self._app_secret)

    def _cmd_get_intents(self) -> int:
        """获取 intents 值"""
        return self._intents

    def _cmd_has_credentials(self) -> bool:
        """检查是否已配置凭证"""
        return bool(self._app_id and self._app_secret)

    def _mask(self, value: str) -> str:
        """脱敏显示"""
        if not value:
            return ""
        if len(value) <= 4:
            return "****"
        return value[:2] + "****" + value[-2:]

    def _cmd_platform_name(self) -> str:
        """获取平台名称"""
        return "qq"
