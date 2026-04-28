# -*- coding: utf-8 -*-
"""
Shell 工具 — 通用的命令执行工具（支持后台任务管理）

设计原则：
  - 对 LLM 暴露子命令接口：run / list / output / kill
  - 后台任务可异步执行、查询输出、主动关闭
  - 安全层由 CelliumShell (SecurityPolicy) 兜底

子命令：
  - run: 执行命令（同步或后台）
  - list: 列出所有后台任务
  - output: 获取后台任务的输出
  - kill: 终止后台任务

用法:
    tool = ShellTool(shell=my_shell)
    # 执行快速命令
    result = tool.execute({"command": "run", "cmd": "ls -la"})
    # 启动后台服务
    result = tool.execute({"command": "run", "cmd": "python server.py", "background": true})
    # 查看后台任务
    result = tool.execute({"command": "list"})
    # 获取任务输出
    result = tool.execute({"command": "output", "task_id": "bg_xxx"})
    # 终止任务
    result = tool.execute({"command": "kill", "task_id": "bg_xxx"})
"""

import logging
import os
import sys
import pathlib
from typing import Dict, Any, List

from .base_tool import BaseTool

logger = logging.getLogger(__name__)


def _detect_embedded_python() -> Dict[str, str]:
    """
    检测嵌入式 Python 环境（打包版）

    Returns:
        {
            "is_embedded": "true/false",
            "python_path": "嵌入式 Python 路径",
            "libs_path": "依赖安装目录",
            "pip_cmd": "安装依赖的完整命令",
        }
    """
    # 检测是否有 runtime/python.exe 或 runtime/python（打包版结构）
    exe_dir = pathlib.Path(sys.executable).resolve().parent

    # 打包版结构：exe 在 runtime/ 目录
    if exe_dir.name == "runtime":
        project_root = exe_dir.parent
        libs_dir = project_root / "libs"
        
        # 跨平台 Python 可执行文件名
        if sys.platform == "win32":
            python_exe = exe_dir / "python.exe"
        else:
            python_exe = exe_dir / "python"

        if python_exe.exists():
            return {
                "is_embedded": "true",
                "python_path": str(python_exe),
                "libs_path": str(libs_dir),
                "pip_cmd": f'"{python_exe}" -m pip install <package> --target="{libs_dir}"',
                "project_root": str(project_root),
            }

    # 开发环境
    return {
        "is_embedded": "false",
        "python_path": sys.executable,
        "libs_path": "",
        "pip_cmd": "pip install <package>",
        "project_root": str(pathlib.Path.cwd()),
    }


class ShellTool(BaseTool):
    """
    通用 Shell 命令执行工具（支持后台任务管理）

    子命令：
      - run: 执行命令
      - list: 列出后台任务
      - output: 获取任务输出
      - kill: 终止任务
    """

    name = "shell"

    @property
    def tool_name(self) -> str:
        """工具名称（LLM function calling 用）"""
        return "shell"

    @property
    def description(self) -> str:
        """动态生成 description（包含环境信息）"""
        env_info = _detect_embedded_python()

        base_desc = (
            "执行系统命令。\n\n"
            "**重要**: Windows 环境使用 PowerShell，Linux/Mac 使用 bash。\n"
            "- Windows: 用 PowerShell 语法（如 `Get-ChildItem`、`pwd`、`$env:PATH`）\n"
            "- Linux/Mac: 用 bash 语法（如 `ls -la`、`pwd`、`echo $PATH`）\n\n"
            "| 子命令 | 用途 | 必填参数 |\n"
            "|--------|------|----------|\n"
            "| `run` | 执行命令 | `cmd` |\n"
            "| `list` | 列出后台任务 | - |\n"
            "| `output` | 获取任务输出 | `task_id` |\n"
            "| `kill` | 终止后台任务 | `task_id` |\n\n"
            "**铁律**: 长运行服务（server/dev 等）必须 `background=true`，否则会阻塞超时\n"
            "**注意**: Windows 上可用 `dir`、`type`、`cd` 等 cmd 命令，会自动回退到 cmd.exe"
        )

        # 打包环境：注入 Python 路径信息
        if env_info["is_embedded"] == "true":
            env_note = (
                f"\n\n**【打包环境 Python 信息】**\n"
                f"- 嵌入式 Python: `{env_info['python_path']}`\n"
                f"- 依赖目录: `{env_info['libs_path']}`\n"
                f"- **安装依赖必须用**: `{env_info['pip_cmd']}`\n"
                f"- 示例: `{env_info['python_path']} -m pip install qrcode --target=\"{env_info['libs_path']}\"`\n"
                f"**切勿**使用系统 pip，否则依赖无法被本程序加载！"
            )
            return base_desc + env_note

        return base_desc

    def __init__(self, shell=None, mp_manager=None):
        super().__init__()
        self.shell = shell
        self.mp_manager = mp_manager

    # ================================================================
    #  LLM 接口层 — definition（覆写 BaseTool）
    # ================================================================

    @property
    def definition(self) -> Dict:
        """生成 LLM function calling 定义"""
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": ["run", "list", "output", "kill"],
                            "description": "子命令：run/list/output/kill",
                        },
                        "cmd": {
                            "type": "string",
                            "description": "[run] 要执行的命令",
                        },
                        "background": {
                            "type": "boolean",
                            "description": "[run] 是否后台运行（默认 false）",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "[output/kill] 任务 ID",
                        },
                    },
                    "required": ["command"],
                },
            },
        }

    # ================================================================
    #  子命令实现
    # ================================================================

    def _cmd_run(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行命令

        参数：
          - cmd: 要执行的命令（必填）
          - background: 是否后台运行（默认 false）
        """
        cmd = args.get("cmd", "")
        background = args.get("background", False)

        if not cmd or not cmd.strip():
            return {"success": False, "error": "缺少 cmd 参数"}

        if not self.shell:
            return {"success": False, "error": "Shell 未初始化"}

        if not self._check_permission(cmd):
            return {"success": False, "error": f"Permission denied: {cmd[:100]}"}

        try:
            logger.info("[ShellTool] run | cmd=%s | background=%s", cmd[:100], background)
            result = self.shell.execute({
                "command": cmd,
                "run_in_background": background,
            })

            # 后台任务返回 task_id
            if background and result.get("status") == "background_started":
                return {
                    "success": True,
                    "task_id": result.get("task_id"),
                    "output_file": result.get("output_file"),
                    "message": f"后台任务已启动，ID: {result.get('task_id')}",
                }

            # 前台任务直接返回结果
            return result

        except Exception as e:
            logger.error("[ShellTool] run 失败 | error=%s", str(e))
            return {"success": False, "error": f"执行失败: {str(e)}"}

    def _cmd_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        列出所有后台任务

        返回：
          - tasks: 任务列表 [{task_id, status, pid?, ...}]
        """
        if not self.shell:
            return {"success": False, "error": "Shell 未初始化"}

        try:
            tasks = self.shell.list_background_tasks()
            return {
                "success": True,
                "tasks": tasks,
                "count": len(tasks),
            }
        except Exception as e:
            logger.error("[ShellTool] list 失败 | error=%s", str(e))
            return {"success": False, "error": f"查询失败: {str(e)}"}

    def _cmd_output(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取后台任务的输出

        参数：
          - task_id: 任务 ID（必填）
        """
        task_id = args.get("task_id", "")
        if not task_id:
            return {"success": False, "error": "缺少 task_id 参数"}

        if not self.shell:
            return {"success": False, "error": "Shell 未初始化"}

        try:
            result = self.shell.get_background_result(task_id, timeout=0)
            if result is None:
                # 尝试读取输出文件
                output_file = args.get("output_file")
                if output_file:
                    import os
                    if os.path.exists(output_file):
                        with open(output_file, "r", encoding="utf-8") as f:
                            content = f.read()
                        return {
                            "success": True,
                            "task_id": task_id,
                            "output": content,
                            "running": True,
                        }
                return {"success": False, "error": f"任务不存在或已完成: {task_id}"}

            return {
                "success": True,
                "task_id": task_id,
                "output": result.get("output_file"),
                "exit_code": result.get("exit_code"),
                "done": True,
            }
        except Exception as e:
            logger.error("[ShellTool] output 失败 | error=%s", str(e))
            return {"success": False, "error": f"获取输出失败: {str(e)}"}

    def _cmd_kill(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        终止后台任务

        参数：
          - task_id: 任务 ID（必填）
        """
        task_id = args.get("task_id", "")
        if not task_id:
            return {"success": False, "error": "缺少 task_id 参数"}

        if not self.shell:
            return {"success": False, "error": "Shell 未初始化"}

        try:
            killed = self.shell.kill_background_task(task_id)
            if killed:
                return {
                    "success": True,
                    "task_id": task_id,
                    "message": f"任务 {task_id} 已终止",
                }
            return {"success": False, "error": f"任务不存在: {task_id}"}
        except Exception as e:
            logger.error("[ShellTool] kill 失败 | error=%s", str(e))
            return {"success": False, "error": f"终止失败: {str(e)}"}

    # ================================================================
    #  权限检查
    # ================================================================

    def _check_permission(self, cmd: str) -> bool:
        """权限检查"""
        if hasattr(self, 'permissions') and self.permissions is not None:
            if self.permissions.check_permission(cmd):
                return True
        return True

    # ================================================================
    #  兼容旧接口
    # ================================================================

    def execute(self, command="", *args, **kwargs) -> Dict[str, Any]:
        """
        统一执行入口

        支持两种模式：
        1. 子命令模式: {"command": "run", "cmd": "ls -la", "background": false}
        2. 简单模式: {"command": "ls -la"} 或 "ls -la"（向后兼容）
        """
        # 字符串模式（向后兼容）
        if isinstance(command, str) and command.strip():
            logger.info("[ShellTool] execute(str) | command=%s", command[:200])
            return self._cmd_run({"cmd": command, "background": False})

        # dict 模式
        if isinstance(command, dict):
            sub_cmd = command.get("command", "")

            # 兼容旧格式：{"command": "ls -la"} 视为 run
            if sub_cmd and sub_cmd not in ("run", "list", "output", "kill"):
                return self._cmd_run({
                    "cmd": command.get("command", ""),
                    "background": command.get("run_in_background", False),
                })

            # 新格式：子命令分发
            if sub_cmd == "run":
                return self._cmd_run(command)
            elif sub_cmd == "list":
                return self._cmd_list(command)
            elif sub_cmd == "output":
                return self._cmd_output(command)
            elif sub_cmd == "kill":
                return self._cmd_kill(command)
            else:
                return {"success": False, "error": f"未知子命令: {sub_cmd}，可用: run, list, output, kill"}

        return {"success": False, "error": "未提供有效的 command 参数"}
