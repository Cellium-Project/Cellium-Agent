# -*- coding: utf-8 -*-
"""
命令处理器
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class CommandHandler:
    """Agent 命令处理器 — 处理用户斜杠命令"""

    @staticmethod
    def handle_config(raw_input: str) -> Dict[str, Any]:
        """处理 /config 命令：查看当前模型与可用模型"""
        from app.core.util.agent_config import get_config

        try:
            cfg = get_config()
            llm_cfg = cfg.get("llm") or {}
            models = llm_cfg.get("models") or []
            current = llm_cfg.get("current_model") or (models[0].get("name") if models else "")

            lines = [f"### 当前 LLM 配置", f"- current_model: **{current}**"]
            if models:
                lines.append("\n### 可用模型")
                for m in models:
                    name = m.get("name", "?")
                    model = m.get("model", "?")
                    mark = " ← 当前" if name == current else ""
                    lines.append(f"- `{name}` → `{model}`{mark}")
            lines.append(
                "\n### 切换模型\n"
                "让 agent 调用 `config.switch_model(model_name=...)` 即可立即切换。"
            )
            return {
                "success": True,
                "message": "\n".join(lines),
                "command": "/config",
                "current_model": current,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"读取 LLM 配置失败: {e}",
                "command": "/config",
            }

    @staticmethod
    def is_slash_command(user_input: str) -> bool:
        """检查是否为斜杠命令"""
        stripped = user_input.strip()
        return stripped.startswith("/config")

    async def process(self, user_input: str):
        """
        处理斜杠命令，返回 SSE 事件生成器（异步迭代器）
        """
        stripped = user_input.strip()
        if stripped.startswith("/config"):
            result = self.handle_config(stripped)
        else:
            result = {"success": False, "message": f"未知命令: {stripped}", "command": stripped}

        yield {"type": "content_chunk", "content": result["message"]}
        yield {
            "type": "done",
            "content": result["message"],
            "iterations": 0,
            "tool_traces": [],
            "command_result": result,
        }
