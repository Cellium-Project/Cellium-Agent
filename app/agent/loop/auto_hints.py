# -*- coding: utf-8 -*-
"""
自动工具提示生成器 - 从 AgentLoop 中提取

职责：
  - 组件审查修复建议（每轮持续注入）
  - 工具使用帮助自动注入
  - 单工具详细帮助生成
  - 重定向引导消息构建
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AutoHintManager:
    """自动提示管理器 — 在 LLM 持续失败时自动注入工具使用指南"""

    def __init__(self):
        self._injected_tool_helps: Dict[str, str] = {}

    def get_auto_tool_hints(self, tools: Dict[str, Any]) -> str:
        """
        获取需要自动注入的工具使用提示（优先级从高到低）

        Returns:
            需要注入的帮助文本（空字符串表示无需注入）
        """
        hints = []

        # ★ 优先级0：检查组件审查提示（组件注册被拒绝的修复建议）
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()
            for tname, hint_text in reg.get_all_audit_hints().items():
                if hint_text:
                    hints.append(hint_text)
                    logger.debug("[AutoHint] 注入审查修复建议(持续): %s", tname)
        except Exception as e:
            logger.debug("[AutoHint] 审查提示获取失败: %s", e)

        return "\n\n".join(hints)

    @staticmethod
    def build_redirect_message(
        reasons: List[str],
        suggestions: List[str],
        tool_recommendations: Optional[Dict] = None,
    ) -> str:
        """构建 REDIRECT 引导消息"""
        reasons_text = "\n".join(f"- {r}" for r in reasons[:3])
        suggestions_text = "\n".join(f"- {s}" for s in suggestions[:3])

        tools_section = ""
        if tool_recommendations and tool_recommendations.get("recommended_tools"):
            tools = tool_recommendations["recommended_tools"]
            lines = [f"- **{t['name']}** (score: {t['score']:.2f}) — {t['reason']}" for t in tools]
            tools_section = f"\n**推荐尝试的工具：**\n{chr(10).join(lines)}"

        return f"""## ⚠️ 方向调整建议

检测到当前执行可能陷入困境：

**问题原因：**
{reasons_text}

**建议尝试的方向：**
{suggestions_text}
{tools_section}
**请考虑：**
1. 换一个工具或方法尝试
2. 回顾之前的步骤，确认是否有遗漏
3. 如果当前方向确实行不通，可以告知用户并寻求更多信息
"""

    @staticmethod
    def format_component_help(tool_name: str, help_dict: Dict[str, Any]) -> str:
        """将组件 _cmd_help 返回的 dict 转为 LLM 可读的帮助文本"""
        lines = [f"### ⚙️ `{tool_name}` 组件自带用法说明", ""]
        desc = help_dict.get("description", "")
        if desc:
            lines.append(f"**功能**: {desc}")
            lines.append("")
        commands = help_dict.get("available_commands", {})
        if commands:
            lines.append(f"**可用命令**: {', '.join(f'`{c}`' for c in commands.keys())}")
            lines.append("")
        examples = help_dict.get("usage_examples", [])
        if examples:
            lines.append("**调用示例**:")
            for ex in examples:
                cmd = ex.get("command", "?")
                args = ex.get("args", {})
                edesc = ex.get("description", "")
                lines.append(f"- **{cmd}**: {edesc}")
                if args:
                    lines.append(f"  ```json")
                    lines.append(f"  {json.dumps({**{'command': cmd}, **args}, ensure_ascii=False, indent=2)}")
                    lines.append(f"  ```")
            lines.append("")
        notes = help_dict.get("_notes") or help_dict.get("notes", [])
        if notes:
            lines.append("**注意事项**:")
            for n in notes:
                lines.append(f"- {n}")
            lines.append("")
        call_format = help_dict.get("_call_format")
        if call_format:
            lines.append("**调用格式**:")
            lines.append("```json")
            lines.append(json.dumps(call_format.get("example", {}), ensure_ascii=False, indent=2))
            lines.append("```")

        return "\n".join(lines)

    @staticmethod
    def generate_single_tool_hint(tool_name: str, tool_instance: Any) -> str:
        """为单个工具生成详细的使用帮助"""
        try:
            defn = getattr(tool_instance, "definition", None)
            if not defn:
                return ""

            fn_info = defn.get("function", {})
            desc = fn_info.get("description", "")
            params = fn_info.get("parameters", {})
            props = params.get("properties", {})
            required = set(params.get("required", []))

            lines = [
                f"### ⚠️ `{tool_name}` 工具调用修正指南",
                "",
                f"**你已多次调用此工具但格式不正确。请严格按照以下格式调用：**",
                "",
            ]

            if desc:
                lines.append(f"**功能**: {desc}")
                lines.append("")

            has_command_field = "command" in props
            command_enum = props.get("command", {}).get("enum", [])

            if has_command_field and command_enum:
                lines.append("**这是子命令模式工具 — 必填 `command` 字段！**")
                lines.append("")
                lines.append(f"`command` 可选值：{', '.join(f'`{c}`' for c in command_enum)}")
                lines.append("")

                for cmd_name in command_enum:
                    cmd_params = []
                    cmd_required = []
                    for pname, pinfo in props.items():
                        if pname == "command":
                            continue
                        ptype = pinfo.get("type", "string")
                        pdesc = pinfo.get("description", "")
                        is_req = pname in required
                        req_mark = " **(必填)**" if is_req else ""
                        cmd_params.append(f"  - `{pname}` ({ptype}){req_mark} — {pdesc}")
                        if is_req:
                            cmd_required.append(pname)

                    lines.append(f"#### 子命令: `{cmd_name}`")
                    if cmd_required:
                        lines.append(f"必填参数: {', '.join(f'`{p}`' for p in cmd_required)}")
                    for cp in cmd_params:
                        lines.append(cp)
                    lines.append("")

                first_cmd = command_enum[0]
                example_args = {"command": f'"{first_cmd}"'}
                for pname, pinfo in props.items():
                    if pname == "command":
                        continue
                    if pname in required or any(
                        f"[{first_cmd}]" in (pinfo.get("description") or "") for pinfo in [props.get(pname)]
                    ):
                        example_args[pname] = f"<{pname}>"

                lines.append("**正确调用示例:**")
                lines.append("```json")
                lines.append(f'{{"command": "{first_cmd}", ')
                for k, v in list(example_args.items())[1:]:
                    lines.append(f' "{k}": {v},')
                lines.append(f' "_intent": "正在执行{first_cmd}"')
                lines.append(f"}}")
                lines.append("```")

            elif props:
                lines.append("**参数说明:**")
                for pname, pinfo in props.items():
                    ptype = pinfo.get("type", "string")
                    pdesc = pinfo.get("description", "")
                    is_req = pname in required
                    req_mark = " **(必填)**" if is_req else ""
                    enum = pinfo.get("enum")
                    enum_str = f"\n    可选值: {enum}" if enum else ""
                    lines.append(f"- `{pname}` ({ptype}){req_mark} — {pdesc}{enum_str}")

                lines.append("")
                lines.append("**正确调用示例:**")
                ex_props = {}
                for pname in props:
                    if pname in required:
                        ex_props[pname] = f"<{pname}>"
                lines.append("```json")
                lines.append(json.dumps(ex_props, ensure_ascii=False, indent=2))
                lines.append("```")

            lines.append("")
            lines.append("---")
            return "\n".join(lines)

        except Exception as e:
            logger.warning("[AutoHint] 生成工具帮助失败 %s: %s", tool_name, e)
            return ""

    @staticmethod
    def format_tool_help(tool_defs: List[Dict]) -> str:
        """将工具定义列表格式化为 LLM 可读的帮助文本"""
        parts = []
        for d in tool_defs:
            fn = d.get("function", {})
            name = fn.get("name", "?")
            desc = fn.get("description", "?")[:100] if fn.get("description") else ""
            params_obj = fn.get("parameters", {}).get("properties", {})
            req_list = fn.get("parameters", {}).get("required", [])
            param_strs = []
            for pn, pi in params_obj.items():
                mark = "*" if pn in req_list else ""
                pt = pi.get("type", "?")
                pd = (pi.get("description") or "")[:60]
                param_strs.append(f"  {pn}({pt}){mark}: {pd}")

            parts.append(f"### {name}\n{desc}\n参数:\n" + "\n".join(param_strs))

        return "\n\n".join(parts)
