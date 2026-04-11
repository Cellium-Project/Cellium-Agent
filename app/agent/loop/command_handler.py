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
    def handle_trust(raw_input: str) -> Dict[str, Any]:
        """
        处理 /trust <组件名> 命令
        """
        parts = raw_input.strip().split()
        if len(parts) < 2:
            return {
                "success": False,
                "message": (
                    "**用法**: `/trust <组件名>` 或 `/trust all`\n\n"
                    "信任一个待审批的组件，使其注册为 LLM 工具。\n"
                    "- `/trust skill_installer` — 信任指定组件\n"
                    "- `/trust all` — 一键信任所有待审批组件（谨慎操作）\n"
                    "- `/pending` — 查看待审批列表"
                ),
                "command": "/trust",
            }

        tool_name = parts[1].strip().lower()

        if tool_name == "all":
            return CommandHandler._handle_trust_all()

        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()

            if reg.is_trusted(tool_name):
                return {
                    "success": False,
                    "message": f"**{tool_name}** 已经在信任白名单中，无需重复信任。",
                    "command": "/trust",
                }

            result = reg.trust(tool_name)

            if result.get("success"):
                msg = (
                    f"[信任通过] {result['message']}\n\n"
                    f"- 组件: **{tool_name}**\n"
                    f"- 类型: {result.get('component_type', '?')}\n"
                    f"- 可用命令: {', '.join(result.get('commands', []))}\n"
                    f"- 该组件现在可作为 LLM 工具使用。"
                )
            else:
                msg = f"[操作失败] {result['message']}"

            return {
                "success": result.get("success", False),
                "message": msg,
                "command": "/trust",
                **result,
            }
        except Exception as e:
            logger.error("[CommandHandler] /trust 命令执行失败: %s", e)
            return {"success": False, "message": f"执行 /trust 命令时出错: {e}", "command": "/trust"}

    @staticmethod
    def _handle_trust_all() -> Dict[str, Any]:
        """处理 /trust all 命令"""
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()
            pending = reg.get_pending_approvals()
            items = pending["items"]

            if not items:
                return {
                    "success": True,
                    "message": "**当前没有待审批的组件，无需操作。**",
                    "command": "/trust all",
                }

            trusted_names, failed_names = [], []
            for item in items:
                tname = item["tool_name"]
                result = reg.trust(tname)
                if result.get("success"):
                    trusted_names.append(tname)
                else:
                    failed_names.append(tname)

            msg_lines = [f"[批量信任] 完成: {len(trusted_names)} 个成功, {len(failed_names)} 个失败\n"]
            if trusted_names:
                msg_lines.append(f"**已信任**: {', '.join(trusted_names)}")
            if failed_names:
                msg_lines.append(f"**失败**: {', '.join(failed_names)}")
            msg_lines.append("\n这些组件现在可作为 LLM 工具使用。")

            return {
                "success": len(failed_names) == 0,
                "message": "\n".join(msg_lines),
                "command": "/trust all",
                "trusted_count": len(trusted_names),
                "failed_count": len(failed_names),
                "trusted_names": trusted_names,
                "failed_names": failed_names,
            }
        except Exception as e:
            logger.error("[CommandHandler] /trust all 执行失败: %s", e)
            return {"success": False, "message": f"/trust all 执行失败: {e}", "command": "/trust all"}

    @staticmethod
    def handle_pending_list() -> Dict[str, Any]:
        """处理 /pending 命令"""
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()
            pending = reg.get_pending_approvals()

            total = pending["total"]
            items = pending["items"]

            if total == 0:
                return {
                    "success": True,
                    "message": "**当前没有待审批的组件。** 所有组件均已注册或已信任。",
                    "command": "/pending",
                    "pending_count": 0,
                }

            lines = [f"### 待审批组件列表 ({total} 个)\n"]
            for item in items:
                tname = item["tool_name"]
                imports = "\n".join(item.get("danger_imports", ["未知"]))
                critical = item.get("critical_count", 0)
                lines.append(
                    f"#### {tname}\n"
                    f"- 类型: `{item.get('component_type', '?')}`\n"
                    f"- 问题数: {item.get('issue_count', 0)} ({critical} 个严重)\n"
                    f"- 危险导入:\n{imports}\n"
                    f'- **操作**: 输入 `/trust {tname}` 信任此组件\n'
                )

            lines.append(f"\n> 使用 `/trust all` 可一键信任所有待审批组件（谨慎操作）")

            return {
                "success": True,
                "message": "\n".join(lines),
                "command": "/pending",
                "pending_count": total,
                "details": pending,
            }
        except Exception as e:
            logger.error("[CommandHandler] /pending 命令执行失败: %s", e)
            return {"success": False, "message": f"查询待审批列表时出错: {e}", "command": "/pending"}

    @staticmethod
    def is_slash_command(user_input: str) -> bool:
        """检查是否为斜杠命令"""
        stripped = user_input.strip()
        return stripped.startswith("/trust") or stripped.startswith("/pending")

    async def process(self, user_input: str):
        """
        处理斜杠命令，返回 SSE 事件生成器（异步迭代器）
        """
        stripped = user_input.strip()
        if stripped.startswith("/trust"):
            result = self.handle_trust(stripped)
        elif stripped.startswith("/pending") or stripped == "/trust":
            result = self.handle_pending_list()
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
