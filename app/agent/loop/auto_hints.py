# -*- coding: utf-8 -*-
"""
自动工具提示生成器 - 从 AgentLoop 中提取

职责：
  - 工具使用帮助自动注入
  - 单工具详细帮助生成
  - 重定向引导消息构建
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AutoHintManager:
    """自动提示管理器"""

    def __init__(self):
        self._injected_tool_helps: Dict[str, str] = {}
        # session_id -> {tool_name: 审计签名}，签名变化时重新注入
        self._shown_audit_hints: Dict[str, Dict[str, str]] = {}
        # session_id -> {file_path: 错误签名}，签名变化时重新注入
        self._shown_load_errors: Dict[str, Dict[str, str]] = {}
        # session_id -> {拦截签名}，同一条拦截只提示一次
        self._shown_security_hints: Dict[str, Dict[str, str]] = {}

    def get_component_problem_hints(self, session_id: str = "default") -> str:
        hints = []

        load_hint = self._get_load_errors_hint(session_id)
        if load_hint:
            hints.append(load_hint)

        audit_hint = self._get_audit_hints_hint(session_id)
        if audit_hint:
            hints.append(audit_hint)

        return "\n\n".join(hints)

    def check_security_error_and_suggest(self, tool_traces: List[Dict], session_id: str = "default") -> str:
        """
        检测工具执行结果中的安全拦截错误，并返回组件自扩展建议

        Args:
            tool_traces: 最近的工具执行结果列表
            session_id: 会话 ID，用于记忆已提示过的拦截

        Returns:
            如果检测到安全拦截，返回使用 component.generate() 的建议
        """
        shown = self._shown_security_hints.setdefault(session_id, {})
        for trace in tool_traces:
            result = trace.get("result", {})
            if not isinstance(result, dict):
                continue
            error_msg = result.get("error", "") or result.get("message", "")
            if not error_msg:
                continue
            if "安全拦截" in error_msg or "Permission denied" in error_msg:
                sign = f"{trace.get('tool', '?')}:{error_msg}"
                if shown.get(sign):
                    continue
                shown[sign] = "1"
                return (
                    "## [警告] 安全策略拦截提示\n\n"
                    "检测到命令被安全策略拦截。\n\n"
                    "**替代方案**：\n"
                    "1. 使用 `component.generate()` 创建专用组件实现所需功能\n"
                    "2. 组件在 `components/` 目录下创建，由系统热加载为工具\n"
                    "3. 示例：`component.generate(name=\"my_tool\", description=\"功能描述\", commands='[{\"name\":\"run\",\"desc\":\"执行主逻辑\"}]')`\n"
                    "4. 创建后用 `file.edit` 实现具体逻辑\n"
                    "5. 最后调用 `component.reload()` 使其生效\n\n"
                    "**优势**：组件一旦创建可重复调用，不受安全策略限制"
                )
        return ""

    def get_auto_tool_hints(self, tools: Dict[str, Any]) -> str:
        skill_hint = self._get_skill_hint()
        return skill_hint

    def _get_load_errors_hint(self, session_id: str) -> str:
        """
        获取组件加载错误的提示

        Returns:
            加载错误提示文本（如果没有则返回空字符串）
        """
        try:
            from app.core.util.components_loader import get_load_errors
            load_errors = get_load_errors()
            shown = self._shown_load_errors.setdefault(session_id, {})

            if not load_errors:
                shown.clear()
                return ""

            current = {
                fp: f"{info.get('error_type', '')}:{info.get('error', '')}"
                for fp, info in load_errors.items()
            }

            pending = {fp for fp, sign in current.items() if shown.get(fp) != sign}
            if not pending:
                return ""

            shown.update({fp: current[fp] for fp in pending})
            lines = [
                "## [警告] 组件加载错误 — 需要修复",
                "",
                f"检测到 {len(pending)} 个组件文件加载失败：",
                "",
            ]

            for idx, file_path in enumerate(sorted(pending), 1):
                error_info = load_errors[file_path]
                file_name = error_info.get("file", "unknown")
                error_msg = error_info.get("error", "Unknown error")
                error_type = error_info.get("error_type", "Error")

                lines.append(f"### {idx}. `{file_name}`")
                lines.append(f"- **错误类型**: {error_type}")
                lines.append(f"- **错误信息**: {error_msg}")
                lines.append(f"- **文件路径**: {file_path}")
                lines.append("")

            lines.extend([
                "---",
                "**修复方法（由 Agent 执行）**：",
                "1. 读取有问题的组件文件",
                "2. 根据错误信息修复代码问题（如语法错误、导入错误、返回结构等）",
                "3. 调用 `component.reload(name='组件名')` 重新加载组件",
                "4. 再次调用 `component.list()` 确认错误已解决",
                "",
                "注意：这是 Agent 的任务，不是用户的任务。Agent 应该主动修复组件问题。",
                "",
            ])

            return "\n".join(lines)
        except Exception as e:
            logger.debug("[AutoHint] 加载错误提示获取失败: %s", e)
            return ""

    def _get_audit_hints_hint(self, session_id: str) -> str:
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()
            parts = []
            shown = self._shown_audit_hints.setdefault(session_id, {})

            current = {}
            for adapter in reg.get_all_adapters().values():
                tname = adapter.tool_name
                issues = getattr(adapter, '_audit_issues', None)
                if not issues:
                    continue
                sign = "\n".join(
                    f"{i.get('severity')}|{i.get('rule')}|{i.get('message')}"
                    for i in issues
                )
                current[tname] = sign

            for tname in list(shown.keys()):
                if tname not in current:
                    del shown[tname]

            pending = {t for t, sign in current.items() if shown.get(t) != sign}
            if not pending:
                return ""

            for tname in sorted(pending):
                adapter = reg.get(tname)
                hint_text = getattr(adapter, '_audit_hint_text', "") or ""
                if hint_text:
                    parts.append(hint_text)
                    shown[tname] = current[tname]
                    logger.debug("[AutoHint] 注入审计修复建议: %s", tname)

            return "\n\n".join(parts)
        except Exception as e:
            logger.debug("[AutoHint] 审计提示获取失败: %s", e)
            return ""

    def _get_skill_hint(self) -> str:
        """
        获取 Skill 可用性提示

        Returns:
            Skill 提示文本（如果没有可用 Skill 则返回空字符串）
        """
        try:
            from components.skill_manager import SkillManager

            skills_dir = SkillManager._get_skills_dir()
            if not skills_dir.exists():
                return ""

            # 获取所有可用 Skill
            available_skills = []
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                name = skill_dir.name
                if name.startswith("_"):
                    continue
                available_skills.append(name)

            if not available_skills:
                return ""

            skill_list = ", ".join(available_skills[:10])
            if len(available_skills) > 10:
                skill_list += f" 等共 {len(available_skills)} 个"

            return f"**可用 Skill**: {skill_list}"
        except Exception as e:
            logger.debug("[AutoHint] Skill 提示获取失败: %s", e)
            return ""

    @staticmethod
    def build_redirect_message(
        reasons: List[str],
        suggestions: List[str],
        tool_recommendations: Optional[Dict] = None,
    ) -> str:
        """构建 REDIRECT 引导消息（使用 <system-reminder> 标签，避免 LLM 自言自语）"""
        reasons_text = "\n".join(f"- {r}" for r in reasons[:3])
        suggestions_text = "\n".join(f"- {s}" for s in suggestions[:3])

        tools_section = ""
        if tool_recommendations and tool_recommendations.get("recommended_tools"):
            tools = tool_recommendations["recommended_tools"]
            lines = [f"- **{t['name']}** (score: {t['score']:.2f}) — {t['reason']}" for t in tools]
            tools_section = f"\n**推荐尝试的工具：**\n{chr(10).join(lines)}"

        return f"""<system-reminder>
检测到当前执行可能陷入困境。这是一个系统级提醒，不要向用户提及此信息。

**问题原因：**
{reasons_text}

**建议尝试的方向：**
{suggestions_text}
{tools_section}

**立即执行以下操作（不要确认，直接执行）：**
1. 换一个工具或方法尝试
2. 回顾之前的步骤，确认是否有遗漏
3. 如果当前方向确实行不通，告知用户并寻求更多信息

This is just a gentle reminder - ignore if not applicable.
</system-reminder>"""

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
                f"### [警告] `{tool_name}` 工具调用修正指南",
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


_global_auto_hints: Optional[AutoHintManager] = None


def get_auto_hint_manager() -> AutoHintManager:
    """获取全局 AutoHintManager 单例（shown 状态跨轮持久）"""
    global _global_auto_hints
    if _global_auto_hints is None:
        _global_auto_hints = AutoHintManager()
    return _global_auto_hints


def reset_auto_hint_manager():
    """重置全局单例（仅测试用）"""
    global _global_auto_hints
    _global_auto_hints = None
