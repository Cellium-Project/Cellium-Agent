# -*- coding: utf-8 -*-
"""配置管理工具"""

import logging
from typing import Any, Dict

from app.agent.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


class ConfigTool(BaseTool):
    name = "config"
    description = (
        "配置管理工具。"
        "help 查看可用命令；list_models/add_model 管理模型；"
        "switch_model 切换模型；enable_intent/set_intent_model 管理意图感知；"
        "list_channels/set_telegram/set_qq/set_feishu 管理外部通道。"
    )

    def _cmd_help(self) -> dict:
        """查看配置工具帮助"""
        return {
            "success": True,
            "message": (
                "§配置工具命令：\n"
                "• help() — 查看本帮助\n"
                "• list_models() — 查看可用模型列表\n"
                "• add_model(name, model, api_key, base_url, temperature=0.7, vision=true) — 添加模型\n"
                "• switch_model(model_name) — 切换当前 LLM 模型\n"
                "• enable_intent(enabled) — 开启/关闭意图感知\n"
                "• set_intent_model(api_key, model, base_url, temperature=0.3) — 配置意图感知模型\n"
                "—\n"
                "外部通道配置：\n"
                "• list_channels() — 查看通道状态\n"
                "• set_telegram(bot_token, whitelist_user_ids=[], whitelist_usernames=[]) — 配置 Telegram\n"
                "• set_qq(app_id, app_secret) — 配置 QQ 机器人\n"
                "• set_feishu(app_id, app_secret, whitelist_users=[]) — 配置飞书\n"
                "• enable_channel(channel_name, enabled) — 启用/禁用通道\n"
                "\n"
                "说明：\n"
                "- name 是 llm.yaml 中 - name: 后的标识，用于 switch_model\n"
                "- model 是实际模型名\n"
                "- channel_name 支持: telegram, qq, feishu\n"
            ),
        }

    def _cmd_list_models(self) -> dict:
        """查看当前可用模型列表与当前模型"""
        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()
            llm_cfg = cfg.get("llm") or {}
            models = llm_cfg.get("models") or []
            current = llm_cfg.get("current_model") or (models[0].get("name") if models else "")

            items = []
            for m in models:
                name = m.get("name", "?")
                model = m.get("model", "?")
                items.append({
                    "name": name,
                    "model": model,
                    "base_url": _mask(m.get("base_url", "")),
                    "temperature": m.get("temperature"),
                    "vision": m.get("vision", False),
                    "is_current": name == current,
                })

            return {
                "success": True,
                "current_model": current,
                "models": items,
                "count": len(items),
            }
        except Exception as e:
            logger.error("[ConfigTool] list_models 失败 | error=%s", e)
            return {"success": False, "error": str(e)}

    def _cmd_add_model(
        self,
        name: str = "",
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        temperature: float = 0.7,
        vision: bool = True,
    ) -> dict:
        """添加新模型到 llm.models"""
        if not name or not model or not api_key or not base_url:
            return {
                "success": False,
                "error": "name, model, api_key, base_url 均为必填",
            }

        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()
            llm_cfg = cfg.get("llm") or {}
            models = list(llm_cfg.get("models") or [])

            if any(m.get("name") == name for m in models):
                return {"success": False, "error": f"模型 name 已存在: {name}"}

            new_model = {
                "name": name,
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "temperature": float(temperature),
                "timeout": 120,
                "vision": bool(vision),
            }
            models.append(new_model)

            cfg.set("llm.models", models, persist=True, notify=False)
            cfg.reload_section("llm")

            logger.info("[ConfigTool] add_model | name=%s | model=%s", name, model)

            return {
                "success": True,
                "name": name,
                "model": model,
                "message": f"已添加模型 {name}（model={model}），立即生效",
            }
        except Exception as e:
            logger.error("[ConfigTool] add_model 失败 | name=%s | error=%s", name, e)
            return {"success": False, "error": str(e)}

    def _cmd_switch_model(self, model_name: str = "") -> dict:
        """切换当前 LLM 模型，改完立即生效"""
        if not model_name:
            return {"success": False, "error": "model_name is required"}

        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()
            llm_cfg = cfg.get("llm") or {}
            models = llm_cfg.get("models") or []
            if not models:
                return {"success": False, "error": "llm.models 配置为空"}

            current_name = llm_cfg.get("current_model") or models[0].get("name")

            target_model = None
            for m in models:
                if m.get("name") == model_name:
                    target_model = m
                    break

            if target_model is None:
                available = [{"name": m.get("name"), "model": m.get("model")} for m in models]
                return {
                    "success": False,
                    "error": f"模型不存在: {model_name}（model_name 必须是已配置的 name，不是实际模型名）",
                    "available": available,
                    "hint": "model_name 是 llm.yaml 中 - name: 后的标识，不是 model: 后的实际模型名",
                }

            actual_model = target_model.get("model", "")

            if current_name == model_name:
                return {
                    "success": True,
                    "current_model": model_name,
                    "actual_model": actual_model,
                    "message": f"当前已经是 {model_name}（model={actual_model}），无需切换",
                }

            cfg.set("llm.current_model", model_name, persist=True, notify=False)
            cfg.reload_section("llm")

            logger.info("[ConfigTool] switch_model | from=%s | to=%s | actual=%s",
                        current_name, model_name, actual_model)

            return {
                "success": True,
                "previous_model": current_name,
                "current_model": model_name,
                "actual_model": actual_model,
                "message": f"已切换到 {model_name}（model={actual_model}），下次迭代起生效",
            }
        except Exception as e:
            logger.error("[ConfigTool] switch_model 失败 | model=%s | error=%s", model_name, e)
            return {"success": False, "error": str(e)}

    def _cmd_enable_intent(self, enabled: bool = None) -> dict:
        """开启或关闭意图感知"""
        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()
            heur_cfg = cfg.get("heuristics") or {}
            intent_cfg = heur_cfg.get("intent") or {}
            current = intent_cfg.get("enabled", False)

            if enabled is None:
                return {
                    "success": True,
                    "enabled": current,
                    "message": f"意图感知当前: {'开启' if current else '关闭'}",
                }

            new_state = bool(enabled)
            if current == new_state:
                return {
                    "success": True,
                    "enabled": new_state,
                    "message": f"意图感知已经是 {'开启' if new_state else '关闭'}，无需修改",
                }

            cfg.set("heuristics.intent.enabled", new_state, persist=True, notify=False)
            cfg.reload_section("heuristics")

            logger.info("[ConfigTool] enable_intent | from=%s | to=%s", current, new_state)

            return {
                "success": True,
                "previous": current,
                "enabled": new_state,
                "message": f"已{'开启' if new_state else '关闭'}意图感知，立即生效",
            }
        except Exception as e:
            logger.error("[ConfigTool] enable_intent 失败 | error=%s", e)
            return {"success": False, "error": str(e)}

    def _cmd_set_intent_model(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        temperature: float = 0.3,
    ) -> dict:
        """配置意图感知模型"""
        if not api_key or not model or not base_url:
            return {
                "success": False,
                "error": "api_key, model, base_url 均为必填",
            }

        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()

            cfg.set("heuristics.intent.model.api_key", api_key, persist=True, notify=False)
            cfg.set("heuristics.intent.model.model", model, persist=True, notify=False)
            cfg.set("heuristics.intent.model.base_url", base_url, persist=True, notify=False)
            cfg.set("heuristics.intent.model.temperature", float(temperature), persist=True, notify=False)

            heur_cfg = cfg.get("heuristics") or {}
            intent_enabled = (heur_cfg.get("intent") or {}).get("enabled", False)
            if not intent_enabled:
                cfg.set("heuristics.intent.enabled", True, persist=True, notify=False)

            cfg.reload_section("heuristics")

            logger.info("[ConfigTool] set_intent_model | model=%s | base_url=%s | auto_enabled=%s",
                        model, _mask(base_url), not intent_enabled)

            return {
                "success": True,
                "model": model,
                "base_url": _mask(base_url),
                "temperature": float(temperature),
                "auto_enabled_intent": not intent_enabled,
                "message": (
                    f"已配置意图感知模型（model={model}），立即生效。"
                    + ("意图感知已自动开启。" if not intent_enabled else "")
                ),
            }
        except Exception as e:
            logger.error("[ConfigTool] set_intent_model 失败 | error=%s", e)
            return {"success": False, "error": str(e)}

    def _cmd_list_channels(self) -> dict:
        """查看外部通道配置状态"""
        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()
            ch_cfg = cfg.get("channels") or {}

            result = {}
            for platform in ("telegram", "qq", "feishu"):
                pc = ch_cfg.get(platform) or {}
                result[platform] = {
                    "enabled": pc.get("enabled", False),
                    "configured": bool(self._channel_is_configured(platform, pc)),
                }

            return {"success": True, "channels": result}
        except Exception as e:
            logger.error("[ConfigTool] list_channels 失败 | error=%s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _channel_is_configured(platform: str, pc: dict) -> bool:
        """判断通道是否已配置密钥"""
        if platform == "telegram":
            return bool(pc.get("bot_token"))
        if platform == "qq":
            return bool(pc.get("app_id") and pc.get("app_secret"))
        if platform == "feishu":
            return bool(pc.get("app_id") and pc.get("app_secret"))
        return False

    def _set_channel_config(self, platform: str, fields: Dict[str, Any]) -> dict:
        """写入通道配置并热重载"""
        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()
            for k, v in fields.items():
                cfg.set(f"channels.{platform}.{k}", v, persist=True, notify=False)
            cfg.reload_section("channels")
            return {"success": True}
        except Exception as e:
            logger.error("[ConfigTool] set_channel 失败 | platform=%s | error=%s", platform, e)
            return {"success": False, "error": str(e)}

    def _channel_reload_hint(self, platform: str) -> str:
        """构造通道重载提示"""
        try:
            from app.channels import ChannelManager
            mgr = ChannelManager.get_instance()
            if mgr.get_adapter(platform):
                return f"配置已保存，正在通过 {platform} 通道热重载生效。"
        except Exception:
            pass
        return "配置已保存并热重载。"

    def _cmd_set_telegram(
        self,
        bot_token: str = "",
        whitelist_user_ids: list = None,
        whitelist_usernames: list = None,
    ) -> dict:
        """配置 Telegram 通道"""
        if not bot_token:
            return {"success": False, "error": "bot_token 为必填（向 @BotFather 申请）"}

        fields = {"bot_token": bot_token, "enabled": True}
        if whitelist_user_ids is not None:
            fields["whitelist_user_ids"] = list(whitelist_user_ids)
        if whitelist_usernames is not None:
            fields["whitelist_usernames"] = list(whitelist_usernames)

        res = self._set_channel_config("telegram", fields)
        if not res.get("success"):
            return res

        logger.info("[ConfigTool] set_telegram | token=%s", _mask(bot_token))
        return {
            "success": True,
            "platform": "telegram",
            "bot_token": _mask(bot_token),
            "enabled": True,
            "message": f"Telegram 配置完成（token={_mask(bot_token)}）。{self._channel_reload_hint('telegram')}",
        }

    def _cmd_set_qq(self, app_id: str = "", app_secret: str = "") -> dict:
        """配置 QQ 机器人通道"""
        if not app_id or not app_secret:
            return {"success": False, "error": "app_id 与 app_secret 均为必填"}

        res = self._set_channel_config("qq", {"app_id": app_id, "app_secret": app_secret, "enabled": True})
        if not res.get("success"):
            return res

        logger.info("[ConfigTool] set_qq | app_id=%s | secret=%s", app_id, _mask(app_secret))
        return {
            "success": True,
            "platform": "qq",
            "app_id": app_id,
            "app_secret": _mask(app_secret),
            "enabled": True,
            "message": f"QQ 配置完成（app_id={app_id}）。{self._channel_reload_hint('qq')}",
        }

    def _cmd_set_feishu(
        self,
        app_id: str = "",
        app_secret: str = "",
        whitelist_users: list = None,
    ) -> dict:
        """配置飞书通道"""
        try:
            import lark_oapi  # noqa: F401
        except ImportError:
            return {
                "success": False,
                "dependency_missing": True,
                "platform": "feishu",
                "message": (
                    "飞书通道依赖 lark-oapi 未安装，无法配置。"
                    "请询问用户是否安装：pip install lark-oapi"
                ),
            }

        if not app_id or not app_secret:
            return {"success": False, "error": "app_id 与 app_secret 均为必填"}

        fields = {"app_id": app_id, "app_secret": app_secret, "enabled": True}
        if whitelist_users is not None:
            fields["whitelist_users"] = list(whitelist_users)

        res = self._set_channel_config("feishu", fields)
        if not res.get("success"):
            return res

        logger.info("[ConfigTool] set_feishu | app_id=%s | secret=%s", app_id, _mask(app_secret))
        return {
            "success": True,
            "platform": "feishu",
            "app_id": app_id,
            "app_secret": _mask(app_secret),
            "enabled": True,
            "message": f"飞书配置完成（app_id={app_id}）。{self._channel_reload_hint('feishu')}",
        }

    def _cmd_enable_channel(self, channel_name: str = "", enabled: bool = None) -> dict:
        """启用/禁用外部通道"""
        if channel_name not in ("telegram", "qq", "feishu"):
            return {
                "success": False,
                "error": f"channel_name 仅支持: telegram, qq, feishu（收到: {channel_name}）",
            }
        if enabled is None:
            return {"success": False, "error": "enabled 参数必填（true/false）"}

        res = self._set_channel_config(channel_name, {"enabled": bool(enabled)})
        if not res.get("success"):
            return res

        logger.info("[ConfigTool] enable_channel | %s | enabled=%s", channel_name, enabled)
        return {
            "success": True,
            "platform": channel_name,
            "enabled": bool(enabled),
            "message": f"通道 {channel_name} 已{'启用' if enabled else '禁用'}。{self._channel_reload_hint(channel_name)}",
        }
