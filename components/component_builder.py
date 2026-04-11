# -*- coding: utf-8 -*-
"""
组件生成器 — 让 LLM 能快速创建符合规范的新组件

能力：
  - generate: 交互式/参数式创建新组件（自动写入 components/）
  - list: 列出当前已加载的所有组件及其命令
  - info: 查看指定组件的详细信息（命令、源文件路径等）
  - template: 获取标准组件模板代码

★ 生成的组件自带 _cmd_help 方法，LLM 可通过 help 命令自查询用法
"""

import json
import os
from datetime import datetime
from typing import Any, Dict

from app.core.interface.base_cell import BaseCell
from app.core.util.components_loader import (
    get_all_cells,
    get_all_commands,
    get_cell,
    get_components_dir,
    discover_components,
)


class ComponentBuilder(BaseCell):
    """组件生成器 — 创建、管理、查看 Cellium 组件"""

    @property
    def cell_name(self) -> str:
        return "component"

    # ================================================================
    # 命令方法
    # ================================================================

    def _cmd_generate(
        self,
        name: str,
        description: str = "",
        commands: str = "",
    ) -> Dict[str, Any]:
        """
        生成一个新的组件文件并写入 components/ 目录
        
        生成的组件自动包含：
          - 完整的类 docstring
          - cell_name 属性（小写）
          - 所有 _cmd_ 命令方法 + docstring
          ★ _cmd_help 方法（LLM 可自查询用法，减少连续错误）
        
        Args:
            name: 组件名称（小写英文，如 calculator、hash_tool）
            description: 组件功能描述，一句话说明这个组件做什么
            commands: 需要的命令列表（JSON 数组格式），每项包含 name/desc，
                      如 '[{"name":"run","desc":"执行主逻辑"}]'
                      省略时自动生成一个默认 _cmd_execute 命令
        
        Returns:
            {"success": True, "file_path": "...", "cell_name": "...", "commands": [...]}
        
        使用示例:
          component.generate("hash_tool", "文件哈希计算器")
          component.generate("batch_rename", "批量重命名工具", 
            commands='[{"name":"rename","desc":"执行批量重命名"},{"name":"preview","desc":"预览重命名结果"}]')
        """

        # 校验名称
        if not name or not name.isidentifier():
            return {
                "error": f"无效的组件名称 '{name}'：必须是合法 Python 标识符",
                "hint": "使用小写英文和下划线，如 hash_tool、batch_rename、my_api",
            }

        cell_name = name.lower()
        class_name = "".join(word.capitalize() for word in name.split("_"))

        # 解析命令定义
        cmd_list = []
        if commands and commands.strip():
            try:
                parsed = json.loads(commands)
                if isinstance(parsed, list):
                    cmd_list = parsed
            except json.JSONDecodeError:
                return {"error": f"commands 参数不是有效的 JSON: {commands[:100]}"}
        
        if not cmd_list:
            cmd_list = [{"name": "execute", "desc": f"执行{description or cell_name}的主要功能"}]

        components_dir = get_components_dir()
        file_path = components_dir / f"{name}.py"

        # 文件已存在检查
        if file_path.exists():
            return {
                "error": f"组件已存在: {file_path}",
                "hint": "请换一个名字，或先删除已有文件",
                "existing_file": str(file_path),
            }

        # ── 生成命令方法代码 ──
        methods_parts = []
        for cmd in cmd_list:
            cn = cmd.get("name", "execute")
            cd = cmd.get("desc", f"执行 {cn} 操作")
            methods_parts.append(f'''
    def _cmd_{cn}(self, input_data: str) -> Dict[str, Any]:
        """
        {cd}
        
        Args:
            input_data: 输入数据
            
        Returns:
            {{"result": 处理结果}}
        """
        # TODO: 实现 {cn} 的具体逻辑
        return {{"status": "ok", "message": "{cn} 功能待实现"}}
''')

        methods_code = "\n".join(methods_parts)

        # 生成 _cmd_help 方法（核心！让 LLM 自学习用法）──
        examples_parts = []
        for i, cmd in enumerate(cmd_list):
            cn = cmd.get("name", "execute")
            cd = cmd.get("desc", "")
            comma = "," if i < len(cmd_list) - 1 else ""
            examples_parts.append(
                f'        {{"command": "{cn}", "args": {{"input_data": "<{cn}的输入>"}}, '
                f'"description": "{cd}"}}{comma}'
            )
        
        help_method = '''
    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """查询组件使用帮助（LLM 可通过此命令了解如何正确调用组件）
        
        Args:
            topic: 具体主题/命令名（留空返回完整总览）
            
        Returns:
            组件的详细使用说明、参数格式、示例、注意事项
        """
        commands = self.get_commands()
        base_info = {
            "name": self.cell_name,
            "description": """''' + (description or cell_name) + '''""",
            "available_commands": commands,
            "command_count": len(commands),
            "usage_examples": [
''' + "\n".join(examples_parts) + '''
            ],
            "_notes": [
                "此组件由 LLM 通过 component.generate() 创建",
                "每个命令需要先实现具体逻辑才能返回有意义的结果",
                "使用前请用 file.edit 编辑本文件补充实现代码",
                "调用时必须带 command 字段指定子命令名",
                "如果不确定如何调用，可先调 help 查看用法",
            ],
            "_call_format": {
                "note": "这是多命令模式工具，每次调用必须带 command 字段",
                "example": {{"command": "<子命令名>", "input_data": "<参数>"}},
                "or_query_help": '{self.cell_name}.help(topic="<命令名>") 可查看某命令详情',
            },
        }
        
        if topic and topic in commands:
            return {**base_info,
                     "focused_command": topic,
                     "command_description": commands[topic],
                     "hint": f'调用示例: 调用 {self.cell_name} 工具时使用 command="{topic}"'}
        return base_info
'''

        # ── 组装完整文件内容 ──
        now = datetime.now().strftime("%Y-%m-%d")
        desc_escaped = (description or cell_name).replace('"', '\\"')
        
        lines = []
        lines.append('# -*- coding: utf-8 -*-')
        lines.append('"""')
        lines.append(desc_escaped + ' \u2014 Cellium \u7ec4\u4ef6')
        lines.append('')
        lines.append('\u521b\u5efa\u65f6\u95f4: ' + now)
        lines.append('\u81ea\u52a8\u751f\u6210 by ComponentBuilder')
        lines.append('')
        lines.append('[\u89c4\u8303\u68c0\u67e5\u6e05\u5355 \u2705]')
        lines.append('  \u2713 \u7ee7\u627f BaseCell')
        lines.append('  \u2713 cell_name \u5df2\u5b9a\u4e59 (\u5c0f\u5199)')
        lines.append('  \u2713 \u547d\u4ee4\u65b9\u6cd5 \u4ee5 _cmd_ \u524d\u7f00\u5f00\u5934')
        lines.append('  \u2713 \u6bcf\u4e2a\u547d\u4eee\u90fd\u6709 docstring')
        lines.append('  \u2713 \u63d0\u4f9b _cmd_help \u65b9\u6cd5\u4f9b LLM \u67e5\u8be2\u7528\u6cd5')
        lines.append('"""')
        lines.append('')
        lines.append('from typing import Any, Dict')
        lines.append('from app.core.interface.base_cell import BaseCell')
        lines.append('')
        lines.append('')
        lines.append(f'class {class_name}(BaseCell):')
        lines.append('    """')
        lines.append(f'    {desc_escaped}')
        lines.append('    ')
        lines.append(f'    \u529f\u80fd\u8bf4\u660e: {desc_escaped or "\u7531 LLM \u81ea\u52a8\u751f\u6210\u7684\u7ec4\u4ef6"}')
        lines.append('    """')
        lines.append('')
        lines.append('    @property')
        lines.append('    def cell_name(self) -> str:')
        lines.append('        """\u7ec4\u4ef6\u6807\u8bc6\uff08\u5c0f\u5199\uff09\u2014 \u7528\u4e8e\u5168\u5c40\u552f\u4e00\u8bc6\u522b\u548c\u547d\u4ee4\u8def\u7531"""')
        lines.append(f'        return "{cell_name}"')
        
        # 追加方法
        for mp in [m.strip() for m in methods_code.split('\n') if m.strip()]:
            lines.append(mp)
        
        for hm in [h.strip() for h in help_method.split('\n') if h.strip()]:
            lines.append(hm)

        lines.append('')
        lines.append('    def on_load(self):')
        lines.append('        """\u7ec4\u4ef6\u88ab\u52a0\u8f7d\u540e\u8c03\u7528"""')
        lines.append('        super().on_load()')

        file_content = "\n".join(lines)

        # 写入文件
        try:
            os.makedirs(components_dir, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(file_content)
        except Exception as e:
            return {"error": f"\u5199\u5165\u6587\u4ef6\u5931\u8d25: {e}"}

        return {
            "success": True,
            "message": f"\u7ec4\u4ef6 '{cell_name}' \u5df2\u521b\u5efa\uff01",
            "file_path": str(file_path),
            "cell_name": cell_name,
            "class_name": class_name,
            "module_path": f"components.{name}.{class_name}",
            "commands": [{c["name"]: c["desc"]} for c in cmd_list],
            "has_help_method": True,
            "next_step": (
                "\u7ec4\u4ef6\u5c06\u5728\u70ed\u63d2\u62cb\u7cfb\u7edf\u68c0\u6d4b\u5230\u540e\u81ea\u52a8\u52a0\u8f7d\uff08\u7ea73\u79d2\u5185\u751f\u6548\uff09"
                "\uff0c\u6216\u8c03\u7528 component.reload() \u7acb\u5373\u52a0\u8f7d"
            ),
        }

    def _cmd_list(self, show_commands: bool = True) -> Dict[str, Any]:
        """
        列出所有已注册的组件及其命令
        
        Args:
            show_commands: 是否显示每个组件的命令详情（默认 true）
            
        Returns:
            {"components": [{name, class, command_count, commands, is_tool}], "total": N}
        
        使用: component.list() 或 component.list(show_commands=false)
        """
        all_cells = get_all_cells()
        all_cmds = get_all_commands()

        registered_tools = set()
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()
            registered_tools = set(reg.get_all_names())
        except Exception:
            pass

        result = []
        for cname, cell in sorted(all_cells.items()):
            cmds = all_cmds.get(cname, {})
            entry = {
                "name": cname,
                "class": type(cell).__name__,
                "command_count": len(cmds),
                "source_file": getattr(cell, "_source_file", "\u672a\u77e5"),
                "is_tool_registered": cname in registered_tools,
                "has_help": hasattr(cell, "_cmd_help"),
            }
            if show_commands:
                entry["commands"] = cmds
            result.append(entry)

        return {
            "components": result,
            "total": len(result),
            "tools_registered": len(registered_tools),
            "components_dir": str(get_components_dir()),
        }

    def _cmd_info(self, name: str) -> Dict[str, Any]:
        """查看指定组件的详细信息"""
        cell = get_cell(name)
        if not cell:
            discovered = discover_components()
            for item in discovered:
                if item.get("class_name", "").lower() == name.lower():
                    return {
                        "name": name,
                        "status": "discovered_but_not_loaded",
                        "class": item.get("class_name"),
                        "file": item.get("file"),
                        "module_path": item.get("module_path"),
                        "is_new": item.get("is_new"),
                        "hint": "\u8be5\u7ec4\u4ef6\u5df2\u88ab\u53d1\u73b0\u4f46\u5c1a\u672a\u52a0\u8f7d\uff0c\u53ef\u5c1d\u8bd5 component.reload()",
                    }
            return {"error": f"\u672a\u627e\u5230\u7ec4\u4ef6: {name}", "available": list(get_all_cells().keys())}

        cmds = cell.get_commands()
        source_file = getattr(cell, "_source_file", None)
        code_preview = None
        if source_file and os.path.exists(source_file):
            try:
                with open(source_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    code_preview = "".join(lines[:50])
                    if len(lines) > 50:
                        code_preview += f"\n... (\u5171 {len(lines)} \u884c)"
            except Exception:
                pass

        return {
            "name": cell.cell_name,
            "class": type(cell).__name__,
            "source_file": source_file,
            "loaded": True,
            "commands": cmds,
            "command_count": len(cmds),
            "has_help": hasattr(cell, "_cmd_help"),
            "code_preview": code_preview,
        }

    def _cmd_template(self, style: str = "minimal") -> Dict[str, Any]:
        """
        获取组件模板代码
        
        Args:
            style: minimal / full / example
            
        Returns:
            {"template": 代码文本, "style": 风格名}
        """
        templates = {}
        templates["minimal"] = '''# -*- coding: utf-8 -*-
"""\u6211\u7684\u7ec4\u4ef6"""

from typing import Any, Dict
from app.core.interface.base_cell import BaseCell


class MyComponent(BaseCell):
    ""\u7ec4\u4ef2\u63cf\u8ff0""

    @property
    def cell_name(self) -> str:
        return "my_component"

    def _cmd_do_something(self, input_text: str) -> Dict[str, Any]:
        ""\u6267\u884c\u64cd\u4f5c""
        return {"result": f"\u5904\u7406\u4e86: {input_text}"}

    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        ""\u67e5\u8be2\u7528\u6cd5\u5e2e\u52a9""
        cmds = self.get_commands()
        return {"name": self.cell_name, "commands": cmds, "notes": ["\u586b\u5199\u8be6\u7ec6\u7528\u6cd5"]}
'''

        templates["full"] = None  # 太长不内联，下面动态读取
        templates["example"] = None

        if style == "full":
            full_path = get_components_dir() / "_example_component.py"
            if full_path.exists():
                with open(full_path, "r", encoding="utf-8") as f:
                    templates["full"] = f.read()
            else:
                templates["full"] = templates["minimal"]

        if style == "example":
            example_path = get_components_dir() / "_example_component.py"
            if example_path.exists():
                with open(example_path, "r", encoding="utf-8") as f:
                    templates["example"] = f.read()
            else:
                templates["example"] = templates["minimal"]

        template = templates.get(style, templates["minimal"])
        return {"template": template, "style": style, "available_styles": ["minimal", "full", "example"]}

    def _cmd_reload(self) -> Dict[str, Any]:
        """手动触发热重载扫描"""
        from app.core.di.container import get_container as get_di
        from app.core.util.components_loader import hot_reload

        container = None
        try:
            container = get_di()
        except Exception:
            pass

        report = hot_reload(container=container)

        tool_count = -1
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()
            reg.sync_from_components_loader()
            tool_count = reg.size
            report["_tool_registry_synced"] = True
        except Exception as e:
            report["_tool_registry_synced"] = False

        added = report.get("added", [])
        removed = report.get("removed", [])

        summary = []
        if added:
            summary.append(f"+ \u65b0\u589e {len(added)} \u4e2a: {[a['name'] for a in added]}")
        if removed:
            summary.append(f"- \u79fb\u9664 {len(removed)} \u4e2a: {[r['name'] for r in removed]}")
        if not added and not removed:
            summary.append("\u65e0\u53d8\u5316")

        result = {
            "status": "ok",
            "report": report,
            "summary": "; ".join(summary),
            "total_loaded": len(get_all_cells()),
        }
        
        if tool_count >= 0:
            result["tools_registered"] = tool_count
            result["note"] = f"\u65b0\u7ec4\u4ef6\u5df2\u6ce8\u518c\u4e3a LLM \u53ef\u8c03\u7528\u5de5\u5177\uff0c\u5f53\u524d\u5171 {tool_count} \u4e2a\u7ec4\u4ef6\u5de5\u5177"

        return result

    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """查询组件使用帮助（LLM 可通过此命令了解如何正确调用 component 工具）
        
        Args:
            topic: 具体命令名（留空返回完整总览）
            
        Returns:
            组件的详细使用说明、参数格式、示例、注意事项
        """
        commands = self.get_commands()
        base_info: Dict[str, Any] = {
            "name": self.cell_name,
            "description": "组件生成器 — 让 LLM 能快速创建符合规范的新组件",
            "available_commands": commands,
            "command_count": len(commands),
            "usage_examples": [
                {"command": "generate", "args": {"name": "<组件名>", "description": "<一句话描述>", "commands": "[{name,desc}...]"}, "description": "创建新组件文件"},
                {"command": "list", "args": {"show_commands": True}, "description": "列出所有已加载组件"},
                {"command": "info", "args": {"name": "<组件名>"}, "description": "查看指定组件详情"},
                {"command": "reload", "args": {}, "description": "手动触发热重载扫描"},
                {"command": "template", "args": {"style": "minimal|full|example"}, "description": "获取标准模板代码"},
            ],
            "_notes": [
                "这是系统内置组件（白名单豁免），负责创建和管理其他 Cellium 组件",
                "generate 创建的组件会自动包含 _cmd_help 方法供 LLM 自学习用法",
                "每次调用必须带 command 字段指定子命令名",
                "调用格式: {\"command\": \"generate\", \"name\": \"my_tool\", ...}",
                "如果不确定如何调用，可先调 help 查看用法或调 list 查看可用命令",
            ],
            "_call_format": {
                "note": "多命令模式工具，每次调用必须带 command 字段",
                "example": {"command": "<子命令名>", "<param>": "<值>"},
                "or_query_help": f'{self.cell_name}.help(topic="<命令名>") 可查看某命令详情',
            },
        }

        if topic and topic in commands:
            cmd_help_map: Dict[str, Dict[str, Any]] = {
                "generate": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "required_params": ["name"],
                    "optional_params": ["description", "commands"],
                    "hint": (
                        '调用示例: 调用 component 工具时使用 command="generate"\n'
                        '  - name (必填): 组件名，小写英文如 hash_tool、batch_rename\n'
                        '  - description (选填): 一句话说明功能\n'
                        '  - commands (选补): JSON数组 [{name,desc}] 定义命令列表\n'
                        '\n'
                        '示例: {"command":"generate","name":"calc","description":"计算器"}'
                    ),
                },
                "list": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": (
                        '调用示例: {"command":"list"} 或 {"command":"list","list_show_commands":true}\n'
                        '注意：使用 list_show_commands 参数而非 show_commands'
                    ),
                },
                "info": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": '调用示例: {"command":"info","info_name":"<组件名>"}',
                },
                "reload": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": '调用示例: {"command":"reload"}',
                },
                "template": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": '调用示例: {"command":"template","style":"full"}',
                },
            }
            return {**base_info, **cmd_help_map.get(topic, {})}
        return base_info

    def on_load(self):
        super().on_load()
