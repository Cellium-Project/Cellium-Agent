# -*- coding: utf-8 -*-
"""
组件生成器 — 让 LLM 能快速创建、管理、安装新组件

能力：
  - generate: 交互式/参数式创建新组件（自动写入 components/）
  - list: 列出当前已加载的所有组件及其命令
  - info: 查看指定组件的详细信息（命令、源文件路径等）
  - template: 获取标准组件模板代码
  - market_search: 在组件市场（components.cellium.top）按关键词/分类搜索
  - market_info: 读取市场组件的完整元信息（版本、安装包地址、README）
  - market_install: 按元信息下载安装包 → 审查 → 安装并热重载

生成的组件自带 _cmd_help 方法，LLM 可通过 help 命令自查询用法
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional, Union

from app.core.interface.base_cell import BaseCell
from app.core.util.components_loader import (
    get_all_cells,
    get_all_commands,
    get_cell,
    get_components_dir,
    discover_components,
    get_load_errors,
)


def _resolve_cell_name(raw_name: str) -> str:
    from app.agent.tools.base_tool import sanitize_tool_name

    raw = (raw_name or "").strip()
    if not raw:
        return ""
    names = [n for n in (getattr(c, "cell_name", "") for c in get_all_cells().values()) if n]
    if raw in names:
        return raw
    low = raw.lower()
    for n in names:
        if n.lower() == low:
            return n
    for n in names:
        if sanitize_tool_name(n).lower() == low:
            return n
    return raw


class ComponentBuilder(BaseCell):
    """组件生成器 — 创建、管理、安装 Cellium 组件。除了本地生成/查看组件，还能从官方组件市场（components.cellium.top）搜索组件、读取元信息并直接安装：market_search 按关键词/分类搜索 → market_info 查看版本与安装包地址 → market_install 下载、审查并热重载。"""

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
          - _cmd_help 方法（LLM 可自查询用法，减少连续错误）
        
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

        if not name or not name.isidentifier():
            return {
                "error": f"无效的组件名称 '{name}'：必须是合法 Python 标识符",
                "hint": "使用小写英文和下划线，如 hash_tool、batch_rename、my_api",
            }

        cell_name = name.lower()
        class_name = "".join(word.capitalize() for word in name.split("_"))

        cmd_list = []
        if commands and commands.strip():
            try:
                parsed = json.loads(commands)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, str):
                            cmd_list.append({"name": item, "desc": f"执行 {item} 操作"})
                        elif isinstance(item, dict):
                            cmd_list.append(item)
            except json.JSONDecodeError:
                return {
                    "error": "commands 参数格式错误：必须是 JSON 数组格式",
                    "hint": (
                        '正确格式示例: commands=\'[{"name":"run","desc":"执行主逻辑"}]\'\n'
                        '或 commands=\'["do_something", "check"]\'（简单字符串列表自动生成描述）'
                    ),
                    "received": commands[:200],
                    "received_type": "string (not JSON)",
                }

        if not cmd_list:
            cmd_list = [{"name": "execute", "desc": f"执行{description or cell_name}的主要功能"}]

        for cmd in cmd_list:
            cn = cmd.get("name", "execute")
            if not cn or not cn.isidentifier():
                return {
                    "error": f"无效的命令名称 '{cn}'：必须是合法 Python 标识符",
                    "hint": "命令名只能包含字母、数字和下划线，且不能以数字开头",
                    "example": '正确的 commands 格式: [{"name":"run","desc":"执行主逻辑"}]',
                }

        components_dir = get_components_dir()
        file_path = components_dir / f"{name}.py"

        if file_path.exists():
            return {
                "error": f"组件已存在: {file_path}",
                "hint": "请换一个名字，或先删除已有文件",
                "existing_file": str(file_path),
            }

        methods_parts = []
        for cmd in cmd_list:
            cn = cmd.get("name", "execute")
            cd = cmd.get("desc", f"执行 {cn} 操作")
            cd_escaped = cd.replace('"', '\\"').replace("'''", "\\'''")
            method = f'''    def _cmd_{cn}(self, input_data: str = "", **kwargs) -> Dict[str, Any]:
        """
        {cd_escaped}

        Args:
            input_data: 输入数据
            **kwargs: 保留参数（如需额外参数请直接添加为命名参数，不要通过 kwargs 字典传入）

        Returns:
            {{"result": 处理结果}}
        """
        # TODO: 实现 {cn} 的具体逻辑
        return {{"success": True, "result": "{cn} 功能待实现"}}
'''
            methods_parts.append(method)

        methods_code = "\n".join(methods_parts)

        agent_helper = '''
    def _trigger_agent(self, message: str, session_id: str = None, event_type: str = "background_trigger", event_data: dict = None) -> Dict[str, Any]:
        """
        主动触发 Agent 执行任务

        推送消息到指定 session 的 Agent 对话，类似定时任务的机制。
        - 如果该 session 有运行中的任务，消息会追加到当前任务
        - 如果没有运行中的任务，会启动新任务

        Args:
            message: 要 Agent 处理的消息内容
            session_id: 目标会话 ID（必须指定）
            event_type: 事件类型标识（可选，默认 background_trigger）
            event_data: 额外的事件数据（可选）

        Returns:
            {"success": True, "result": {...}} 或 {"success": False, "error": "..."}
        """
        if not session_id:
            self.logger.warning(f"[{self.cell_name}] _trigger_agent 需要 session_id 参数")
            return {"success": False, "error": "session_id is required"}

        import httpx

        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()
            host = cfg.get("server.host", "127.0.0.1")
            port = cfg.get("server.port", 18000)
            base_url = f"http://{host}:{port}"
        except Exception:
            base_url = "http://127.0.0.1:18000"

        try:
            response = httpx.post(
                f"{base_url}/api/component/event",
                json={
                    "session_id": session_id,
                    "message": message,
                    "source": self.cell_name,
                    "event_type": event_type,
                    "event_data": event_data or {},
                },
                timeout=10.0
            )
            result = response.json()
            self.logger.info(f"[{self.cell_name}] 已触发 Agent | session={session_id} | status={result.get('status')}")
            return {"success": True, "result": result}
        except Exception as e:
            self.logger.error(f"[{self.cell_name}] 触发 Agent 失败: {e}")
            return {"success": False, "error": str(e)}
'''

        examples_parts = []
        for i, cmd in enumerate(cmd_list):
            cn = cmd.get("name", "execute")
            cd = cmd.get("desc", "").replace('"', '\\"')
            comma = "," if i < len(cmd_list) - 1 else ""
            examples_parts.append(
                f'                {{"command": "{cn}", "args": {{"input_data": "<{cn}的输入>"}}, '
                f'"description": "{cd}"}}{comma}'
            )

        examples_code = "\n".join(examples_parts)

        help_method = f'''    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """查询组件使用帮助（LLM 可通过此命令了解如何正确调用组件）

        Args:
            topic: 具体主题/命令名（留空返回完整总览）

        Returns:
            组件的详细使用说明、参数格式、示例、注意事项
        """
        commands = self.get_commands()
        base_info = {{
            "name": self.cell_name,
            "description": """{description or cell_name}""",
            "available_commands": commands,
            "command_count": len(commands),
            "usage_examples": [
                {examples_code}
            ],
            "_notes": [
                "此组件由 LLM 通过 component.generate() 创建",
                "每个命令需要先实现具体逻辑才能返回有意义的结果",
                "使用前请用 file.edit 编辑本文件补充实现代码",
                "调用时必须带 command 字段指定子命令名",
                "如果不确定如何调用，可先调 help 查看用法",
                "【调用 Agent 循环】如需触发 Agent 执行任务，可调用 self._trigger_agent(message, session_id)",
                "  示例: result = self._trigger_agent('帮我分析这个日志文件', session_id='<会话ID>')",
                "  返回: {{'success': True, 'result': {{...}}}}",
            ],
            "_call_format": {{
                "note": "这是多命令模式工具，每次调用必须带 command 字段",
                "example": {{"command": "<子命令名>", "input_data": "<参数>"}},
                "or_query_help": f'{{self.cell_name}}.help(topic="<命令名>") 可查看某命令详情',
            }},
        }}

        if topic and topic in commands:
            return {{**base_info,
                     "focused_command": topic,
                     "command_description": commands[topic],
                     "hint": f'调用示例: 调用 {{self.cell_name}} 工具时使用 command="{{topic}}"'}}
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
        lines.append('')

        for line in methods_code.split('\n'):
            lines.append(line)

        lines.append('')

        for line in agent_helper.split('\n'):
            lines.append(line)

        lines.append('')

        for line in help_method.split('\n'):
            lines.append(line)

        lines.append('')
        lines.append('    def on_load(self):')
        lines.append('        """\u7ec4\u4ef6\u88ab\u52a0\u8f7d\u540e\u8c03\u7528"""')
        lines.append('        super().on_load()')

        file_content = "\n".join(lines)

        try:
            compile(file_content, file_path.name, 'exec')
        except SyntaxError as e:
            return {
                "success": False,
                "error": f"生成的组件代码有语法错误: {e}",
                "error_line": e.lineno,
                "error_text": e.text,
                "hint": "请检查 commands 参数或其他输入是否包含特殊字符导致代码格式错误",
            }

        # 写入文件
        try:
            os.makedirs(components_dir, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(file_content)
        except Exception as e:
            return {"success": False, "error": f"\u5199\u5165\u6587\u4ef6\u5931\u8d25: {e}"}

        # 自动触发重载
        try:
            from app.core.util.components_loader import hot_reload
            from app.core.di.container import get_container as get_di_container
            try:
                container = get_di_container()
            except Exception:
                container = None
            reload_result = hot_reload(container=container)
            
            try:
                from app.core.util.component_tool_registry import get_component_tool_registry
                tool_registry = get_component_tool_registry()
                tool_registry.sync_from_components_loader()
            except Exception:
                pass
                
            return {
                "success": True,
                "message": f"\u7ec4\u4ef6 '{cell_name}' \u5df2\u521b\u5efa\u5e76\u52a0\u8f7d\uff01",
                "file_path": str(file_path),
                "cell_name": cell_name,
                "class_name": class_name,
                "module_path": f"components.{name}.{class_name}",
                "commands": [{c["name"]: c["desc"]} for c in cmd_list],
                "has_help_method": True,
                "has_agent_helper": True,
                "agent_helper_note": "组件包含 _trigger_agent(message, session_id) 方法，可触发 Agent 执行任务",
                "reload_result": reload_result,
            }
        except Exception as e:
            return {
                "success": True,
                "message": f"\u7ec4\u4ef6 '{cell_name}' \u5df2\u521b\u5efa\uff0c\u4f46\u91cd\u8f7d\u5931\u8d25: {e}",
                "file_path": str(file_path),
                "cell_name": cell_name,
                "hint": "\u8bf7\u624b\u52a8\u8c03\u7528 component.reload()",
            }

    def _cmd_generate_package(
        self,
        name: str,
        description: str = "",
        commands: str = "",
        modules: str = "",
    ) -> Dict[str, Any]:
        """
        \u751f\u6210\u4e00\u4e2a\u5305\u7ec4\u4ef6\uff08\u6587\u4ef6\u5939\u5f62\u5f0f\uff09\uff0c\u9002\u5408\u590d\u6742\u529f\u80fd

        \u5305\u7ec4\u4ef6\u7ed3\u6784:
          components/{name}/
          \u251c\u2014 __init__.py
          \u2514\u2014 core.py  (\u4e3b\u7ec4\u4ef6\u7c7b)

        \u989d\u5916\u6a21\u5757\u4f1a\u521b\u5efa\u4e3a\u72ec\u7acb .py \u6587\u4ef6

        Args:
            name: \u7ec4\u4ef6\u540d\u79f0\uff08\u5c0f\u5199\u82f1\u6587\uff0c\u5982 eth_quant\u3001data_pipeline\uff09
            description: \u7ec4\u4ef6\u529f\u80fd\u63cf\u8ff0
            commands: \u547d\u4ee4\u5217\u8868 JSON \u6570\u7ec4\uff0c\u5982 '[{"name":"run","desc":"\u6267\u884c"}]'
            modules: \u989d\u5916\u6a21\u5757\u5217\u8868 JSON \u6570\u7ec4\uff0c\u5982 '["utils","monitor","api"]'

        Returns:
            {"success": True, "package_dir": "...", "files": [...], "cell_name": "..."}

        \u4f7f\u7528\u793a\u4f8b:
          component.generate_package("eth_quant", "\u4ee5\u592a\u574a\u91cf\u5316\u4ea4\u6613")
          component.generate_package("data_pipeline", "\u6570\u636e\u5904\u7406\u7ba1\u9053",
            commands='[{"name":"run","desc":"\u8fd0\u884c\u7ba1\u9053"},{"name":"status","desc":"\u67e5\u770b\u72b6\u6001"}]',
            modules='["utils","monitor","api"]')
        """
        if not name or not name.isidentifier():
            return {
                "error": f"\u65e0\u6548\u7684\u7ec4\u4ef6\u540d\u79f0 '{name}'\uff1a\u5fc5\u987b\u662f\u5408\u6cd5 Python \u6807\u8bc6\u7b26",
                "hint": "\u4f7f\u7528\u5c0f\u5199\u82f1\u6587\u548c\u4e0b\u5212\u7ebf\uff0c\u5982 eth_quant\u3001data_pipeline",
            }

        cell_name = name.lower()
        class_name = "".join(word.capitalize() for word in name.split("_"))

        cmd_list = []
        if commands and commands.strip():
            try:
                parsed = json.loads(commands)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, str):
                            cmd_list.append({"name": item, "desc": f"\u6267\u884c {item} \u64cd\u4f5c"})
                        elif isinstance(item, dict):
                            cmd_list.append(item)
            except json.JSONDecodeError:
                return {
                    "error": "commands \u53c2\u6570\u683c\u5f0f\u9519\u8bef\uff1a\u5fc5\u987b\u662f JSON \u6570\u7ec4\u683c\u5f0f",
                    "hint": '\u6b63\u786e\u683c\u5f0f: commands=\'[{"name":"run","desc":"\u6267\u884c"}]\'',
                    "received": commands[:200],
                }

        if not cmd_list:
            cmd_list = [{"name": "execute", "desc": f"\u6267\u884c{description or cell_name}\u7684\u4e3b\u8981\u529f\u80fd"}]

        module_list = []
        if modules and modules.strip():
            try:
                parsed = json.loads(modules)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, str):
                            module_list.append(item)
            except json.JSONDecodeError:
                return {
                    "error": "modules \u53c2\u6570\u683c\u5f0f\u9519\u8bef\uff1a\u5fc5\u987b\u662f JSON \u6570\u7ec4\u683c\u5f0f",
                    "hint": '\u6b63\u786e\u683c\u5f0f: modules=\'["utils","monitor"]\'',
                }

        components_dir = get_components_dir()
        pkg_dir = components_dir / name

        if pkg_dir.exists():
            return {
                "error": f"\u7ec4\u4ef6\u5305\u5df2\u5b58\u5728: {pkg_dir}",
                "hint": "\u8bf7\u6362\u4e00\u4e2a\u540d\u5b57\uff0c\u6216\u5148\u5220\u9664\u5df2\u6709\u76ee\u5f55",
                "existing_dir": str(pkg_dir),
            }

        py_file = components_dir / f"{name}.py"
        if py_file.exists():
            return {
                "error": f"\u5b58\u5728\u540c\u540d\u5355\u6587\u4ef6\u7ec4\u4ef6: {py_file}",
                "hint": "\u5305\u7ec4\u4ef6\u4e0d\u80fd\u4e0e\u5355\u6587\u4ef6\u7ec4\u4ef6\u540c\u540d",
            }

        try:
            pkg_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"success": False, "error": f"\u521b\u5efa\u76ee\u5f55\u5931\u8d25: {e}"}

        created_files = []

        init_content = f'''# -*- coding: utf-8 -*-
"""
{description or cell_name} — Cellium \u5305\u7ec4\u4ef6

\u81ea\u52a8\u751f\u6210 by ComponentBuilder
"""

from .core import {class_name}

__all__ = ["{class_name}"]
'''
        init_path = pkg_dir / "__init__.py"
        try:
            with open(init_path, "w", encoding="utf-8") as f:
                f.write(init_content)
            created_files.append(str(init_path))
        except Exception as e:
            return {"success": False, "error": f"\u5199\u5165 __init__.py \u5931\u8d25: {e}"}

        methods_parts = []
        for cmd in cmd_list:
            cn = cmd.get("name", "execute")
            cd = cmd.get("desc", f"\u6267\u884c {cn} \u64cd\u4f5c")
            cd_escaped = cd.replace('"', '\\"').replace("'''", "\\'''")
            method = f'''    def _cmd_{cn}(self, input_data: str = "", **kwargs) -> Dict[str, Any]:
        """
        {cd_escaped}

        Args:
            input_data: \u8f93\u5165\u6570\u636e

        Returns:
            {{"success": True, "result": ...}}
        """
        # TODO: \u5b9e\u73b0 {cn} \u7684\u5177\u4f53\u903b\u8f91
        return {{"success": True, "result": "{cn} \u529f\u80fd\u5f85\u5b9e\u73b0"}}
'''
            methods_parts.append(method)

        methods_code = "\n".join(methods_parts)

        now = datetime.now().strftime("%Y-%m-%d")
        desc_escaped = (description or cell_name).replace('"', '\\"')

        core_lines = []
        core_lines.append('# -*- coding: utf-8 -*-')
        core_lines.append('"""')
        core_lines.append(desc_escaped + ' — \u6838\u5fc3\u6a21\u5757')
        core_lines.append('')
        core_lines.append('\u521b\u5efa\u65f6\u95f4: ' + now)
        core_lines.append('"""')
        core_lines.append('')
        core_lines.append('from typing import Any, Dict')
        core_lines.append('from app.core.interface.base_cell import BaseCell')
        for mod in module_list:
            core_lines.append(f'from .{mod} import *')
        core_lines.append('')
        core_lines.append('')
        core_lines.append(f'class {class_name}(BaseCell):')
        core_lines.append('    """')
        core_lines.append(f'    {desc_escaped}')
        core_lines.append('    ')
        core_lines.append(f'    \u529f\u80fd\u8bf4\u660e: {desc_escaped}')
        core_lines.append('    """')
        core_lines.append('')
        core_lines.append('    @property')
        core_lines.append('    def cell_name(self) -> str:')
        core_lines.append('        return "' + cell_name + '"')
        core_lines.append('')

        for line in methods_code.split('\n'):
            core_lines.append(line)

        core_lines.append('')
        core_lines.append('    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:')
        core_lines.append('        """\u67e5\u8be2\u7ec4\u4ef6\u4f7f\u7528\u5e2e\u52a9"""')
        core_lines.append('        return {')
        core_lines.append('            "name": self.cell_name,')
        core_lines.append('            "commands": self.get_commands(),')
        core_lines.append('            "description": """' + desc_escaped + '"""')
        core_lines.append('        }')
        core_lines.append('')
        core_lines.append('    def on_load(self):')
        core_lines.append('        """\u7ec4\u4ef6\u88ab\u52a0\u8f7d\u540e\u8c03\u7528"""')
        core_lines.append('        super().on_load()')

        core_content = "\n".join(core_lines)

        try:
            compile(core_content, "core.py", 'exec')
        except SyntaxError as e:
            import shutil
            shutil.rmtree(pkg_dir)
            return {
                "success": False,
                "error": f"\u751f\u6210\u7684\u7ec4\u4ef6\u4ee3\u7801\u6709\u8bed\u6cd5\u9519\u8bef: {e}",
            }

        core_path = pkg_dir / "core.py"
        try:
            with open(core_path, "w", encoding="utf-8") as f:
                f.write(core_content)
            created_files.append(str(core_path))
        except Exception as e:
            import shutil
            shutil.rmtree(pkg_dir)
            return {"success": False, "error": f"\u5199\u5165 core.py \u5931\u8d25: {e}"}

        for mod_name in module_list:
            if not mod_name.isidentifier():
                continue
            mod_content = f'''# -*- coding: utf-8 -*-
"""
{mod_name} \u6a21\u5757

\u81ea\u52a8\u751f\u6210 by ComponentBuilder
"""

# TODO: \u5b9e\u73b0\u6a21\u5757\u529f\u80fd

def example_function():
    """\u793a\u4f8b\u51fd\u6570"""
    return "Hello from {mod_name}"
'''
            mod_path = pkg_dir / f"{mod_name}.py"
            try:
                with open(mod_path, "w", encoding="utf-8") as f:
                    f.write(mod_content)
                created_files.append(str(mod_path))
            except Exception as e:
                pass

        try:
            from app.core.util.components_loader import hot_reload
            from app.core.di.container import get_container as get_di_container
            try:
                container = get_di_container()
            except Exception:
                container = None
            reload_result = hot_reload(container=container)

            try:
                from app.core.util.component_tool_registry import get_component_tool_registry
                tool_registry = get_component_tool_registry()
                tool_registry.sync_from_components_loader()
            except Exception:
                pass

            return {
                "success": True,
                "message": f"\u5305\u7ec4\u4ef6 '{cell_name}' \u5df2\u521b\u5efa\u5e76\u52a0\u8f7d\uff01",
                "package_dir": str(pkg_dir),
                "cell_name": cell_name,
                "class_name": class_name,
                "module_path": f"components.{name}.{class_name}",
                "files": created_files,
                "commands": [{c["name"]: c["desc"]} for c in cmd_list],
                "modules": module_list,
                "reload_result": reload_result,
                "note": "\u5305\u7ec4\u4ef6\u9002\u5408\u590d\u6742\u529f\u80fd\uff0c\u53ef\u4ee5\u5728\u5404\u6a21\u5757\u4e2d\u5206\u79bb\u5b9e\u73b0\u903b\u8f91",
            }
        except Exception as e:
            return {
                "success": True,
                "message": f"\u5305\u7ec4\u4ef6 '{cell_name}' \u5df2\u521b\u5efa\uff0c\u4f46\u91cd\u8f7d\u5931\u8d25: {e}",
                "package_dir": str(pkg_dir),
                "files": created_files,
                "hint": "\u8bf7\u624b\u52a8\u8c03\u7528 component.reload()",
            }

    def _cmd_list(
        self,
        show_commands: bool = False,
        search: str = "",
        page: int = 1,
        page_size: int = 27,
    ) -> Dict[str, Any]:
        """
        列出所有已注册的组件及其命令

        Args:
            show_commands: 是否显示每个组件的命令详情（默认 false）
            search: 按组件名模糊匹配（不区分大小写）
            page: 页码（从 1 开始）
            page_size: 每页数量（默认 27）

        Returns:
            {
                "components": [{name, class, command_count, commands, is_tool}],
                "total": N,
                "page": 当前页,
                "total_pages": 总页数,
                "has_next": 是否有下一页,
                "has_prev": 是否有上一页
            }

        使用:
            component.list()
            component.list(show_commands=false)
            component.list(search="file", page=1, page_size=5)
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

        all_components = []
        components_with_issues = []

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

            try:
                from app.core.util.component_tool_registry import get_component_tool_registry
                reg = get_component_tool_registry()
                adapter = reg.get(cname)
                if adapter and hasattr(adapter, '_audit_issues') and adapter._audit_issues:
                    entry["has_issues"] = True
                    entry["issue_count"] = len(adapter._audit_issues)
                    entry["audit_score"] = getattr(adapter, '_audit_score', 0)
                    entry["audit_hint"] = getattr(adapter, '_audit_hint_text', "")[:500]
                    components_with_issues.append(cname)
                else:
                    entry["has_issues"] = False
            except Exception:
                entry["has_issues"] = False

            if show_commands:
                entry["commands"] = cmds
            all_components.append(entry)

        if search:
            search_lower = search.lower()
            all_components = [
                c for c in all_components
                if search_lower in c["name"].lower()
            ]

        total = len(all_components)
        page_size = max(1, min(page_size, 50)) 
        page = max(1, page)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1

        if page > total_pages:
            page = total_pages

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_components = all_components[start_idx:end_idx]

        load_errors = get_load_errors()
        errors_list = []
        for file_path, error_info in load_errors.items():
            errors_list.append({
                "file": error_info.get("file", "unknown"),
                "error": error_info.get("error", "Unknown error"),
                "error_type": error_info.get("error_type", "Error"),
            })

        response = {
            "components": paginated_components,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "tools_registered": len(registered_tools),
            "components_dir": str(get_components_dir()),
            "load_errors": errors_list,
            "error_count": len(errors_list),
        }

        if components_with_issues:
            response["components_with_issues"] = components_with_issues
            response["fix_hint"] = (
                f"检测到 {len(components_with_issues)} 个组件存在问题。"
                "使用 component.info(name='<组件名>') 查看详细修复建议，"
                "或使用 file.edit 直接修改组件文件。"
            )

        return response

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
            return {"success": False, "error": f"\u672a\u627e\u5230\u7ec4\u4ef6: {name}", "available": list(get_all_cells().keys())}

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

        result = {
            "name": cell.cell_name,
            "class": type(cell).__name__,
            "source_file": source_file,
            "loaded": True,
            "commands": cmds,
            "command_count": len(cmds),
            "has_help": hasattr(cell, "_cmd_help"),
            "code_preview": code_preview,
        }

        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            reg = get_component_tool_registry()
            adapter = reg.get(name)
            if adapter and hasattr(adapter, '_audit_issues') and adapter._audit_issues:
                result["has_issues"] = True
                result["audit_score"] = getattr(adapter, '_audit_score', 0)
                result["audit_issues"] = adapter._audit_issues
                result["audit_warnings"] = getattr(adapter, '_audit_warnings', [])
                result["full_audit_hint"] = getattr(adapter, '_audit_hint_text', "")
                result["fix_guide"] = (
                    "[系统提示] 此组件存在规范问题，需要修复：\n"
                    "1. 查看 audit_issues 了解具体问题；\n"
                    "2. 读取并修改组件文件；\n"
                    "3. 调用 component.reload(name='组件名') 重新加载。\n"
                )
            else:
                result["has_issues"] = False
                result["audit_status"] = "通过"
        except Exception:
            pass

        return result

    def _cmd_template(self, style: str = "minimal") -> Dict[str, Any]:
        """
        获取组件模板代码

        Args:
            style: minimal / full / example / background / permission
                   permission = 第三方集成权限模板（命令级管控 + 跨组件黑名单），一般组件用不到

        Returns:
            {"template": 代码文本, "style": 风格名}
        """
        templates = {}
        templates["minimal"] = '''# -*- coding: utf-8 -*-
"""\u6211\u7684\u7ec4\u4ef6"""

from typing import Any, Dict
from app.core.interface.base_cell import BaseCell


class MyComponent(BaseCell):
    """\u7ec4\u4ef6\u63cf\u8ff0"""

    @property
    def cell_name(self) -> str:
        return "my_component"

    def _cmd_do_something(self, input_text: str) -> Dict[str, Any]:
        """\u6267\u884c\u64cd\u4f5c"""
        return {"success": True, "result": f"\u5904\u7406\u4e86: {input_text}"}

    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """\u67e5\u8be2\u7528\u6cd5\u5e2e\u52a9"""
        cmds = self.get_commands()
        return {"success": True, "name": self.cell_name, "commands": cmds, "notes": ["\u586b\u5199\u8be6\u7ec6\u7528\u6cd5"]}
'''

        templates["background"] = '''# -*- coding: utf-8 -*-
"""
\u540e\u53f0\u76d1\u63a7\u7ec4\u4ef6 \u2014 \u6301\u7eed\u8fd0\u884c\u7684\u540e\u53f0\u4efb\u52a1

\u529f\u80fd:
  - \u7ec4\u4ef6\u52a0\u8f7d\u65f6\u81ea\u52a8\u542f\u52a8\u540e\u53f0\u7ebf\u7a0b
  - Agent \u53ef\u901a\u8fc7\u547d\u4ee4\u63a7\u5236\uff08start/stop/status\uff09
  - \u53ef\u4e3b\u52a8\u89e6\u53d1 Agent \u6267\u884c\u4efb\u52a1

\u4f7f\u7528\u573a\u666f:
  - \u5b9e\u65f6\u76d1\u63a7\uff08\u4ef7\u683c\u3001\u65e5\u5fd7\u3001\u7cfb\u7edf\u72b6\u6001\u7b49\uff09
  - \u5b9a\u65f6\u68c0\u67e5\uff08\u670d\u52a1\u5065\u5eb7\u68c0\u67e5\u3001\u6570\u636e\u540c\u6b65\u7b49\uff09
  - \u4e8b\u4ef6\u89e6\u53d1\uff08\u68c0\u6d4b\u5230\u53d8\u5316\u65f6\u901a\u77e5 Agent\uff09
"""

import threading
import time
from typing import Any, Dict
from app.core.interface.base_cell import BaseCell


class BackgroundMonitor(BaseCell):
    """\u540e\u53f0\u76d1\u63a7\u7ec4\u4ef6"""

    # 后台组件声明：沙箱模式下启动心跳保活，空闲清理线程不会回收本沙箱
    IS_BACKGROUND = True

    def __init__(self):
        super().__init__()
        self._running = False
        self._thread = None
        self._counter = 0
        self._target_sessions = []  # 目标 session 列表

    @property
    def cell_name(self) -> str:
        return "background_monitor"

    def on_load(self):
        """\u7ec4\u4ef6\u52a0\u8f7d\u65f6\u81ea\u52a8\u542f\u52a8\u540e\u53f0\u4efb\u52a1"""
        super().on_load()
        self._start_background()

    def on_unload(self):
        """\u7ec4\u4ef6\u5378\u8f7d\u65f6\u505c\u6b62\u540e\u53f0\u4efb\u52a1"""
        self._stop_background()

    def _start_background(self):
        """\u542f\u52a8\u540e\u53f0\u7ebf\u7a0b"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()

    def _stop_background(self):
        """\u505c\u6b62\u540e\u53f0\u7ebf\u7a0b"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _background_loop(self):
        """\u540e\u53f0\u5faa\u73af \u2014 \u5728\u8fd9\u91cc\u5b9e\u73b0\u76d1\u63a7\u903b\u8f91"""
        while self._running:
            try:
                self._counter += 1
                # === 在这里实现你的监控逻辑 ===
                # 示例：检测到变化时通知所有目标 session
                # if self._detect_change():
                #     for session_id in self._target_sessions:
                #         self._trigger_agent("检测到变化，请处理", session_id)
            except Exception as e:
                self.logger.error(f"\u540e\u53f0\u4efb\u52a1\u51fa\u9519: {e}")
            time.sleep(60)

    def _trigger_agent(self, message: str, session_id: str = None):
        """\u4e3b\u52a8\u89e6\u53d1 Agent \u6267\u884c\u4efb\u52a1
        
        \u63a8\u9001\u6d88\u606f\u5230\u6307\u5b9a session \u7684 Agent \u5bf9\u8bdd\uff0c\u7c7b\u4f3c\u5b9a\u65f6\u4efb\u52a1\u7684\u673a\u5236\u3002
        - \u5982\u679c\u8be5 session \u6709\u8fd0\u884c\u4e2d\u7684\u4efb\u52a1\uff0c\u6d88\u606f\u4f1a\u8ffd\u52a0\u5230\u5f53\u524d\u4efb\u52a1
        - \u5982\u679c\u6ca1\u6709\u8fd0\u884c\u4e2d\u7684\u4efb\u52a1\uff0c\u4f1a\u542f\u52a8\u65b0\u4efb\u52a1
        """
        import httpx
        if not session_id:
            self.logger.warning("_trigger_agent \u9700\u8981 session_id \u53c2\u6570")
            return {"success": False, "error": "session_id is required"}
        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()
            host = cfg.get("server.host", "127.0.0.1")
            port = cfg.get("server.port", 18000)
            base_url = f"http://{host}:{port}"
        except Exception:
            base_url = "http://127.0.0.1:18000"
        try:
            response = httpx.post(
                f"{base_url}/api/component/event",
                json={"session_id": session_id, "message": message, "source": self.cell_name, "event_type": "background_trigger"},
                timeout=10.0
            )
            result = response.json()
            self.logger.info(f"\u5df2\u89e6\u53d1 Agent | session={session_id} | status={result.get(\'status\')}")
            return {"success": True, "result": result}
        except Exception as e:
            self.logger.error(f"\u89e6\u53d1 Agent \u5931\u8d25: {e}")
            return {"success": False, "error": str(e)}

    def _cmd_start(self) -> Dict[str, Any]:
        """\u542f\u52a8\u540e\u53f0\u76d1\u63a7\u4efb\u52a1"""
        self._start_background()
        return {"success": True, "message": "\u540e\u53f0\u76d1\u63a7\u5df2\u542f\u52a8", "running": self._running}

    def _cmd_stop(self) -> Dict[str, Any]:
        """\u505c\u6b62\u540e\u53f0\u76d1\u63a7\u4efb\u52a1"""
        self._stop_background()
        return {"success": True, "message": "\u540e\u53f0\u76d1\u63a7\u5df2\u505c\u6b62", "running": self._running}

    def _cmd_status(self) -> Dict[str, Any]:
        """\u83b7\u53d6\u540e\u53f0\u76d1\u63a7\u72b6\u6001"""
        return {
            "running": self._running,
            "counter": self._counter,
            "target_sessions": self._target_sessions,
            "thread_alive": self._thread.is_alive() if self._thread else False,
        }

    def _cmd_add_session(self, session_id: str = None) -> Dict[str, Any]:
        """\u6dfb\u52a0\u76ee\u6807 session\uff08\u4e0d\u4f20\u5219\u4f7f\u7528\u5f53\u524d\u5bf9\u8bdd\uff09"""
        if session_id and session_id not in self._target_sessions:
            self._target_sessions.append(session_id)
        return {"success": True, "target_sessions": self._target_sessions}

    def _cmd_remove_session(self, session_id: str) -> Dict[str, Any]:
        """\u79fb\u9664\u76ee\u6807 session"""
        if session_id in self._target_sessions:
            self._target_sessions.remove(session_id)
        return {"success": True, "target_sessions": self._target_sessions}
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

        if style == "permission":
            templates["permission"] = '''# -*- coding: utf-8 -*-
"""
权限模板 — 仅用于组件对接第三方平台的场景，一般组件不需要

什么时候用这个：
  你的组件把外部用户（QQ/微信等）的消息转成 Agent 任务时，需要控制
  "哪些命令谁能调、经这个组件触发的任务能用哪些工具"。

平时自己用的组件（计算器、文件、定时任务等）用 minimal/full 风格模板，
不要用本模板。

声明方式：把对应段复制进你的组件类即可，全部可选、不声明 = 不限制。
"""

from typing import Any, Dict

from app.core.interface.base_cell import BaseCell


class MyIntegration(BaseCell):
    """
    第三方集成组件 — 权限声明示例

    权限分三层，按需取用：
      1. TOOL_POLICY          本组件内，哪条命令谁能调（命令级）
      2. TRIGGER_TOOL_POLICY  经本组件触发的 Agent 任务，禁用哪些工具（跨组件）
      3. is_policy_admin      "admin" 策略的判定依据（用了 admin 策略就必须实现）
    """

    @property
    def cell_name(self) -> str:
        return "my_integration"

    # ================================================================
    # 第 1 层：本组件命令级权限（谁能调哪条命令）
    # 键 = 命令名（不带 _cmd_ 前缀）
    # ================================================================

    TOOL_POLICY = {
        "reset": "deny",            # 所有人禁用（包括管理员）
        "add_user": "admin",        # 仅管理员（需实现 is_policy_admin）
        # 用户黑名单：列表内用户被禁，其他人可用
        "shutdown": {"mode": "blacklist", "users": ["12345"]},
        # 用户白名单：仅列表内用户可用
        "send_private": {"mode": "whitelist", "users": ["10001"]},
    }
    TOOL_POLICY_DEFAULT = "allow"   # 未列出的命令默认放行（可改 "admin" 全默认仅管理员）

    def is_policy_admin(self, platform_context=None) -> bool:
        """
        "admin" 策略的判定依据 — 用了 "admin" 策略的组件必须实现

        注意：未实现时所有 admin 命令一律拒绝（按非管理员处理）；
        方法内部报错也会被当作非管理员，不要在这里抛异常。
        """
        user_id = str((platform_context or {}).get("user_id") or "")
        return user_id in {"10001"}  # 换成你的管理员 ID 来源

    # ================================================================
    # 第 2 层：跨组件触发黑名单（经本组件触发的 Agent 任务禁用哪些工具）
    # 键支持 "工具名" 或 "工具名.命令名"（精确到命令）
    # 黑名单模式自动叠加系统默认兜底（shell/component 等本来就禁）
    # ================================================================

    TRIGGER_TOOL_POLICY = {
        "mode": "blacklist",
        "tools": [
            "sub_agent",               # 禁子 Agent
            "qq_files.download",       # 禁下载命令（qq_files 其他命令不受影响）
        ],
    }

    # ================================================================
    # 命令示例 — 被上面 TOOL_POLICY 管控的命令
    # ================================================================

    def _cmd_reset(self, **kwargs) -> Dict[str, Any]:
        """重置配置（示例：被 deny，所有人都调不到）"""
        return {"success": True, "message": "reset"}

    def _cmd_add_user(self, user_id: str = "") -> Dict[str, Any]:
        """添加用户（示例：仅管理员）"""
        return {"success": True, "added": user_id}

    def _cmd_send_private(self, to: str = "", text: str = "") -> Dict[str, Any]:
        """发私信（示例：仅白名单用户）"""
        return {"success": True, "to": to, "sent": text}

    def _cmd_query(self, keyword: str = "") -> Dict[str, Any]:
        """普通查询（未列入 TOOL_POLICY，走 DEFAULT=allow 所有人可用）"""
        return {"success": True, "keyword": keyword}
'''

        template = templates.get(style, templates["minimal"])
        return {"success": True, "template": template, "style": style, "available_styles": ["minimal", "full", "example", "background", "permission"]}

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
            summary.append("\u5df2\u91cd\u8f7d")

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

    def _cmd_cleanup(self, dry_run: bool = True, force: bool = False) -> Dict[str, Any]:
        """
        清理组件系统缓存

        用于解决以下问题：
          - 组件文件已删除但系统仍提示错误
          - 沙箱进程残留导致内存占用过高
          - 信任白名单中有大量已删除的组件
          - 加载错误记录堆积

        Args:
            dry_run: 预览模式（默认 True）- 只查看有哪些问题，不实际清理
            force: 强制模式 - 包括正在运行的沙箱（可能导致执行中的任务失败）
        """
        from app.core.util.components_loader import clear_all_caches

        report = clear_all_caches(dry_run=dry_run, force=force)

        messages = []
        if report.get("load_errors_cleared", 0) > 0:
            messages.append(f"\u6e05\u7406\u4e86 {report['load_errors_cleared']} \u6761\u52a0\u8f7d\u9519\u8bef\u8bb0\u5f55")
        if report.get("component_classes_cleared", 0) > 0:
            messages.append(f"\u6e05\u7406\u4e86 {report['component_classes_cleared']} \u4e2a\u5b64\u513f\u7c7b\u5f15\u7528")
        if report.get("sandboxes_cleared", 0) > 0:
            messages.append(f"\u6e05\u7406\u4e86 {report['sandboxes_cleared']} \u4e2a\u6b8b\u7559\u6c99\u7bb1")
        if report.get("sandboxes_skipped"):
            messages.append(f"\u8df3\u8fc7 {len(report['sandboxes_skipped'])} \u4e2a\u6b63\u5728\u8fd0\u884c\u7684\u6c99\u7bb1")
        if report.get("trust_list_cleaned"):
            messages.append(f"\u6e05\u7406\u4e86\u4fe1\u4efb\u767d\u540d\u5355: {report['trust_list_cleaned']}")

        if not messages:
            messages.append("\u6ca1\u6709\u53d1\u73b0\u9700\u8981\u6e05\u7406\u7684\u7f13\u5b58")

        result = {
            "success": True,
            "dry_run": dry_run,
            "force": force,
            "report": report,
            "summary": "; ".join(messages),
        }

        if report.get("warnings"):
            result["warnings"] = report["warnings"]

        if dry_run:
            result["hint"] = "\u8fd9\u662f\u9884\u89c8\u6a21\u5f0f\uff0c\u6ca1\u6709\u5b9e\u9645\u6267\u884c\u6e05\u7406\u3002\u5982\u679c\u786e\u8ba4\u65e0\u8bef\uff0c\u8bf7\u8c03\u7528 component.cleanup(dry_run=false)"
        elif report.get("sandboxes_skipped") and not force:
            result["hint"] = f"\u6709 {len(report['sandboxes_skipped'])} \u4e2a\u6c99\u7bb1\u6b63\u5728\u8fd0\u884c\u88ab\u8df3\u8fc7\u3002\u5982\u9700\u5f3a\u5236\u6e05\u7406\uff0c\u8bf7\u4f7f\u7528 force=true"

        return result

    def _cmd_dir(self) -> Dict[str, Any]:
        """获取组件文件夹的真实路径（pip 安装模式返回用户数据目录，绿色版返回项目目录）"""
        from app.core.util.runtime_paths import _detect_install_mode
        from app.core.util.components_loader import get_builtin_components_dir

        components_dir = get_components_dir()
        builtin_dir = get_builtin_components_dir()
        mode, source_root = _detect_install_mode()
        exists = components_dir.exists()

        count = 0
        if exists:
            try:
                count = sum(
                    1 for item in components_dir.iterdir()
                    if not item.name.startswith("_")
                    and (item.is_file() and item.suffix == ".py"
                         or item.is_dir() and (item / "__init__.py").exists())
                )
            except Exception:
                pass

        return {
            "success": True,
            "components_dir": str(components_dir),
            "builtin_dir": str(builtin_dir),
            "mode": mode,
            "mode_desc": "pip 安装" if mode == "installed" else "绿色版/源码",
            "exists": exists,
            "component_count": count,
        }

    def _cmd_sandbox_list(self) -> Dict[str, Any]:
        """查看免沙箱白名单（内置硬编码名单 + 自定义名单）

        Returns:
            builtin=内置免沙箱组件（代码硬编码，不写入白名单文件）
            custom=自定义白名单（config/sandbox.yaml）
            in_sandbox=仍在沙箱子进程运行的组件（即可以加入白名单的候选）
        """
        from app.core.util.cell_tool_adapter import (
            EXEMPTED_NAMES,
            get_exempted_names,
            get_sandbox_whitelist_path,
            load_sandbox_whitelist,
        )
        from app.core.util.components_loader import get_scan_dirs

        custom = load_sandbox_whitelist()
        effective = get_exempted_names()
        loaded = sorted(n for n in (getattr(c, "cell_name", "") for c in get_all_cells().values()) if n)

        return {
            "success": True,
            "whitelist_file": get_sandbox_whitelist_path(),
            "builtin": sorted(EXEMPTED_NAMES),
            "custom": sorted(custom),
            "effective": sorted(effective),
            "in_sandbox": [n for n in loaded if n not in effective],
            "stale": sorted(custom - set(loaded)),
            "components_dirs": [str(d) for d in get_scan_dirs()],
            "note": (
                "只有组件（components 目录下的 BaseCell 子类）参与沙箱；"
                "内置工具 shell/file/edit 等不经过适配器，无需白名单。"
                "builtin 组件已免沙箱，无需也不必写入白名单文件。"
                "stale 是白名单中当前未加载的残留项，可用 sandbox_remove 清理"
            ),
        }

    def _cmd_sandbox_add(self, name: str = "", auto_reload: bool = True) -> Dict[str, Any]:
        """将组件加入免沙箱白名单（该组件将在主进程直接执行）

        Args:
            name: 组件名（cell_name）
            auto_reload: 是否立即热重载使配置生效，默认 True

        Returns:
            白名单文件路径与最新自定义清单
        """
        from app.core.util.cell_tool_adapter import (
            EXEMPTED_NAMES,
            get_sandbox_whitelist_path,
            load_sandbox_whitelist,
            save_sandbox_whitelist,
        )

        target = _resolve_cell_name(name)
        if not target:
            return {"success": False, "error": "缺少参数 name（组件名）"}

        loaded = {getattr(c, "cell_name", "") for c in get_all_cells().values()}
        if target not in loaded:
            return {
                "success": False,
                "error": f"组件 {target} 未加载，无法加入白名单",
                "loaded_components": sorted(n for n in loaded if n),
            }

        if target in EXEMPTED_NAMES:
            return {
                "success": True,
                "already_exempt": True,
                "message": f"{target} 是内置免沙箱组件，无需添加",
            }

        custom = load_sandbox_whitelist()
        if target in custom:
            return {"success": True, "already_exempt": True, "message": f"{target} 已在自定义白名单中"}

        custom.add(target)
        path = save_sandbox_whitelist(custom)

        result: Dict[str, Any] = {
            "success": True,
            "message": f"{target} 已加入免沙箱白名单",
            "resolved_name": target,
            "whitelist_file": path,
            "custom": sorted(custom),
            "reloaded": False,
        }
        if auto_reload:
            try:
                reload_result = self._cmd_reload()
                result["reloaded"] = True
                result["reload_summary"] = reload_result.get("summary")
            except Exception as e:
                result["reload_error"] = str(e)
                result["hint"] = "请手动调用 component.reload 使配置生效"
        else:
            result["hint"] = "调用 component.reload 后生效"
        return result

    def _cmd_sandbox_remove(self, name: str = "", auto_reload: bool = True) -> Dict[str, Any]:
        """将组件移出自定义免沙箱白名单（恢复沙箱隔离）

        Args:
            name: 组件名（cell_name）
            auto_reload: 是否立即热重载使配置生效，默认 True

        Returns:
            白名单文件路径与最新自定义清单
        """
        from app.core.util.cell_tool_adapter import (
            EXEMPTED_NAMES,
            get_sandbox_whitelist_path,
            load_sandbox_whitelist,
            save_sandbox_whitelist,
        )

        target = _resolve_cell_name(name)
        if not target:
            return {"success": False, "error": "缺少参数 name（组件名）"}

        if target in EXEMPTED_NAMES:
            return {
                "success": False,
                "error": f"{target} 是内置免沙箱组件，不能通过此命令移除",
            }

        custom = load_sandbox_whitelist()
        if target not in custom:
            hit = next((n for n in custom if n.lower() == target.lower()), "")
            if not hit:
                return {"success": True, "message": f"{target} 不在自定义白名单中", "custom": sorted(custom)}
            target = hit

        custom.discard(target)
        path = save_sandbox_whitelist(custom)

        result: Dict[str, Any] = {
            "success": True,
            "message": f"{target} 已移出白名单，将恢复沙箱隔离",
            "whitelist_file": path,
            "custom": sorted(custom),
            "reloaded": False,
        }
        if auto_reload:
            try:
                reload_result = self._cmd_reload()
                result["reloaded"] = True
                result["reload_summary"] = reload_result.get("summary")
            except Exception as e:
                result["reload_error"] = str(e)
                result["hint"] = "请手动调用 component.reload 使配置生效"
        else:
            result["hint"] = "调用 component.reload 后生效"
        return result

    # ================================================================
    # 本地组件安装（文件夹 / 压缩包）
    # ================================================================

    _ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
    _MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
    _MAX_ARCHIVE_FILES = 500
    _RISKY_PREFIXES = {
        "os.system": "直接调用系统 shell",
        "os.popen": "直接调用系统 shell",
        "subprocess": "启动外部进程",
        "eval": "动态执行字符串",
        "exec": "动态执行代码",
    }
    _SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}

    def _cmd_install_local(self, path: str = "", force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """从本地文件夹或压缩包安装组件（先审查再装入）

        支持的来源格式:
          - 单文件组件: xxx.py
          - 包组件目录: xxx/ (含 __init__.py 与 .py 模块)
          - 压缩包: .zip / .tar / .tar.gz / .tgz / .tar.bz2 / .tar.xz（内含上述结构）

        Args:
            path: 本地路径（文件夹、.py 文件或压缩包）
            force: 目标已存在时是否覆盖（默认 False）
            dry_run: 只审查不安装（默认 False）

        Returns:
            {"success": True, "name": ..., "audit": {...}, "installed": [...]}
        """
        import shutil
        import tempfile
        from pathlib import Path

        raw = (path or "").strip().strip('"').strip("'")
        if not raw:
            return {
                "success": False,
                "error": "缺少参数 path（文件夹或压缩包路径）",
                "hint": '示例: {"command":"install_local","path":"D:/downloads/my_comp.zip"}',
            }

        src = Path(raw).expanduser()
        if not src.exists():
            return {"success": False, "error": f"路径不存在: {src}"}

        tmpdir = None
        try:
            if src.is_file() and src.name.lower().endswith(self._ARCHIVE_SUFFIXES):
                tmpdir = tempfile.mkdtemp(prefix="cellium_comp_")
                err = self._safe_extract(src, Path(tmpdir))
                if err:
                    return err
                work_root = Path(tmpdir)
            else:
                work_root = src

            located = self._locate_component(work_root)
            if isinstance(located, dict):
                return located
            kind, comp_path = located
            name = comp_path.name if kind == "package" else comp_path.stem
            if not self._valid_component_name(name):
                return {
                    "success": False,
                    "error": f"无效的组件名 '{name}'：必须是 ASCII 合法 Python 标识符且不以 _ 开头",
                    "hint": "请重命名文件或目录，如 my_comp.py；下划线开头的文件会被加载器忽略，非 ASCII 名无法作为模块导入",
                }

            audit = self._audit_component(kind, comp_path)
            if not audit.get("ok"):
                return {
                    "success": False,
                    "error": "审查未通过：未找到 BaseCell 组件类",
                    "audit": audit,
                    "hint": "组件类需继承 BaseCell（from app.core.interface.base_cell import BaseCell）",
                }

            name = comp_path.name if kind == "package" else comp_path.stem
            components_dir = get_components_dir()
            target = components_dir / (name if kind == "package" else f"{name}.py")
            conflict = components_dir / (f"{name}.py" if kind == "package" else name)

            if conflict.exists():
                return {
                    "success": False,
                    "error": f"存在同名但不同格式的组件: {conflict}",
                    "hint": "包组件与单文件组件不能同名，请改名后再装",
                }
            if target.exists() and not force:
                return {
                    "success": False,
                    "error": f"组件已存在: {target}",
                    "hint": "带 force=true 覆盖，或先删除已有组件",
                    "audit": audit,
                }

            result: Dict[str, Any] = {
                "success": True,
                "name": name,
                "kind": kind,
                "source": str(comp_path),
                "target": str(target),
                "dry_run": bool(dry_run),
                "audit": audit,
                "overwritten": target.exists(),
            }

            if dry_run:
                result["hint"] = "预览模式，未写入。确认无误后去掉 dry_run 再调用"
                return result

            try:
                components_dir.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.rmtree(target) if kind == "package" else target.unlink()
                if kind == "package":
                    shutil.copytree(
                        comp_path, target,
                        ignore=shutil.ignore_patterns(*self._SKIP_DIRS, "*.pyc"),
                    )
                else:
                    shutil.copy2(comp_path, target)
            except Exception as e:
                return {"success": False, "error": f"写入组件目录失败: {e}", "target": str(target)}

            try:
                reload_result = self._cmd_reload()
                result["reload"] = reload_result.get("summary")
                result["tools_registered"] = reload_result.get("tools_registered")
                result["message"] = f"已安装 {name} 并热重载"
            except Exception as e:
                result["reload_error"] = str(e)
                result["hint"] = "已复制到组件目录，但热重载失败，请手动调用 component.reload"

            try:
                from app.core.util.components_loader import get_load_errors
                hit = {k: v for k, v in get_load_errors().items() if name in k}
                if hit:
                    result["load_errors"] = hit
                    result["message"] = f"已安装 {name}，但加载报错，请查看 load_errors"
            except Exception:
                pass

            return result
        finally:
            if tmpdir:
                try:
                    shutil.rmtree(tmpdir)
                except Exception:
                    pass

    def _safe_extract(self, archive, dest) -> Optional[Dict[str, Any]]:
        """安全解压：拦截路径穿越、限制体积与文件数"""
        import tarfile
        import zipfile
        from pathlib import Path

        total = 0
        count = 0
        too_big = {
            "success": False,
            "error": f"压缩包超限（上限 {self._MAX_ARCHIVE_BYTES // 1024 // 1024}MB / {self._MAX_ARCHIVE_FILES} 文件），拒绝解压",
        }
        try:
            if archive.name.lower().endswith(".zip"):
                with zipfile.ZipFile(archive) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        parts = Path(info.filename.replace("\\", "/")).parts
                        if ".." in parts or info.filename.startswith(("/", "\\")):
                            return {"success": False, "error": f"压缩包含非法路径: {info.filename}"}
                        total += info.file_size
                        count += 1
                        if total > self._MAX_ARCHIVE_BYTES or count > self._MAX_ARCHIVE_FILES:
                            return too_big
                    zf.extractall(dest)
            else:
                with tarfile.open(archive) as tf:
                    for m in tf.getmembers():
                        if not m.isfile():
                            continue
                        parts = Path(m.name).parts
                        if ".." in parts or m.name.startswith("/"):
                            return {"success": False, "error": f"压缩包含非法路径: {m.name}"}
                        total += m.size
                        count += 1
                        if total > self._MAX_ARCHIVE_BYTES or count > self._MAX_ARCHIVE_FILES:
                            return too_big
                    kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                    tf.extractall(dest, **kwargs)
        except Exception as e:
            return {"success": False, "error": f"解压失败: {e}"}
        return None

    @staticmethod
    def _has_cell_class(py_path) -> bool:
        """轻量 AST 检测：文件里是否定义了 BaseCell 子类"""
        import ast
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return False
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for b in node.bases:
                try:
                    name = getattr(b, "id", "") or getattr(b, "attr", "") or ast.unparse(b)
                except Exception:
                    name = ""
                if "BaseCell" in name:
                    return True
        return False

    def _locate_component(self, root):
        """定位组件根：根层优先，找不到再递归（兼容 src/ 等嵌套结构），只认含 BaseCell 的候选"""
        from pathlib import Path

        def _is_pkg(p) -> bool:
            return p.is_dir() and (p / "__init__.py").exists()

        def _pkg_has_cell(p) -> bool:
            return any(self._has_cell_class(f) for f in p.rglob("*.py"))

        def _skip(p) -> bool:
            if p.name.startswith("."):
                return True
            return any(part in self._SKIP_DIRS for part in p.relative_to(root).parts)

        if root.is_file():
            return ("file", root) if root.suffix == ".py" else {
                "success": False, "error": f"不支持的文件类型: {root.name}",
            }
        if _is_pkg(root):
            return ("package", root)

        entries = [e for e in root.iterdir() if not _skip(e)]
        pkgs = [e for e in entries if _is_pkg(e) and _pkg_has_cell(e)]
        pys = [
            e for e in entries
            if e.is_file() and e.suffix == ".py" and not e.name.startswith("_")
            and self._has_cell_class(e)
        ]

        candidates = pkgs + pys
        if not candidates:
            found_pkgs = sorted(
                (d for d in root.rglob("*")
                 if d.is_dir() and _is_pkg(d) and not _skip(d) and _pkg_has_cell(d)),
                key=lambda d: len(d.parts),
            )
            outer = []
            for d in found_pkgs:
                if not any(o in d.parents for o in outer):
                    outer.append(d)
            candidates = outer + [
                p for p in root.rglob("*.py")
                if not p.name.startswith("_") and not _skip(p)
                and self._has_cell_class(p)
                and not any(o in p.parents for o in outer)
            ]

        if len(candidates) == 1:
            c = candidates[0]
            return ("package", c) if c.is_dir() else ("file", c)
        if not candidates:
            return {
                "success": False,
                "error": "未找到组件：没有含 BaseCell 类的 .py 文件或包",
                "dir": str(root),
            }
        return {
            "success": False,
            "error": "包含多个组件，无法确定安装目标",
            "candidates": [p.relative_to(root).as_posix() for p in candidates],
            "hint": "请分别打包，或传入具体组件的文件/目录路径",
        }

    @staticmethod
    def _valid_component_name(name: str) -> bool:
        """组件名必须是 ASCII 合法标识符且非下划线开头，否则加载器无法导入"""
        return bool(name) and name.isascii() and name.isidentifier() and not name.startswith("_")

    def _audit_component(self, kind: str, comp_path) -> Dict[str, Any]:
        """静态审查：组件类、命令、依赖、危险调用"""
        import ast
        import importlib.util
        import io
        import sys
        import tokenize
        from pathlib import Path

        if kind == "file":
            py_files = [comp_path]
            root = comp_path.parent
        else:
            py_files = sorted(p for p in comp_path.rglob("*.py") if p.is_file())
            root = comp_path

        total_bytes = sum(p.stat().st_size for p in py_files)
        cells: list = []
        risky: list = []
        missing_deps: list = []
        syntax_errors: list = []
        skipped_modules: list = []
        stdlib = getattr(sys, "stdlib_module_names", frozenset())

        for py in py_files:
            rel = py.relative_to(root).as_posix() if kind == "package" else py.name
            # 与加载器保持一致：非法模块名无法导入，_ 开头的模块会被跳过
            if kind == "package" and py.stem != "__init__":
                if not (py.stem.isascii() and py.stem.isidentifier()):
                    syntax_errors.append({
                        "file": rel,
                        "error": "模块文件名不是合法 Python 标识符，加载器无法导入",
                    })
                    continue
                if py.stem.startswith("_"):
                    skipped_modules.append(rel)
                    continue
            try:
                raw = py.read_bytes()
                enc, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                text = raw.decode(enc)
            except SyntaxError as e:
                syntax_errors.append({"file": rel, "error": f"编码声明无效: {e}"})
                continue
            except UnicodeDecodeError as e:
                syntax_errors.append({"file": rel, "error": f"文件编码无法解码: {e}"})
                continue

            try:
                tree = ast.parse(text)
            except SyntaxError as e:
                syntax_errors.append({"file": rel, "error": f"{e.msg} (line {e.lineno})"})
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    "BaseCell" in (getattr(b, "id", "") or getattr(b, "attr", "") or ast.unparse(b))
                    for b in node.bases
                ):
                    cmds = [
                        n.name[5:] for n in node.body
                        if isinstance(n, ast.FunctionDef) and n.name.startswith("_cmd_")
                    ]
                    cells.append({
                        "class": node.name,
                        "file": rel,
                        "cell_name": self._extract_cell_name(node, ast),
                        "commands": cmds,
                    })

                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else ast.unparse(fn)
                    for prefix, why in self._RISKY_PREFIXES.items():
                        if name == prefix or name.startswith(prefix + "."):
                            risky.append({"call": name, "file": rel, "why": why})
                            break

            for node in tree.body:
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module.split(".")[0]]
                for mod in mods:
                    if mod in stdlib or mod in {"app", "components", "config", "libs"}:
                        continue
                    try:
                        if importlib.util.find_spec(mod) is None:
                            missing_deps.append({"module": mod, "file": rel})
                    except (ImportError, ValueError):
                        missing_deps.append({"module": mod, "file": rel})

        warnings = []
        if kind == "package" and not (comp_path / "__init__.py").exists():
            warnings.append("包目录缺少 __init__.py，可能无法被加载")
        if len(cells) > 1:
            warnings.append(f"发现 {len(cells)} 个 BaseCell 类，确认是否都需要")
        if risky:
            warnings.append(f"检测到 {len(risky)} 处对外部系统/动态执行的调用，请确认来源可信")
        if missing_deps:
            warnings.append(f"缺少 {len(set(d['module'] for d in missing_deps))} 个依赖模块，需先安装后再用该组件")
        if skipped_modules:
            warnings.append(f"{len(skipped_modules)} 个以 _ 开头的模块被加载器跳过: {skipped_modules}")

        return {
            "ok": bool(cells) and not syntax_errors,
            "kind": kind,
            "root": str(comp_path),
            "py_files": len(py_files),
            "total_bytes": total_bytes,
            "cells": cells,
            "commands": [c for cell in cells for c in cell["commands"]],
            "risky_calls": risky,
            "missing_deps": missing_deps,
            "syntax_errors": syntax_errors,
            "warnings": warnings,
        }

    @staticmethod
    def _extract_cell_name(node, ast_mod) -> str:
        for item in node.body:
            if isinstance(item, ast_mod.Assign):
                for t in item.targets:
                    if getattr(t, "id", "") == "cell_name" and isinstance(item.value, ast_mod.Constant):
                        return str(item.value.value)
            if isinstance(item, ast_mod.FunctionDef) and item.name == "cell_name":
                for sub in ast_mod.walk(item):
                    if isinstance(sub, ast_mod.Return) and isinstance(sub.value, ast_mod.Constant):
                        return str(sub.value.value)
        return ""

    # ================================================================
    # 组件市场（C 端：搜索 → 元信息 → 下载安装）
    # ================================================================

    _MARKET_BASE_DEFAULT = "https://components.cellium.top"
    _MARKET_CACHE_TTL = 300          # 索引缓存秒数，与线上 5 分钟发布节奏一致
    _MARKET_TIMEOUT = 20             # 单次网络请求超时
    _MARKET_MAX_BYTES = 50 * 1024 * 1024
    _market_cache: Dict[str, Any] = {"base": "", "at": 0.0, "data": None}

    def _market_base(self) -> str:
        """市场站点地址，可用环境变量 CELLIUM_COMPONENTS_BASE 覆盖"""
        base = (os.environ.get("CELLIUM_COMPONENTS_BASE") or self._MARKET_BASE_DEFAULT).strip()
        return base.rstrip("/")

    @staticmethod
    def _market_slug(raw: str) -> str:
        """兼容 slug / URL / 站点复制的 `market install <slug>` 三种写法"""
        s = (raw or "").strip().strip('"').strip("'")
        if "market install " in s:
            s = s.split("market install ", 1)[1].strip()
        if "/c/" in s:
            s = s.split("/c/", 1)[1]
        s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if s.endswith(".json"):
            s = s[:-5]
        return s.strip()

    @staticmethod
    def _market_valid_slug(slug: str) -> bool:
        import re
        return bool(re.match(r"^[a-z0-9][a-z0-9_-]{1,39}$", slug))

    @staticmethod
    def _market_http_json(url: str, timeout: float) -> Any:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Cellium-Agent/component-builder",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    raise RuntimeError(f"HTTP {status}")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError("组件不存在（404）") from e
            raise RuntimeError(f"HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"网络不可达: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"返回内容不是合法 JSON: {e}") from e

    def _market_index(self, refresh: bool = False) -> Dict[str, Any]:
        """拉取市场索引 /data/index.json（带 5 分钟内存缓存）"""
        import time

        base = self._market_base()
        cache = self._market_cache
        now = time.time()
        if (
            not refresh
            and cache.get("data")
            and cache.get("base") == base
            and now - float(cache.get("at") or 0) < self._MARKET_CACHE_TTL
        ):
            return cache["data"]

        data = self._market_http_json(f"{base}/data/index.json", self._MARKET_TIMEOUT)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise RuntimeError("市场索引格式不正确（缺少 items 列表）")
        self._market_cache.update({"base": base, "at": now, "data": data})
        return data

    def _market_detail(self, slug: str) -> Dict[str, Any]:
        """拉取组件详情 /data/c/<slug>.json"""
        from urllib.parse import quote

        base = self._market_base()
        data = self._market_http_json(
            f"{base}/data/c/{quote(slug, safe='')}.json", self._MARKET_TIMEOUT
        )
        if not isinstance(data, dict) or not data.get("slug"):
            raise RuntimeError("组件详情格式不正确")
        return data

    @staticmethod
    def _market_install_url(base: str, meta: Dict[str, Any], version: Dict[str, Any]) -> str:
        """版本自带直链优先；否则回落到站点自建的 pkg/<file>"""
        download = str(version.get("download") or "").strip()
        if download:
            return download
        slug = str(meta.get("slug") or "")
        ver = str(version.get("version") or "")
        file_name = str(version.get("file") or f"{slug}-{ver}.zip")
        return f"{base}/pkg/{file_name}"

    @staticmethod
    def _market_archive_suffix(url: str) -> str:
        path = url.split("?", 1)[0].split("#", 1)[0].lower()
        for suffix in (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip", ".tar"):
            if path.endswith(suffix):
                return suffix
        return ".zip"

    def _market_download(self, url: str, dest) -> Dict[str, Any]:
        """下载安装包到本地，边下边算 sha256，超限即停"""
        import hashlib
        import urllib.error
        import urllib.request

        sha = hashlib.sha256()
        total = 0
        req = urllib.request.Request(
            url, headers={"User-Agent": "Cellium-Agent/component-builder"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self._MARKET_TIMEOUT) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    raise RuntimeError(f"HTTP {status}")
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self._MARKET_MAX_BYTES:
                            raise RuntimeError(
                                f"安装包超过 {self._MARKET_MAX_BYTES // 1024 // 1024}MB 上限，已中止"
                            )
                        sha.update(chunk)
                        f.write(chunk)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"下载失败 HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"下载失败，网络不可达: {e.reason}") from e
        return {"size": total, "sha256": sha.hexdigest()}

    def _market_is_installed(self, slug: str) -> bool:
        """slug（可能带 -）与组件文件名（Python 标识符，可能用 _）都算已安装"""
        components_dir = get_components_dir()
        for name in {slug, slug.replace("-", "_")}:
            if (components_dir / f"{name}.py").exists() or (components_dir / name).exists():
                return True
        return False

    def _cmd_market_search(
        self,
        keyword: str = "",
        category: str = "",
        tags: str = "",
        page: int = 1,
        page_size: int = 10,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        在 Cellium 组件市场搜索组件（C 端站点 https://components.cellium.top 同源数据）

        只读索引 /data/index.json，不下载安装包。用于先了解市场有什么，再决定装哪个。

        Args:
            keyword: 关键词，匹配组件名/slug/简介/tags（不区分大小写）
            category: 分类精确过滤，可选 web/ai/security/interaction/automation/data/media/other
            tags: 标签过滤，逗号分隔，命中任意一个即可
            page: 页码（从 1 开始）
            page_size: 每页数量（默认 10，最大 50）
            refresh: 是否强制刷新索引缓存（默认 5 分钟缓存）

        Returns:
            {"items":[{slug,name,summary,category,tags,author,latest_version,updated_at,installed}],
             "total": N, "page": ..., "total_pages": ..., "market_base": ...}

        使用:
            component.market_search(keyword="pdf")
            component.market_search(category="automation", page=2)
        """
        base = self._market_base()
        try:
            index = self._market_index(refresh=refresh)
        except Exception as e:
            return {
                "success": False,
                "error": f"读取市场索引失败: {e}",
                "market_base": base,
                "hint": "检查网络或设置环境变量 CELLIUM_COMPONENTS_BASE 指向可用镜像",
            }

        items = index.get("items") or []
        kw = (keyword or "").strip().lower()
        cat = (category or "").strip().lower()
        tag_list = [t.strip().lower() for t in (tags or "").split(",") if t.strip()]

        filtered = []
        for it in items:
            if cat and str(it.get("category", "")).lower() != cat:
                continue
            if kw:
                haystack = " ".join(
                    [
                        str(it.get("slug", "")),
                        str(it.get("name", "")),
                        str(it.get("summary", "")),
                        " ".join(str(t) for t in (it.get("tags") or [])),
                    ]
                ).lower()
                if kw not in haystack:
                    continue
            if tag_list:
                item_tags = {str(t).lower() for t in (it.get("tags") or [])}
                if not item_tags.intersection(tag_list):
                    continue
            filtered.append(it)

        filtered.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)

        total = len(filtered)
        page_size = max(1, min(int(page_size or 10), 50))
        page = max(1, int(page or 1))
        total_pages = (total + page_size - 1) // page_size if total else 0
        if total_pages and page > total_pages:
            page = total_pages
        start = (page - 1) * page_size

        results = []
        for it in filtered[start:start + page_size]:
            slug = str(it.get("slug") or "")
            results.append({
                "slug": slug,
                "name": it.get("name", ""),
                "summary": it.get("summary", ""),
                "category": it.get("category", ""),
                "tags": it.get("tags") or [],
                "author": it.get("author", ""),
                "latest_version": it.get("latest_version", ""),
                "updated_at": it.get("updated_at", ""),
                "deprecated": bool(it.get("deprecated")),
                "installed": self._market_is_installed(slug),
                "next": f"component.market_info(slug='{slug}') 查看详情，component.market_install(slug='{slug}') 安装",
            })

        response: Dict[str, Any] = {
            "success": True,
            "items": results,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "market_base": base,
            "generated_at": index.get("generated_at", ""),
        }
        if total and not results:
            response["hint"] = f"页码超出范围，可用的总页数是 {total_pages}"
        elif not total:
            response["hint"] = "没有匹配的组件，可换个关键词，或 refresh=true 强制刷新索引"
        return response

    def _cmd_market_info(self, slug: str, version: str = "") -> Dict[str, Any]:
        """
        查看市场组件的完整元信息：版本列表、安装包下载地址、校验值、README

        先读详情 /data/c/<slug>.json，再解析出「该怎么装」——install_url、
        sha256/size 校验值，以及可直接调用的 market_install 参数。

        Args:
            slug: 组件标识，也可直接贴站点复制的 `market install <slug>` 或详情页 URL
            version: 指定版本号（默认最新版）

        Returns:
            {"success": True, "slug":..., "resolved_version":..., "install_url":...,
             "checks": {"sha256":..., "size":...}, "install": {"command":"market_install", ...},
             "versions":[...], "readme": ...}
        """
        base = self._market_base()
        target = self._market_slug(slug)
        if not target:
            return {"success": False, "error": "缺少参数 slug", "hint": '示例: {"command":"market_info","slug":"my-cell"}'}
        if not self._market_valid_slug(target):
            return {"success": False, "error": f"非法 slug: {target}", "hint": "slug 只能用小写字母、数字、-、_，2–40 字符"}

        try:
            meta = self._market_detail(target)
        except Exception as e:
            return {
                "success": False,
                "error": f"读取组件元信息失败: {e}",
                "slug": target,
                "market_base": base,
                "hint": "可在 market_search 里确认 slug 是否正确",
            }

        versions = meta.get("versions") or []
        if not versions:
            return {"success": False, "error": "该组件没有可用版本", "slug": target}

        want = (version or "").strip().lstrip("vV")
        chosen = versions[0]
        if want:
            chosen = next(
                (v for v in versions if str(v.get("version", "")).lstrip("vV") == want), None
            )
            if not chosen:
                return {
                    "success": False,
                    "error": f"版本不存在: {version}",
                    "slug": target,
                    "available_versions": [str(v.get("version", "")) for v in versions],
                }

        ver = str(chosen.get("version") or "")
        install_url = self._market_install_url(base, meta, chosen)
        readme = str(meta.get("readme") or "")
        truncated = len(readme) > 3000

        return {
            "success": True,
            "slug": meta.get("slug", target),
            "name": meta.get("name", ""),
            "summary": meta.get("summary", ""),
            "category": meta.get("category", ""),
            "tags": meta.get("tags") or [],
            "author": meta.get("author", ""),
            "repo": meta.get("repo", ""),
            "license": meta.get("license", ""),
            "deprecated": bool(meta.get("deprecated")),
            "latest_version": meta.get("latest_version", ""),
            "resolved_version": ver,
            "updated_at": meta.get("updated_at", ""),
            "install_url": install_url,
            "checks": {
                "sha256": str(chosen.get("sha256") or ""),
                "size": chosen.get("size"),
                "changelog": str(chosen.get("changelog") or ""),
                "date": str(chosen.get("date") or ""),
            },
            "versions": [
                {
                    "version": v.get("version", ""),
                    "date": v.get("date", ""),
                    "changelog": v.get("changelog", ""),
                    "size": v.get("size"),
                    "sha256": v.get("sha256", ""),
                    "download": v.get("download", ""),
                }
                for v in versions
            ],
            "readme": readme[:3000],
            "readme_truncated": truncated,
            "installed": self._market_is_installed(str(meta.get("slug", target))),
            "install": {
                "command": "market_install",
                "slug": meta.get("slug", target),
                "version": ver,
            },
            "market_base": base,
        }

    def _cmd_market_install(
        self,
        slug: str,
        version: str = "",
        force: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        从组件市场下载并安装组件（下载 → 校验 → 审查 → 装入 → 热重载）

        按 market_info 给出的 install_url 下载安装包；元信息带 sha256/size 时先校验，
        再复用本地安装流程的审查与热重载，安装完组件立即成为可用工具。

        Args:
            slug: 组件标识（也接受 `market install <slug>` / 详情页 URL）
            version: 指定版本号（默认最新版）
            force: 本地已存在同名组件时是否覆盖
            dry_run: 只下载并审查，不写入组件目录（用于装前确认）

        Returns:
            {"success": True, "name":..., "target":..., "audit":{...},
             "market": {"slug":..., "version":..., "install_url":..., "sha256":..., "size":...}}
        """
        import shutil
        import tempfile
        from pathlib import Path

        base = self._market_base()
        target = self._market_slug(slug)
        if not target:
            return {"success": False, "error": "缺少参数 slug", "hint": '示例: {"command":"market_install","slug":"my-cell"}'}
        if not self._market_valid_slug(target):
            return {"success": False, "error": f"非法 slug: {target}", "hint": "slug 只能用小写字母、数字、-、_，2–40 字符"}

        try:
            meta = self._market_detail(target)
        except Exception as e:
            return {"success": False, "error": f"读取组件元信息失败: {e}", "slug": target, "market_base": base}

        versions = meta.get("versions") or []
        if not versions:
            return {"success": False, "error": "该组件没有可用版本", "slug": target}

        want = (version or "").strip().lstrip("vV")
        chosen = versions[0]
        if want:
            chosen = next(
                (v for v in versions if str(v.get("version", "")).lstrip("vV") == want), None
            )
            if not chosen:
                return {
                    "success": False,
                    "error": f"版本不存在: {version}",
                    "slug": target,
                    "available_versions": [str(v.get("version", "")) for v in versions],
                }

        ver = str(chosen.get("version") or "")
        install_url = self._market_install_url(base, meta, chosen)
        expected_sha = str(chosen.get("sha256") or "").strip().lower()
        expected_size = chosen.get("size")
        if not install_url.startswith("https://"):
            return {"success": False, "error": f"安装包地址不是 https://：{install_url}", "slug": target}

        tmpdir = tempfile.mkdtemp(prefix="cellium_market_")
        try:
            dest = Path(tmpdir) / f"{target}-{ver}{self._market_archive_suffix(install_url)}"
            try:
                got = self._market_download(install_url, dest)
            except Exception as e:
                return {"success": False, "error": str(e), "slug": target, "install_url": install_url, "market_base": base}

            if isinstance(expected_size, int) and expected_size > 0 and got["size"] != expected_size:
                return {
                    "success": False,
                    "error": f"安装包大小不符：期望 {expected_size} 字节，实际 {got['size']} 字节",
                    "slug": target,
                    "version": ver,
                    "install_url": install_url,
                    "hint": "安装包可能已被替换，确认来源可信后再用 force 重装",
                }
            if expected_sha and got["sha256"] != expected_sha:
                return {
                    "success": False,
                    "error": "安装包 sha256 校验失败，内容与市场记录不一致",
                    "slug": target,
                    "version": ver,
                    "install_url": install_url,
                    "expected_sha256": expected_sha,
                    "actual_sha256": got["sha256"],
                }

            result = self._cmd_install_local(path=str(dest), force=force, dry_run=dry_run)
            result["market"] = {
                "slug": meta.get("slug", target),
                "version": ver,
                "install_url": install_url,
                "sha256": got["sha256"],
                "size": got["size"],
                "sha256_verified": bool(expected_sha),
                "dry_run": bool(dry_run),
            }
            if result.get("success") and not dry_run:
                result["message"] = f"已从市场安装 {result.get('name') or target} v{ver} 并热重载"
            return result
        finally:
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                pass

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
            "description": "组件生成器 — 创建、管理、安装 Cellium 组件（含组件市场搜索与一键安装）",
            "available_commands": commands,
            "command_count": len(commands),
            "usage_examples": [
                {"command": "generate", "args": {"name": "<组件名>", "description": "<一句话描述>"}, "description": "创建单文件组件（简单功能）"},
                {"command": "generate_package", "args": {"name": "<组件名>", "description": "<描述>", "modules": "[\"utils\",\"monitor\"]"}, "description": "创建包组件（复杂功能）"},
                {"command": "list", "args": {"show_commands": True}, "description": "列出所有已加载组件"},
                {"command": "info", "args": {"name": "<组件名>"}, "description": "查看指定组件详情"},
                {"command": "market_search", "args": {"keyword": "<关键词>", "category": "<分类>"}, "description": "在组件市场搜索组件（先看有什么）"},
                {"command": "market_info", "args": {"slug": "<组件标识>"}, "description": "查看市场组件元信息：版本、安装包地址、校验值"},
                {"command": "market_install", "args": {"slug": "<组件标识>", "version": "<可选版本>"}, "description": "从市场下载安装包，校验后安装并热重载"},
                {"command": "reload", "args": {}, "description": "手动触发热重载扫描"},
                {"command": "dir", "args": {}, "description": "获取组件文件夹真实路径（自动区分 pip/绿色版）"},
                {"command": "install_local", "args": {"path": "<文件夹或压缩包路径>"}, "description": "从本地文件夹/zip/tar.gz 安装组件（自动审查结构、组件类、依赖后装入）"},
                {"command": "cleanup", "args": {"dry_run": True}, "description": "预览/清理组件缓存"},
                {"command": "sandbox_list", "args": {}, "description": "查看免沙箱白名单（内置+自定义）"},
                {"command": "sandbox_add", "args": {"name": "<组件名>"}, "description": "把组件加入免沙箱白名单，改后自动热重载"},
                {"command": "sandbox_remove", "args": {"name": "<组件名>"}, "description": "把组件移出白名单，恢复沙箱隔离"},
            ],
            "_notes": [
                "这是系统内置组件（白名单豁免），负责创建和管理其他 Cellium 组件",
                "安装市场组件的标准链路：market_search 找组件 → market_info 看版本与包地址 → market_install 安装",
                "market_install 会先校验 sha256/size（元信息有时），再复用本地安装的审查流程，装完自动热重载",
                "市场地址默认 https://components.cellium.top，可用环境变量 CELLIUM_COMPONENTS_BASE 指向镜像",
                "generate 创建单文件组件，适合简单功能",
                "generate_package 创建包组件（文件夹形式），适合复杂功能，便于模块解耦",
                "每次调用必须带 command 字段指定子命令名",
                "调用格式: {\"command\": \"generate\", \"name\": \"my_tool\", ...}",
                "sandbox_add 让组件在主进程直接执行（不进沙箱子进程），白名单持久化到 config/sandbox.yaml，pip 与绿色版通用",
                "内置组件（builtin）已硬编码免沙箱，sandbox_add 只用于自定义组件",
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
                "generate_package": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "required_params": ["name"],
                    "optional_params": ["description", "commands", "modules"],
                    "hint": (
                        '调用示例: 调用 component 工具时使用 command="generate_package"\n'
                        '  - name (必填): 组件名，如 eth_quant、data_pipeline\n'
                        '  - description (选填): 功能描述\n'
                        '  - commands (选填): JSON数组定义命令\n'
                        '  - modules (选填): JSON数组定义额外模块，如 ["utils","monitor"]\n'
                        '\n'
                        '示例: {"command":"generate_package","name":"eth_quant","description":"交易组件","modules":"[\\"monitor\\",\\"api\\"]"}\n'
                        '\n'
                        '包组件结构:\n'
                        '  components/{name}/\n'
                        '  ├── __init__.py\n'
                        '  ├── core.py (主组件类)\n'
                        '  ├── utils.py (额外模块)\n'
                        '  └── monitor.py (额外模块)'
                    ),
                },
                "list": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "params": {
                        "show_commands": "是否显示命令详情（默认 false）",
                        "search": "按组件名模糊匹配（不区分大小写）",
                        "page": "页码（从 1 开始，默认 1）",
                        "page_size": "每页数量（默认 27）",
                    },
                    "hint": (
                        '调用示例:\n'
                        '  - {"command":"list"}\n'
                        '  - {"command":"list","show_commands":false}\n'
                        '  - {"command":"list","search":"file","page":1,"page_size":5}\n'
                        '返回: components, total, page, total_pages, has_next, has_prev'
                    ),
                },
                "info": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": '调用示例: {"command":"info","name":"<组件名>"}',
                },
                "reload": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": '调用示例: {"command":"reload"}',
                },
                "dir": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": (
                        '调用示例: {"command":"dir"}\n'
                        '返回: components_dir（真实路径）, mode（source=绿色版 / installed=pip 安装）, '
                        'mode_desc, exists, component_count\n'
                        '路径自动兼容两种模式：绿色版返回项目 components/ 目录，pip 安装返回 cellium/components 数据目录'
                    ),
                },
                "install_local": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "required_params": ["path"],
                    "optional_params": ["force", "dry_run"],
                    "hint": (
                        '调用示例:\n'
                        '  - {"command":"install_local","path":"D:/dl/my_comp.zip","dry_run":true}  # 先审查\n'
                        '  - {"command":"install_local","path":"D:/dl/my_comp"}                     # 装文件夹\n'
                        '  - {"command":"install_local","path":"D:/dl/my_comp","force":true}         # 覆盖已有\n'
                        '支持: .py 单文件 / 含 __init__.py 的包目录 / .zip / .tar.gz；\n'
                        '审查内容: 是否含 BaseCell 组件类、语法错误、第三方依赖缺失、危险调用（os.system/subprocess/eval 等告警）；\n'
                        '装完自动热重载并注册为 LLM 工具'
                    ),
                },
                "market_search": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "optional_params": ["keyword", "category", "tags", "page", "page_size", "refresh"],
                    "hint": (
                        '调用示例:\n'
                        '  - {"command":"market_search","keyword":"pdf"}\n'
                        '  - {"command":"market_search","category":"automation","page":1,"page_size":10}\n'
                        '只读市场索引 /data/index.json，返回 slug/name/summary/tags/latest_version；\n'
                        '每条结果带 next 提示，可直接用 market_info 看详情'
                    ),
                },
                "market_info": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "required_params": ["slug"],
                    "optional_params": ["version"],
                    "hint": (
                        '调用示例: {"command":"market_info","slug":"my-cell"}\n'
                        '返回 install_url（安装包下载地址）、checks（sha256/size）、versions、readme、\n'
                        '以及可直接执行的 install 参数（{"command":"market_install","slug":...,"version":...}）'
                    ),
                },
                "market_install": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "required_params": ["slug"],
                    "optional_params": ["version", "force", "dry_run"],
                    "hint": (
                        '调用示例:\n'
                        '  - {"command":"market_install","slug":"my-cell","dry_run":true}   # 下载+审查，不写入\n'
                        '  - {"command":"market_install","slug":"my-cell"}                  # 装最新版\n'
                        '  - {"command":"market_install","slug":"my-cell","version":"1.0.0"}\n'
                        '流程: 读元信息 → 下载 install_url → 校验 sha256/size（元信息有才校验）\n'
                        '      → 复用 install_local 审查与安装 → 热重载并注册为工具'
                    ),
                },
                "cleanup": {
                    "focused_command": topic,
                    "command_description": commands.get(topic),
                    "hint": (
                        '调用示例:\n'
                        '  - {"command":"cleanup"}                    # 预览模式，查看问题\n'
                        '  - {"command":"cleanup","dry_run":false}     # 安全清理（推荐）\n'
                        '  - {"command":"cleanup","dry_run":false,"force":true}  # 强制清理'
                    ),
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
