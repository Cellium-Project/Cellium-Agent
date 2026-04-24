# -*- coding: utf-8 -*-
"""
CellToolAdapter — 组件→工具适配器

将 components/ 下的 BaseCell 组件包装成 AgentLoop 可用的 BaseTool。
关键能力：
  1. 把 cell_name 映射为 tool_name（LLM function calling 格式）
  2. 自动生成 definition（LLM 可理解的 JSON Schema）
  3. execute() 委托给组件的 execute() 方法
  4. 安全隔离：非白名单组件在子进程沙箱中执行

架构：
    BaseCell (components/xxx.py)
        ↓ CellToolAdapter 包装
    BaseTool (有 .definition + .execute)
        ↓ 注册到 ComponentToolRegistry
    AgentLoop._get_tools_definition() 动态读取
        ↓ 注入给 LLM

用法：
    adapter = CellToolAdapter(cell_instance)
    adapter.definition     # → {"type": "function", "function": {...}}
    adapter.execute("cmd_name", arg1="...")  # → 委托给组件
"""

import inspect
import logging
from typing import Any, Dict, Optional, Set

from app.agent.tools.base_tool import BaseTool
from app.core.interface.icell import ICell

logger = logging.getLogger(__name__)

# 白名单组件豁免沙箱隔离（系统核心组件，需要高权限）
EXEMPTED_NAMES: Set[str] = {
    "component",        # ComponentBuilder — 需要创建文件
    "skill_installer",  # SkillInstaller — 需要安装包
    "skill_manager",    # SkillManager — 只读操作，无需沙箱
    "web_query",        # WebQuery — 需要网络请求
    "qq_files",         # QQFiles — 需要访问主进程的 ChannelManager
    "telegram_files",   # TelegramFiles — 需要访问主进程的 ChannelManager
}

# 是否启用沙箱模式（可通过配置关闭）
SANDBOX_ENABLED = True


class CellToolAdapter(BaseTool):
    """
    BaseCell → BaseTool 适配器

    将任意 BaseCell/ICell 实例包装为 AgentLoop 可识别的 Tool 对象。
    LLM 调用工具时，自动路由到对应组件的 _cmd_ 方法。
    """

    def __init__(self, cell: ICell, use_sandbox: Optional[bool] = None):
        """
        Args:
            cell: 已实例化的 BaseCell 子类对象
            use_sandbox: 是否使用沙箱（None=自动判断）
        """
        self._cell = cell
        self._sandbox = None
        self._sandbox_initialized = False

        # 设置 name 和 description（BaseTool 需要）
        # tool_name 格式: "cell_name" （纯小写，与组件标识一致）
        self.name = getattr(cell, 'cell_name', type(cell).__name__.lower())

        # 从类 docstring 或第一个命令描述提取工具描述
        doc = (type(cell).__doc__ or "").strip()
        if doc:
            self.description = doc.split('\n')[0].strip()
        else:
            commands = cell.get_commands()
            if commands:
                first_cmd_desc = list(commands.values())[0]
                self.description = f"组件 [{self.name}] — {first_cmd_desc}"
            else:
                self.description = f"组件 [{self.name}]"

        self._use_sandbox = self._determine_sandbox_mode(use_sandbox)

        super().__init__()

    def _determine_sandbox_mode(self, use_sandbox: Optional[bool]) -> bool:
        """决定是否使用沙箱模式"""
        if not SANDBOX_ENABLED:
            return False

        # 显式指定
        if use_sandbox is not None:
            return use_sandbox

        # 自动判断：白名单组件不使用沙箱
        return self.name not in EXEMPTED_NAMES

    @property
    def cell(self) -> ICell:
        """获取底层组件实例"""
        return self._cell

    @property
    def tool_name(self) -> str:
        """工具名 = cell_name（LLM 调用时用这个名字）"""
        return self.name

    @property
    def component_type(self) -> str:
        """组件类型名（用于日志和调试）"""
        return type(self._cell).__name__

    def _get_component_source(self) -> Optional[str]:
        """获取组件源文件路径"""
        try:
            return inspect.getsourcefile(type(self._cell))
        except (TypeError, OSError):
            return None

    def execute(self, command="", *args, **kwargs) -> Dict[str, Any]:
        """
        执行命令 — 委托给底层组件的 execute()

        支持两种调用模式：
        1. command=str:   execute("do_something", arg1="val")
        2. command=dict:  execute({"command": "do_something", "arg1": "val"})  ← LLM 模式
        """
        if self._use_sandbox:
            return self._execute_in_sandbox(command, *args, **kwargs)
        else:
            return self._execute_direct(command, *args, **kwargs)

    def _execute_in_sandbox(self, command, *args, **kwargs) -> Dict[str, Any]:
        """在沙箱中执行组件命令"""
        try:
            from app.core.util.component_sandbox import ComponentSandbox, SandboxProcess

            sandbox = ComponentSandbox.get_sandbox(self.name)

            # 初始化沙箱
            if not sandbox._initialized:
                source_file = self._get_component_source()
                class_name = type(self._cell).__name__

                if source_file:
                    success = sandbox.init_component(source_file, class_name)
                    if not success:
                        logger.warning(
                            "[CellToolAdapter] %s 沙箱初始化失败，回退到直接执行",
                            self.name,
                        )
                        return self._execute_direct(command, *args, **kwargs)
                else:
                    logger.warning(
                        "[CellToolAdapter] %s 无法获取源文件，回退到直接执行",
                        self.name,
                    )
                    return self._execute_direct(command, *args, **kwargs)

            if isinstance(command, dict):
                all_args = dict(command)
                cmd_name = all_args.pop("command", None)
                if cmd_name:
                    cmd_name = cmd_name.strip()
                if not cmd_name:
                    cmd_name = self._infer_command(all_args)

                target_method = getattr(self._cell, f"{self.COMMAND_PREFIX}{cmd_name}", None)
                if target_method:
                    sig = inspect.signature(target_method)
                    valid_params = {p for p in sig.parameters if p != "self"}
                    all_args = {k: v for k, v in all_args.items() if k in valid_params}

                result = sandbox.execute(cmd_name, kwargs=all_args)
            else:
                result = sandbox.execute(command, args=list(args), kwargs=kwargs)

            if not isinstance(result, dict):
                result = {"result": result}
            result.setdefault("_source", f"component:{self.name}")
            result.setdefault("_sandboxed", True)

            return result

        except Exception as e:
            import traceback
            logger.error(
                "[CellToolAdapter] %s 沙箱执行失败，回退到直接执行: %s\n%s",
                self.name, e, traceback.format_exc()
            )
            # 沙箱失败时回退到直接执行
            return self._execute_direct(command, *args, **kwargs)

    def _execute_direct(self, command, *args, **kwargs) -> Dict[str, Any]:
        """直接执行组件命令（无沙箱）"""
        try:
            if isinstance(command, dict):
                # ── LLM 模式：自行处理（不走 super）──
                all_args = dict(command)
                cmd_name = all_args.pop("command", None)
                if cmd_name:
                    cmd_name = cmd_name.strip()

                if not cmd_name:
                    cmd_name = self._infer_command(all_args)

                logger.info(
                    "[CellToolAdapter] dict 模式 | 提取 command=%s | 原始 keys=%s",
                    cmd_name, list(all_args.keys()),
                )

                target_method_name = f"{self.COMMAND_PREFIX}{cmd_name}" if cmd_name else None
                if not target_method_name or not hasattr(self._cell, target_method_name):
                    raise ValueError(
                        f"Command '{cmd_name}' not found in tool '{self.tool_name}'. "
                        f"Available: {list(self.get_commands().keys())}"
                    )

                target_method = getattr(self._cell, target_method_name)
                sig = inspect.signature(target_method)
                valid_params = {p for p in sig.parameters if p != "self"}

                cleaned_args = {k: v for k, v in all_args.items() if k in valid_params}

                logger.info(
                    "[CellToolAdapter] 调用命令 | 命令=%s | 参数=%s",
                    cmd_name, list(cleaned_args.keys()),
                )
                result = self._cell.execute(cmd_name, **cleaned_args)
            else:

                result = self._cell.execute(command, *args, **kwargs)

            if not isinstance(result, dict):
                result = {"result": result}

            result.setdefault("_source", f"component:{self.name}")

            logger.info(
                "[CellToolAdapter] %s.%s OK | 返回 keys=%s",
                self.name,
                command if not isinstance(command, dict) else command.get("command", "?"),
                list(result.keys()),
            )
            return result

        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            logger.error(
                "[CellToolAdapter] %s.execute 失败: %s\n%s",
                self.name, e, tb_str,
            )

            error_msg = str(e)
            error_type = type(e).__name__

            hint = ""
            if "not found" in error_msg.lower() or "找不到" in error_msg:
                hint = f"可用的命令: {list(self.get_commands().keys())}"
            elif "missing" in error_msg.lower() or "缺少" in error_msg:
                hint = "请提供所有必填参数"
            elif "invalid" in error_msg.lower() or "无效" in error_msg:
                hint = "请检查参数格式是否正确"
            elif "timeout" in error_msg.lower() or "超时" in error_msg:
                hint = "操作超时，请稍后重试或检查网络连接"
            elif "connection" in error_msg.lower() or "连接" in error_msg:
                hint = "连接失败，请检查网络或URL是否正确"
            else:
                hint = "请检查命令名称和参数是否正确"

            return {
                "success": False,
                "error": error_msg,
                "error_type": error_type,
                "hint": hint,
                "command": cmd_name if isinstance(command, dict) else command,
                "_source": f"component:{self.name}",
                "status": "error",
            }

    def _infer_command(self, all_args: dict) -> str:
        """根据参数覆盖度推断 LLM 意图调用的子命令。

        策略：检查每个子命令的「有多少参数出现在 all_args 中」，
        选择匹配度最高的子命令。必填参数命中权重更高。
        与 BaseTool._infer_command 保持一致。
        """
        from collections import defaultdict

        commands = self.get_commands()
        if not commands:
            return ""

        explicit = all_args.get("command", "")
        if explicit and explicit.strip():
            return explicit.strip()

        non_empty = {k: v for k, v in all_args.items()
                     if v not in (None, "", False, 0, [], {}, b"")}
        if not non_empty:
            return ""

        for cmd_name in commands:
            if cmd_name in non_empty and non_empty[cmd_name] not in (None, "", False):
                logger.info("[CellToolAdapter] 直接命中 | key='%s' == command", cmd_name)
                return cmd_name

        cmd_param_map: Dict[str, set] = {}      
        cmd_required_map: Dict[str, set] = {}   

        for cmd_name in commands:
            method = getattr(self._cell, f"{self.COMMAND_PREFIX}{cmd_name}", None)
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
        best_score = cmd_scores[best_cmd]

        sorted_scores = sorted(cmd_scores.items(), key=lambda x: -x[1])
        if len(sorted_scores) >= 2:
            second_score = sorted_scores[1][1]
            if second_score > 0 and best_score > 0 and (best_score - second_score) / best_score < 0.3:
                logger.warning(
                    "[CellToolAdapter] 推断歧义 | 选择=%s(%.1f) | 次选=%s(%.1f)",
                    best_cmd, best_score,
                    sorted_scores[1][0], second_score,
                )

        logger.info(
            "[CellToolAdapter] 推断结果 | 选择=%s(%.1f) | 全部得分=%s",
            best_cmd, best_score, dict(cmd_scores),
        )
        return best_cmd

    def get_commands(self) -> Dict[str, str]:
        """获取组件的所有可用命令"""
        return self._cell.get_commands()

    @property
    def definition(self) -> Dict:
        """
        生成 LLM function calling 格式的工具定义

        结构与 BaseTool.definition 一致，确保 AgentLoop 能正确处理。
        """
        commands_info = []
        properties = {}
        required_all = []

        for cmd_name, cmd_desc in self.get_commands().items():
            method = getattr(self._cell, f"{self.COMMAND_PREFIX}{cmd_name}", None)
            if method is None or not callable(method):
                continue

            params = {}
            required = []

            # 提取方法参数签名
            sig = inspect.signature(method)
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue

                param_info: Dict[str, Any] = {
                    "type": "string",
                    "description": param_name,
                }

                # 类型推断
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

                    # array 类型需要指定 items
                    if annotation == list:
                        param_info["items"] = {"type": "string"}

                params[param_name] = param_info

                # 必填/选填
                if param.default == inspect.Parameter.empty:
                    required.append(param_name)

            commands_info.append({
                "command": cmd_name,
                "description": cmd_desc.strip(),
                "parameters": params,
                "required": required.copy(),
            })

        # 单命令模式：简化结构
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

        all_properties = {
            "command": {
                "type": "string",
                "description": f"[{self.name}] 要执行的子命令（必填）",
                "enum": [c["command"] for c in commands_info],
            }
        }
        all_required = ["command"]

        for cmd in commands_info:
            for pname, pinfo in cmd["parameters"].items():
                all_properties[pname] = {
                    **pinfo,
                    "description": f"[{cmd['command']}] {pname} — {'必填' if pname in cmd.get('required', []) else '选填'}",
                }

        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": f"{self.description}\n\n可用命令: {', '.join(c['command'] for c in commands_info)}\n参数直接使用原始名称，无需添加子命令前缀",
                "parameters": {
                    "type": "object",
                    "properties": all_properties,
                    "required": all_required,
                },
            }
        }

    def __repr__(self):
        cmds = list(self.get_commands().keys())
        sandbox_flag = " [sandbox]" if self._use_sandbox else ""
        return f"<CellToolAdapter name={self.name} component={self.component_type} commands={cmds}{sandbox_flag}>"
