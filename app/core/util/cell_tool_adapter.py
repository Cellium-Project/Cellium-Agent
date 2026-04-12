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
    "web_query",        # WebQuery — 需要网络请求
    "qq_files",         # QQFiles — 需要访问主进程的 ChannelManager
}

# 是否启用沙箱模式（可通过配置关闭）
SANDBOX_ENABLED = True


class CellToolAdapter(BaseTool):
    """
    BaseCell → BaseTool 适配器

    将任意 BaseCell/ICell 实例包装为 AgentLoop 可识别的 Tool 对象。
    LLM 调用工具时，自动路由到对应组件的 _cmd_ 方法。

    ★ 安全机制：
    - 非白名单组件在子进程沙箱中执行
    - 组件崩溃不影响主进程
    - 组件无法直接访问主进程内存
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

        # ★ 关键：设置 name 和 description（BaseTool 需要）
        # tool_name 格式: "cell_name" （纯小写，与组件标识一致）
        self.name = getattr(cell, 'cell_name', type(cell).__name__.lower())

        # 从类 docstring 或第一个命令描述提取工具描述
        doc = (type(cell).__doc__ or "").strip()
        if doc:
            # 取第一行作为简短描述
            self.description = doc.split('\n')[0].strip()
        else:
            commands = cell.get_commands()
            if commands:
                first_cmd_desc = list(commands.values())[0]
                self.description = f"组件 [{self.name}] — {first_cmd_desc}"
            else:
                self.description = f"组件 [{self.name}]"

        # ★ 决定是否使用沙箱
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

        ★ 关键：dict 模式不委托 BaseTool.execute()，
           因为 _cmd_ 方法定义在底层 cell 上而非本适配器上。
           自行提取 command、清洗参数后直接调用 self._cell.execute()

        ★ 安全：非白名单组件在子进程沙箱中执行，
           组件崩溃不影响主进程。
        """
        if self._use_sandbox:
            return self._execute_in_sandbox(command, *args, **kwargs)
        else:
            return self._execute_direct(command, *args, **kwargs)

    def _execute_in_sandbox(self, command, *args, **kwargs) -> Dict[str, Any]:
        """在沙箱中执行组件命令"""
        try:
            from app.core.util.component_sandbox import ComponentSandbox, SandboxProcess

            # 获取或创建沙箱
            sandbox = ComponentSandbox.get_sandbox(self.name)

            # 初始化沙箱（仅首次）
            if not sandbox._initialized:
                source_file = self._get_component_source()
                class_name = type(self._cell).__name__

                if source_file:
                    sandbox.init_component(source_file, class_name)
                else:
                    # 无法获取源文件，回退到直接执行
                    logger.warning(
                        "[CellToolAdapter] %s 无法获取源文件，回退到直接执行",
                        self.name,
                    )
                    return self._execute_direct(command, *args, **kwargs)

            # 执行命令
            if isinstance(command, dict):
                all_args = dict(command)
                cmd_name = (all_args.pop("command") or "").strip()
                if not cmd_name:
                    cmd_name = self._infer_command(all_args)

                # 过滤有效参数
                target_method = getattr(self._cell, f"{self.COMMAND_PREFIX}{cmd_name}", None)
                if target_method:
                    sig = inspect.signature(target_method)
                    valid_params = {p for p in sig.parameters if p != "self"}
                    all_args = {k: v for k, v in all_args.items() if k in valid_params}

                result = sandbox.execute(cmd_name, kwargs=all_args)
            else:
                result = sandbox.execute(command, args=list(args), kwargs=kwargs)

            # 确保返回值格式正确
            if not isinstance(result, dict):
                result = {"result": result}
            result.setdefault("_source", f"component:{self.name}")
            result.setdefault("_sandboxed", True)

            return result

        except Exception as e:
            logger.error(
                "[CellToolAdapter] %s 沙箱执行失败，回退到直接执行: %s",
                self.name, e,
            )
            # 沙箱失败时回退到直接执行（带运行时保护）
            return self._execute_direct(command, *args, **kwargs)

    def _execute_direct(self, command, *args, **kwargs) -> Dict[str, Any]:
        """直接执行组件命令（无沙箱）"""
        try:
            if isinstance(command, dict):
                # ── LLM 模式：自行处理（不走 super）──
                all_args = dict(command)
                cmd_name = (all_args.pop("command") or "").strip()

                if not cmd_name:
                    cmd_name = self._infer_command(all_args)

                logger.info(
                    "[CellToolAdapter] dict 模式 | 提取 command=%s | 原始 keys=%s",
                    cmd_name, list(all_args.keys()),
                )

                # 验证命令存在
                target_method_name = f"{self.COMMAND_PREFIX}{cmd_name}" if cmd_name else None
                if not target_method_name or not hasattr(self._cell, target_method_name):
                    raise ValueError(
                        f"Command '{cmd_name}' not found in tool '{self.tool_name}'. "
                        f"Available: {list(self.get_commands().keys())}"
                    )

                # ★ 简化：只过滤目标方法能接受的参数，不做前缀转换（与 BaseTool 统一）
                target_method = getattr(self._cell, target_method_name)
                sig = inspect.signature(target_method)
                valid_params = {p for p in sig.parameters if p != "self"}

                cleaned_args = {k: v for k, v in all_args.items() if k in valid_params}

                logger.info(
                    "[CellToolAdapter] 调用命令 | 命令=%s | 参数=%s",
                    cmd_name, list(cleaned_args.keys()),
                )
                # ★ 直接调用底层组件的 execute
                result = self._cell.execute(cmd_name, **cleaned_args)
            else:
                # 标准模式：直接传给组件
                result = self._cell.execute(command, *args, **kwargs)

            # ★ 确保返回值是 dict（AgentLoop 需要统一格式）
            if not isinstance(result, dict):
                result = {"result": result}

            # ★ 标记来源组件
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
            return {
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": tb_str,
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

        # ★ 快速路径：检查是否有显式的 command 字段
        explicit = all_args.get("command", "")
        if explicit and explicit.strip():
            return explicit.strip()

        # 收集有意义的值（排除空串/None/False/0/空容器）
        non_empty = {k: v for k, v in all_args.items()
                     if v not in (None, "", False, 0, [], {}, b"")}
        if not non_empty:
            return ""

        # ★ 快速路径：是否有键名恰好等于某个命令名（如 {"build": "xxx"}）
        for cmd_name in commands:
            if cmd_name in non_empty and non_empty[cmd_name] not in (None, "", False):
                logger.info("[CellToolAdapter] 直接命中 | key='%s' == command", cmd_name)
                return cmd_name

        # ★ 构建每个子命令的参数集（从 _cell 的方法签名提取）
        cmd_param_map: Dict[str, set] = {}       # {cmd_name: {param_names}}
        cmd_required_map: Dict[str, set] = {}     # {cmd_name: {required_param_names}}

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

        # ★ 覆盖度评分
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

        # ★ 歧义检测：最高分和次高分接近时记录警告
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

        # 多命令模式：用 command 字段分发
        # ★ 简化设计：参数名直接用方法签名原名（无前缀），与 BaseTool 统一
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
                # ★ 直接用原始参数名，不加前缀
                all_properties[pname] = {
                    **pinfo,
                    "description": f"[{cmd['command']}] {pname} — {'必填' if pname in cmd.get('required', []) else '选填'}",
                }

        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": f"{self.description}\n\n可用命令: {', '.join(c['command'] for c in commands_info)}\n★ 参数直接使用原始名称，无需添加子命令前缀",
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
