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
        "switch_model 切换模型；enable_intent/set_intent_model 管理意图感知。"
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
                "\n"
                "说明：\n"
                "- name 是 llm.yaml 中 - name: 后的标识，用于 switch_model\n"
                "- model 是实际模型名\n"
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
