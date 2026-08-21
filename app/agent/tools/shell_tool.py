# -*- coding: utf-8 -*-

import logging
import os
import sys
import pathlib
from typing import Dict, Any, List

from .base_tool import BaseTool

logger = logging.getLogger(__name__)


def _detect_embedded_python() -> Dict[str, str]:
    """
    检测嵌入式 Python 环境

    Returns:
        {
            "is_embedded": "true/false",
            "python_path": "嵌入式 Python 路径",
            "libs_path": "依赖安装目录",
            "pip_cmd": "安装依赖的完整命令",
        }
    """
    exe_dir = pathlib.Path(sys.executable).resolve().parent

    if exe_dir.name == "runtime":
        project_root = exe_dir.parent
        libs_dir = project_root / "libs"
        
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
    通用 Shell 命令执行工具

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
        env_info = _detect_embedded_python()

        base_desc = (
            "执行系统命令。\n\n"
            "**cmd 参数**：字符串或参数数组\n"
            "- 数组直接传给进程，无 shell 解析，适合 Python/脚本和复杂参数\n"
            "- 字符串经 shell 解析，适合 pipe (`|`)、`&&`、`||`、重定向 (`>`)、wildcard (`*`)\n"
            "- Windows: PowerShell 语法 | Linux/Mac: bash 语法\n\n"
            "| 子命令 | 用途 | 参数 |\n"
            "|--------|------|------|\n"
            "| `run` | 执行命令 | `argv`(优先) 或 `cmd` |\n"
            "| `list` | 列出后台任务 | - |\n"
            "| `output` | 获取任务输出 | `task_id` |\n"
            "| `kill` | 终止后台任务 | `task_id` |\n\n"
            "**铁律**: 长运行服务必须 `background=true`"
        )

        if env_info["is_embedded"] == "true":
            env_note = (
                f"\n\n**【打包环境 Python 信息】**\n"
                f"- 嵌入式 Python: `{env_info['python_path']}`\n"
                f"- 依赖目录: `{env_info['libs_path']}`\n"
                f"- **安装依赖**: `{env_info['pip_cmd']}`\n"
            )
            return base_desc + env_note

        return base_desc

    def __init__(self, shell=None):
        super().__init__()
        self.shell = shell

    # ================================================================
    #  LLM 接口层 — definition（覆写 BaseTool）
    # ================================================================

    @property
    def definition(self) -> Dict:
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
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": "[run] 命令：字符串经 shell 执行，数组直接执行",
                        },
                        "background": {
                            "type": "boolean",
                            "description": "[run] 是否后台运行（默认 false）",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "[output/kill] 任务 ID",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "[run] 超时秒数（可选，默认120秒）",
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
        cmd = args.get("cmd", "")
        background = args.get("background", False)
        timeout = args.get("timeout")

        if not self.shell:
            return {"success": False, "error": "Shell 未初始化"}
        if not cmd or (isinstance(cmd, str) and not cmd.strip()):
            return {"success": False, "error": "缺少 cmd 参数"}
        if not isinstance(cmd, (str, list)) or (isinstance(cmd, list) and not cmd):
            return {"success": False, "error": "cmd 必须是非空字符串或参数数组"}

        try:
            label = " ".join(cmd[:5]) if isinstance(cmd, list) else cmd[:100]
            logger.info("[ShellTool] run | cmd=%s | background=%s | timeout=%s", label, background, timeout)
            payload: Dict[str, Any] = {
                "cmd": cmd,
                "run_in_background": background,
            }
            if timeout:
                payload["timeout"] = timeout
            result = self.shell.execute(payload)

            if background and result.get("status") == "background_started":
                return {
                    "success": True,
                    "task_id": result.get("task_id"),
                    "output_file": result.get("output_file"),
                    "message": f"后台任务已启动，ID: {result.get('task_id')}",
                }

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
            bg_tasks = getattr(self.shell, "_background_tasks", {})
            task = bg_tasks.get(task_id)

            if not task:
                return {"success": False, "error": f"任务不存在: {task_id}"}

            output_file = task.get("output_file")
            if not output_file or not os.path.exists(output_file):
                return {
                    "success": True,
                    "task_id": task_id,
                    "output": "",
                    "running": True,
                    "message": "任务刚启动，暂无输出",
                }

            with open(output_file, "r", encoding="utf-8") as f:
                content = f.read()

            proc = task["process_ref"].get("p")
            future = task.get("future")
            is_running = proc and proc.poll() is None
            is_done = future and future.done() if future else False

            return {
                "success": True,
                "task_id": task_id,
                "output": content,
                "running": is_running,
                "exit_code": proc.returncode if not is_running else None,
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

            if not sub_cmd and (command.get("cmd") or command.get("argv")):
                sub_cmd = "run"

            if sub_cmd and sub_cmd not in ("run", "list", "output", "kill"):
                return self._cmd_run({
                    "cmd": command.get("command", ""),
                    "background": command.get("run_in_background", False),
                })

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
