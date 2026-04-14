# -*- coding: utf-8 -*-
"""
Agent 工具基类 - 借鉴 BaseCell 的 _cmd_ 命令映射模式

使用方式：
    class MyTool(BaseTool):
        name = "my_tool"
        description = "工具描述"

        def _cmd_action1(self, param: str) -> dict:
            \"\"\"动作1的描述\"\"\"
            return {"result": ...}

        def _cmd_action2(self) -> dict:
            \"\"\"动作2的描述\"\"\"
            return {"result": ...}

    tool = MyTool()
    tool.execute("action1", "hello")   # 调用 _cmd_action1
    tool.get_commands()               # 返回 {"action1": "动作1的描述", "action2": "动作2的描述"}
    tool.definition                    # 返回 LLM function calling 格式
"""

import logging
import inspect
from typing import Dict, Any


logger = logging.getLogger(__name__)


class BaseTool:
    """工具基类 — 声明式命令注册"""

    # 子类必须覆盖
    name: str = ""
    description: str = ""

    # 命令前缀（与 BaseCell 保持一致）
    COMMAND_PREFIX = "_cmd_"

    def __init__(self):
        if not self.name:
            self.name = self.__class__.__name__.lower()

    @property
    def tool_name(self) -> str:
        """完整工具名（格式: category:name）"""
        return f"{self.__class__.__module__.split('.')[-1]}:{self.name}"

    def execute(self, command="", *args, **kwargs) -> Dict[str, Any]:
        """
        执行命令（统一入口）

        Args:
            command: 命令名称（对应 _cmd_xxx 方法）
                     - str 时：直接作为命令名查找并调用 _cmd_xxx 方法
                     - dict 时（LLM 返回的完整参数）: 从中提取 command 字段分发，剩余字段传给目标方法
            *args: 位置参数
            **kwargs: 关键字参数（优先，支持 JSON 反序列化后的 dict）

        Returns:
            命令执行结果
        """
        if isinstance(command, dict):
            all_args = command
            cmd_name = (all_args.pop("command") or "").strip()

            if not cmd_name:
                cmd_name = self._infer_command(all_args)
                if cmd_name:
                    logger.info(
                        "[BaseTool] 智能推断 command=%s（原字段为空）| 有值keys=%s",
                        cmd_name,
                        [k for k, v in all_args.items() if v not in (None, "", False, 0, [], {})],
                    )

            logger.info(
                "[BaseTool] dict 模式 | 提取 command=%s | 原始 keys=%s",
                cmd_name, list(all_args.keys()),
            )
            method_name = f"{self.COMMAND_PREFIX}{cmd_name}" if cmd_name else None

            if method_name and hasattr(self, method_name):
                method = getattr(self, method_name)
                if callable(method):
                    import inspect as _inspect

                    sig = _inspect.signature(method)
                    valid_params = {p for p in sig.parameters if p != "self"}

                    cleaned_args = {k: v for k, v in all_args.items() if k in valid_params}

                    logger.info(
                        "[BaseTool] 调用命令 | 命令=%s | 参数=%s",
                        cmd_name, list(cleaned_args.keys()),
                    )
                    return method(**cleaned_args)

            raise ValueError(
                f"Command '{cmd_name}' not found in tool '{self.tool_name}'. "
                f"Available: {list(self.get_commands().keys())}"
            )

        method_name = f"{self.COMMAND_PREFIX}{command}" if command else None

        if method_name and hasattr(self, method_name):
            method = getattr(self, method_name)
            if callable(method):
                if kwargs:
                    return method(**kwargs)
                elif args:
                    return method(*args)
                else:
                    return method()

        raise ValueError(
            f"Command '{command}' not found in tool '{self.tool_name}'. "
            f"Available: {list(self.get_commands().keys())}"
        )

    def _infer_command(self, all_args: dict) -> str:
        """根据参数覆盖度推断 LLM 意图调用的子命令。

        策略：检查每个子命令的「有多少参数出现在 all_args 中」，
        选择匹配度最高的子命令。必填参数命中权重更高。

        适用场景：LLM 忘记填 command 字段但正确传了业务参数时。
        """
        from collections import defaultdict

        commands = self.get_commands()
        if not commands:
            return ""

        non_empty = {k: v for k, v in all_args.items()
                     if v not in (None, "", False, 0, [], {}, b"")}
        if not non_empty:
            return ""

        for cmd_name in commands:
            if cmd_name in non_empty and non_empty[cmd_name] not in (None, "", False):
                logger.info("[BaseTool] 直接命中 | key='%s' == command", cmd_name)
                return cmd_name

        cmd_param_map: Dict[str, set] = {}  # {cmd_name: {param_names}}
        cmd_required_map: Dict[str, set] = {}  # {cmd_name: {required_param_names}}

        for cmd_name in commands:
            method = getattr(self, f"{self.COMMAND_PREFIX}{cmd_name}", None)
            if not method or not callable(method):
                continue
            try:
                sig = inspect.signature(method)
                param_names = {p for p in sig.parameters if p != "self"}
                required = {
                    p for p in param_names
                    if sig.parameters[p].default == inspect.Parameter.empty
                }
                cmd_param_map[cmd_name] = param_names
                cmd_required_map[cmd_name] = required
            except (ValueError, TypeError):
                continue

        cmd_scores: Dict[str, float] = defaultdict(float)
        input_keys = set(non_empty.keys())

        for cmd_name, cmd_params in cmd_param_map.items():
            overlap = input_keys & cmd_params
            if not overlap:
                continue

            required_hits = overlap & cmd_required_map.get(cmd_name, set())
            optional_hits = overlap - required_hits

            score = len(required_hits) * 2.0 + len(optional_hits) * 1.0
            cmd_scores[cmd_name] = score

        if not cmd_scores:
            return ""

        best_cmd = max(cmd_scores, key=cmd_scores.get)

        logger.info(
            "[BaseTool] 推断结果 | 选择=%s(%.1f) | 全部得分=%s",
            best_cmd, cmd_scores[best_cmd], dict(cmd_scores),
        )
        return best_cmd

    def get_commands(self) -> Dict[str, str]:
        """获取所有可用命令及其描述"""
        commands = {}
        for attr_name in dir(self):
            if attr_name.startswith(self.COMMAND_PREFIX):
                cmd_name = attr_name[len(self.COMMAND_PREFIX):]
                method = getattr(self, attr_name)
                if callable(method):
                    doc = (method.__doc__ or "").strip().split("\n")[0]
                    commands[cmd_name] = doc
        return commands

    @property
    def definition(self) -> Dict:
        """
        生成 LLM function calling 格式的工具定义

        自动从 _cmd_ 方法提取参数信息生成 schema。
        子类可覆盖此属性以提供自定义定义。
        """
        import inspect

        commands_info = []
        properties = {}
        required = []

        for cmd_name, cmd_desc in self.get_commands().items():
            method = getattr(self, f"{self.COMMAND_PREFIX}{cmd_name}", None)
            if method:
                sig = inspect.signature(method)
                
                _doc_lines = (method.__doc__ or "").strip().split("\n")
                _doc_summary = _doc_lines[0].strip() if _doc_lines else ""
                
                params = {}

                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue
                    param_info: Dict[str, Any] = {
                        "type": "string",
                        "description": f"[{cmd_name}] {param_name}",
                    }

                    annotation = param.annotation
                    if annotation != inspect.Parameter.empty:
                        type_map = {
                            int: "integer",
                            float: "number",
                            bool: "boolean",
                            list: "array",
                            dict: "object",
                            str: "string",
                        }
                        param_info["type"] = type_map.get(annotation, "string")

                    params[param_name] = param_info

                    if param.default == inspect.Parameter.empty:
                        required.append(param_name)

                commands_info.append({
                    "command": cmd_name,
                    "description": cmd_desc or _doc_summary,
                    "parameters": params,
                    "required": required.copy() if required else [],
                    "_summary": _doc_summary,  
                })
                required.clear()

        if len(commands_info) == 1:
            cmd = commands_info[0]
            return {
                "type": "function",
                "function": {
                    "name": self.tool_name,
                    "description": self.description or cmd["description"],
                    "parameters": {
                        "type": "object",
                        "properties": cmd["parameters"],
                        "required": cmd["required"],
                    },
                }
            }

        all_properties = {"command": {
            "type": "string",
            "description": "要执行的子命令（必填）",
            "enum": [c["command"] for c in commands_info],
        }}
        all_required = ["command"]

        _examples = []
        
        for cmd in commands_info:
            _req_params = [p for p in cmd["required"] if p != "self"]
            if _req_params:
                _example_parts = [f'"{_req_params[0]}": "<{_req_params[0]}>"']
            else:
                _example_parts = ['"(无需额外参数)"']
            _examples.append(f'  • {cmd["command"]}: {{" ".join(_example_parts)}}')
            
            for pname, pinfo in cmd["parameters"].items():
                all_properties[pname] = {
                    **pinfo, 
                    "description": (
                        f"[{cmd['command']}] {pname} — "
                        f"{'必填' if pname in cmd.get('required', []) else '选填'}"
                    ),
                }

        _desc_base = self.description or ""
        _example_text = "\n".join(_examples)
        _enhanced_desc = (
            f"{_desc_base}\n\n"
            f"§调用示例：\n{_example_text}\n"
            f"§参数直接使用原始名称（如 path, content, files），无需添加子命令前缀"
        )

        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": _enhanced_desc,
                "parameters": {
                    "type": "object",
                    "properties": all_properties,
                    "required": all_required,
                },
            }
        }

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.tool_name} commands={list(self.get_commands().keys())}>"
