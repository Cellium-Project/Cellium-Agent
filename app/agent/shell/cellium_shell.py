# -*- coding: utf-8 -*-
"""
CelliumShell — 跨平台命令执行器（Windows / macOS / Linux）
"""

import os
import sys
import re
import signal
import asyncio
import time
import tempfile
import logging
import subprocess
import shutil
from typing import Dict, Any, Optional, Callable, Union, List, Tuple
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


# =============================================================================
# 常量定义
# =============================================================================

DEFAULT_TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
HARD_TIMEOUT_SECONDS = 300
PREVIEW_SIZE_BYTES = 500

_WRITE_INDICATORS = [
    r"rm\s+-rf", r"rmdir\s+/s", r"del\s+/[fq]", r"remove-item\s+-recurse",
    r"format\s+", r"diskpart", r"mkfs\.", r"dd\s+if=",
    r"shutdown", r"reboot", r"stop-computer",
    r"reg\s+delete", r"bcdedit",
    r"sc\s+config", r"sc\s+delete",
    r"netsh\s+(?:firewall|advfirewall)",
    r">\s*/etc/", r">\s*~/\.",
]

READ_ONLY_PATTERNS = [
    r"^(?:ls|dir|pwd|cd|echo|cat|head|tail|grep|find|which|where|type|file|stat|cksum|md5sum|sha1sum|sha256sum|wc|sort|uniq|cut|tr|tee|less|more|view|git\s+(?:status|log|show|diff|blame|branch|tag))",
    r"^curl\s+(?:-[sSO]|--silent|--output)",
    r"^wget\s+(?:-q|--quiet)",
    r"^nc\s+(?:-\w+\s+)*(?:-l|-p)",
    r"^(?:Get-|Test-|Measure-)",  # PowerShell 只读 cmdlet
]

_PS_CMDLET_PATTERN = re.compile(
    r'^\s*(Get-|Set-|New-|Remove-|Test-|Write-|Format-|Select-|Where-|Sort-|'
    r'Measure-|Join-|Split-|Compare-|Group-|ForEach-|Start-|Stop-|Import-|Export-|'
    r'ConvertTo-|ConvertFrom-|Out-)',
    re.IGNORECASE,
)


# =============================================================================
# 数据类型
# =============================================================================

class CommandType(Enum):
    READ = "read"
    WRITE = "write"
    UNKNOWN = "unknown"


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    code: int = 0
    interrupted: bool = False
    timed_out: bool = False
    background_task_id: Optional[str] = None
    output_file_path: Optional[str] = None
    output_file_size: Optional[int] = None
    error: Optional[str] = None

    def is_error(self) -> bool:
        return self.code != 0 or self.interrupted or self.timed_out or self.error is not None


# =============================================================================
# 辅助函数
# =============================================================================

def check_dangerous_command(command: str) -> Optional[str]:
    """检查危险命令，返回警告信息或 None

    注意：此函数使用 SecurityPolicy 进行检测。
    如果 SecurityPolicy 不可用，返回 None（由调用者处理降级）。
    """
    try:
        from app.core.security.policy import SecurityPolicy
        policy = SecurityPolicy()
        result = policy.check_command(command)
        if not result.get("allowed", True):
            return result.get("message", "危险命令被拦截")
    except Exception:
        pass
    return None


def classify_command(command: str) -> CommandType:
    """分类命令为只读或写入"""
    cmd = command.strip()

    for pattern in _WRITE_INDICATORS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return CommandType.WRITE

    for pattern in READ_ONLY_PATTERNS:
        if re.match(pattern, cmd, re.IGNORECASE):
            return CommandType.READ

    if re.match(r"^cd\s+", cmd):
        return CommandType.READ

    return CommandType.UNKNOWN


def truncate_output(output: str, max_bytes: int = MAX_OUTPUT_BYTES) -> tuple:
    """截断输出，返回 (截断后的输出, 是否被截断)"""
    if len(output.encode('utf-8')) <= max_bytes:
        return output, False

    truncated = output.encode('utf-8')[:max_bytes].decode('utf-8', errors='ignore')
    return truncated + f"\n... (truncated, exceeded {max_bytes} bytes)", True


def decode_output(data: bytes) -> str:
    """解码命令输出，自动检测编码"""
    if not data:
        return ""
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        try:
            return data.decode('gbk')
        except UnicodeDecodeError:
            return data.decode('cp936', errors='replace')


def format_duration(seconds: float) -> str:
    """格式化时长"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# =============================================================================
# Shell 工具类
# =============================================================================

class CelliumShell:
    """
    Shell 命令执行工具

    用法:
        tool = CelliumShell()

        # 同步模式
        result = tool.run("ls -la")
        result = tool.execute({"command": "Get-Process", "timeout": 30})

        # 异步模式
        result = await tool.execute_async({"command": "ls -la"})

    改进点：
        1. 智能Shell选择（PowerShell cmdlet自动路由）
        2. 危险命令检测与拦截
        3. 命令分类（只读/写入/未知）
        4. 工作目录跟踪（cd命令）
        5. 输出截断与编码自动检测
        6. 后台任务管理
        7. 流式输出回调
    """

    # 降级兜底黑名单（SecurityPolicy 不可用时使用）
    _FALLBACK_BLOCKLIST = [
        # ═══ 文件系统破坏 ═══
        "remove-item -recurse", "rm -rf /", "rmdir /s /q", "del /f /s /q",
        # ═══ 磁盘操作 ═══
        "format", "diskpart", "mkfs", "dd if=", "cipher /w",
        # ═══ 系统控制 ═══
        "shutdown", "stop-computer", "reboot", "restart-computer", "init 0", "init 6",
        # ═══ 启动/引导 ═══
        "bcdedit", "bootcfg",
        # ═══ 注册表 ═══
        "reg delete", "reg import",
        # ═══ 服务 ═══
        "sc config", "sc delete", "sc create", "new-service",
        # ═══ 防火墙/网络 ═══
        "netsh firewall", "netsh advfirewall", "iptables -f", "ufw disable",
        # ═══ 用户/权限 ═══
        "net user", "net localgroup", "takeown", "icacls", "chmod 777", "chown -r",
        # ═══ PowerShell 危险操作 ═══
        "powershell -enc", "powershell -e ", "invoke-expression", "iex ",
        # ═══ Windows 危险工具 ═══
        "certutil -urlcache", "bitsadmin /transfer", "mshta ", "rundll32 ", "regsvr32 ",
        # ═══ Fork 炸弹 ═══
        ":(){ :|:& };:", ":(){ :|:&};:",
        # ═══ 进程杀戮 ═══
        "kill -9 -1", "killall ", "taskkill /f",
        # ═══ 定时任务 ═══
        "crontab -e", "crontab -r", "schtasks /create", "schtasks /delete",
        # ═══ 环境注入 ═══
        "ld_preload=", "dyld_insert_libraries=",
    ]

    def __init__(
        self,
        initial_cwd: str = None,
        security_policy=None,
    ):
        self._platform = sys.platform
        self._executor = None
        self._max_workers = max(8, (os.cpu_count() or 4))
        self._background_tasks: Dict[str, Any] = {}
        self._cwd = initial_cwd if initial_cwd and os.path.isdir(initial_cwd) else os.getcwd()
        self.security = security_policy

        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()
            global DEFAULT_TIMEOUT_SECONDS, MAX_OUTPUT_BYTES, HARD_TIMEOUT_SECONDS
            DEFAULT_TIMEOUT_SECONDS = int(cfg.get("security.command_timeout", DEFAULT_TIMEOUT_SECONDS))
            MAX_OUTPUT_BYTES = int(cfg.get("security.max_output_bytes", MAX_OUTPUT_BYTES))
            HARD_TIMEOUT_SECONDS = int(cfg.get("security.shell_hard_timeout", HARD_TIMEOUT_SECONDS))
        except Exception:
            pass

        self._shell_cmd: List[str] = []
        self._shell_name: str = ""
        self._pwsh_path: Optional[str] = None
        self._init_platform_shell()

    def _init_platform_shell(self) -> None:
        """根据操作系统选择默认 Shell"""
        if self._platform == "win32":
            pwsh = self._find_pwsh()
            if pwsh:
                self._shell_cmd = [pwsh, "-NoProfile", "-NonInteractive", "-Command"]
                self._shell_name = "powershell"
                self._pwsh_path = pwsh
            else:
                self._shell_cmd = ["cmd.exe", "/c"]
                self._shell_name = "cmd"
                self._pwsh_path = None
            logger.info("[Shell] 平台=Windows | Shell=%s | 路径=%s",
                        os.path.basename(self._shell_cmd[0]), self._shell_cmd[0])
        elif self._platform == "darwin":
            self._shell_cmd = ["/bin/zsh", "-c"] if os.path.exists("/bin/zsh") else ["/bin/bash", "-c"]
            self._shell_name = "zsh" if "/zsh" in self._shell_cmd[0] else "bash"
            logger.info("[Shell] 平台=macOS | Shell=%s", self._shell_name)
        else:
            self._shell_cmd = ["/bin/bash", "-c"]
            self._shell_name = "bash"
            logger.info("[Shell] 平台=%s | Shell=bash", self._platform)

    @staticmethod
    def _find_pwsh() -> Optional[str]:
        """查找 PowerShell Core (pwsh) 或 Windows PowerShell 的路径"""
        return shutil.which("pwsh") or shutil.which("powershell")

    def _get_executor(self) -> ThreadPoolExecutor:
        """延迟初始化线程池"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
            logger.info(f"[Shell] 线程池初始化 | max_workers={self._max_workers}")
        return self._executor

    # ================================================================
    #  安全检查
    # ================================================================

    def _check_security(self, cmd: str) -> Dict[str, Any]:
        """安全策略检查"""
        danger_warning = check_dangerous_command(cmd)
        if danger_warning:
            return {"success": False, "allowed": False, "reason": danger_warning}

        if self.security:
            try:
                from app.core.security.policy import RiskLevel
                result = self.security.check_command(cmd)
                if not result.get("allowed", False):
                    return {"success": False, "allowed": False, "reason": result.get("message", "被拦截")}
                risk = RiskLevel(result.get("risk_level", "medium"))
                return {"success": True, "allowed": True, "timeout": self.security.get_timeout(risk)}
            except Exception:
                pass

        cmd_lower = cmd.lower()
        for pattern in self._FALLBACK_BLOCKLIST:
            if pattern in cmd_lower:
                return {"success": False, "allowed": False, "reason": f"危险命令模式: {pattern}"}

        return {"success": True, "allowed": True, "timeout": DEFAULT_TIMEOUT_SECONDS}

    # ================================================================
    #  兼容旧接口
    # ================================================================

    def run(self, cmd: str, timeout: int = None) -> Dict[str, Any]:
        """
        执行系统命令（兼容旧接口）

        Returns:
            {
                "status": "success",     # 成功
                "data": "...",           # stdout 内容
                "elapsed_ms": 15,        # 实际耗时
            }
            或
            {
                "error": "...",          # 错误信息
                "elapsed_ms": 30000,     # 耗时
            }
        """
        result = self.execute({"command": cmd, "timeout": timeout})

        if result.get("error"):
            return {
                "error": result["error"],
                "elapsed_ms": result.get("elapsed_ms", 0),
            }
        else:
            return {
                "status": "success",
                "data": result.get("output", ""),
                "elapsed_ms": result.get("elapsed_ms", 0),
            }

    # ================================================================
    #  核心执行引擎
    # ================================================================

    def execute(
        self,
        command: Union[str, Dict[str, Any]] = "",
    ) -> Dict[str, Any]:
        """
        同步执行命令

        支持两种调用方式：
        1. dict 模式: {"command": "ls -la", "timeout": 30}
        2. str 模式: "ls -la"
        """
        if isinstance(command, dict):
            cmd_str = command.get("command", "")
            logger.info("[Shell] execute(dict) | command=%s", cmd_str[:200] if cmd_str else "(空)")
            return self._run_command(
                cmd_str,
                timeout=command.get("timeout", DEFAULT_TIMEOUT_SECONDS),
                run_in_background=command.get("run_in_background", False),
                cwd=command.get("cwd", self._cwd),
            )

        if isinstance(command, str) and command.strip():
            logger.info("[Shell] execute(str) | command=%s", command[:200])
            return self._run_command(command, cwd=self._cwd)

        return {"success": False, "error": "未提供有效的 command 参数"}

    async def execute_async(
        self,
        command: Union[str, Dict[str, Any]] = "",
    ) -> Dict[str, Any]:
        """
        异步执行命令

        支持两种调用方式：
        1. dict 模式: {"command": "ls -la", "timeout": 30}
        2. str 模式: "ls -la"

        额外参数:
            - on_progress: 流式输出回调 fn(stdout: str, stderr: str)
        """
        if isinstance(command, dict):
            cmd_str = command.get("command", "")
            logger.info("[Shell] execute_async(dict) | command=%s", cmd_str[:200] if cmd_str else "(空)")
            return await self._run_command_async(
                cmd_str,
                timeout=command.get("timeout", DEFAULT_TIMEOUT_SECONDS),
                run_in_background=command.get("run_in_background", False),
                cwd=command.get("cwd", self._cwd),
                on_progress=command.get("on_progress"),
            )

        if isinstance(command, str) and command.strip():
            logger.info("[Shell] execute_async(str) | command=%s", command[:200])
            return await self._run_command_async(command, cwd=self._cwd)

        return {"success": False, "error": "未提供有效的 command 参数"}

    def _resolve_shell(self, cmd: str) -> Tuple[str, List[str]]:
        """
        根据命令内容自动选择 Shell

        Windows:
            - 检测到 PowerShell cmdlet → 用 PowerShell
            - 其他 → 用 cmd.exe
        Linux/Mac:
            - 用 bash
        """
        if self._platform != "win32":
            return ("/bin/bash", ["-c"])

        if _PS_CMDLET_PATTERN.search(cmd) or any(
            kw in cmd for kw in ["$null", "$_", "| Select", "| Where", "| ForEach",
                                "-ErrorAction", "-WhatIf", "| ConvertTo", "| ConvertFrom"]
        ):
            if self._pwsh_path:
                return (self._pwsh_path, ["-NoProfile", "-NonInteractive", "-Command"])
            logger.warning("[Shell] 检测到 PowerShell 语法但未找到 pwsh，回退到 cmd")

        return ("cmd.exe", ["/c"])

    @property
    def cwd(self) -> str:
        """获取当前工作目录"""
        return self._cwd

    def _update_cwd(self, cmd: str, exit_code: int, stdout: str) -> None:
        """更新当前目录（跟踪 cd 命令）"""
        if exit_code != 0:
            return

        cmd = cmd.strip()
        if not cmd:
            return

        if self._platform == "win32":
            if cmd.lower().startswith("cd ") or cmd.lower().startswith("chdir "):
                parts = cmd.split(None, 1)
                if len(parts) > 1:
                    new_path = parts[1].strip()
                    if new_path and new_path != ".":
                        if os.path.isdir(new_path):
                            self._cwd = os.path.abspath(new_path)
        else:
            if cmd.startswith("cd ") or cmd.startswith("cd\t"):
                parts = cmd.split(None, 1)
                if len(parts) > 1:
                    new_path = parts[1].strip()
                    if new_path and new_path != ".":
                        if os.path.isdir(new_path):
                            self._cwd = os.path.abspath(new_path)

    def _run_command(
        self,
        cmd: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        run_in_background: bool = False,
        cwd: str = None,
    ) -> Dict[str, Any]:
        """同步执行命令的主路径"""
        if not cmd or not cmd.strip():
            return {"success": False, "error": "命令为空"}

        sec = self._check_security(cmd)
        if not sec["allowed"]:
            return {"success": False, "error": f"安全拦截: {sec['reason']}"}

        cmd_type = classify_command(cmd)
        effective_timeout = min(timeout or sec.get("timeout", DEFAULT_TIMEOUT_SECONDS), HARD_TIMEOUT_SECONDS)

        if run_in_background:
            return self._run_background(cmd, effective_timeout, cwd)

        return self._execute_sync(cmd, effective_timeout, cwd, cmd_type)

    async def _run_command_async(
        self,
        cmd: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        run_in_background: bool = False,
        cwd: str = None,
        on_progress: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """异步执行命令的主路径"""
        if not cmd or not cmd.strip():
            return {"success": False, "error": "命令为空"}

        sec = self._check_security(cmd)
        if not sec["allowed"]:
            return {"success": False, "error": f"安全拦截: {sec['reason']}"}

        cmd_type = classify_command(cmd)
        effective_timeout = min(timeout or sec.get("timeout", DEFAULT_TIMEOUT_SECONDS), HARD_TIMEOUT_SECONDS)

        if run_in_background:
            return self._run_background(cmd, effective_timeout, cwd)

        return await self._execute_async(cmd, effective_timeout, cwd, cmd_type, on_progress)

    def _execute_sync(
        self,
        cmd: str,
        timeout: int,
        cwd: str,
        cmd_type: CommandType,
    ) -> Dict[str, Any]:
        """同步执行命令"""
        shell_cmd, shell_args = self._resolve_shell(cmd)

        if self._platform == "win32" and "powershell" not in shell_cmd.lower():
            full_cmd = [shell_cmd] + shell_args + [cmd]
        else:
            full_cmd = [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]

        work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
        env = os.environ.copy()

        start_time = time.time()

        try:
            process = subprocess.Popen(
                full_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=work_dir,
                env=env,
                shell=False,
            )

            try:
                stdout, stderr = process.communicate(timeout=timeout)
                elapsed = time.time() - start_time

                stdout_str = decode_output(stdout).strip()
                stderr_str = decode_output(stderr).strip()

                output, truncated = truncate_output(stdout_str, MAX_OUTPUT_BYTES)

                result = {
                    "output": output,
                    "exit_code": process.returncode,
                    "elapsed_ms": int(elapsed * 1000),
                    "command_type": cmd_type.value,
                }

                if stderr_str:
                    result["stderr"] = stderr_str
                if truncated:
                    result["truncated"] = True
                if process.returncode != 0:
                    result["error"] = stderr_str or f"Exit code: {process.returncode}"

                self._update_cwd(cmd, process.returncode, stdout_str)
                return result

            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return {
                    "error": f"命令超时（{timeout}秒）",
                    "timed_out": True,
                    "timeout_seconds": timeout,
                }

        except FileNotFoundError:
            return {"success": False, "error": f"命令未找到: {shell_cmd}"}
        except PermissionError as e:
            return {"success": False, "error": f"权限拒绝: {e}"}
        except Exception as e:
            return {"success": False, "error": f"执行失败 ({type(e).__name__}): {e}"}

    async def _execute_async(
        self,
        cmd: str,
        timeout: int,
        cwd: str,
        cmd_type: CommandType,
        on_progress: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """异步执行命令（使用 asyncio subprocess）"""
        shell_cmd, shell_args = self._resolve_shell(cmd)

        if self._platform == "win32" and "powershell" not in shell_cmd.lower():
            full_cmd = [shell_cmd] + shell_args + [cmd]
        else:
            full_cmd = [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]

        work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
        env = os.environ.copy()

        start_time = time.time()
        stdout_lines = []
        stderr_lines = []

        try:
            process = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=env,
            )

            async def read_stream(stream: asyncio.StreamReader, is_stdout: bool):
                """流式读取输出"""
                try:
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        line_str = decode_output(line)
                        if is_stdout:
                            stdout_lines.append(line_str)
                        else:
                            stderr_lines.append(line_str)
                        if on_progress:
                            try:
                                on_progress(line_str if is_stdout else None, line_str if not is_stdout else None)
                            except Exception as cb_e:
                                logger.debug("[Shell] 进度回调失败: %s", cb_e)
                except Exception as e:
                    logger.debug("[Shell] 读取流失败: %s", e)

            try:
                if timeout > 0:
                    await asyncio.wait_for(
                        asyncio.gather(
                            read_stream(process.stdout, True),
                            read_stream(process.stderr, False),
                        ),
                        timeout=timeout,
                    )
                else:
                    await asyncio.gather(
                        read_stream(process.stdout, True),
                        read_stream(process.stderr, False),
                    )

                await process.wait()
                elapsed = time.time() - start_time

                stdout_str = "".join(stdout_lines).strip()
                stderr_str = "".join(stderr_lines).strip()

                output, truncated = truncate_output(stdout_str, MAX_OUTPUT_BYTES)

                result = {
                    "output": output,
                    "exit_code": process.returncode,
                    "elapsed_ms": int(elapsed * 1000),
                    "command_type": cmd_type.value,
                }

                if stderr_str:
                    result["stderr"] = stderr_str
                if truncated:
                    result["truncated"] = True
                if process.returncode != 0:
                    result["error"] = stderr_str or f"Exit code: {process.returncode}"

                return result

            except asyncio.TimeoutError:
                if self._platform != "win32":
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    except Exception as kill_e:
                        logger.debug("[Shell] 终止进程组失败: %s", kill_e)
                else:
                    process.terminate()

                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except Exception:
                    logger.debug("[Shell] 等待进程退出超时，强制终止")
                    process.kill()
                    await process.wait()

                return {
                    "error": f"命令超时（{timeout}秒）",
                    "timed_out": True,
                    "timeout_seconds": timeout,
                }

        except FileNotFoundError:
            return {"success": False, "error": f"命令未找到: {shell_cmd}"}
        except PermissionError as e:
            return {"success": False, "error": f"权限拒绝: {e}"}
        except Exception as e:
            return {"success": False, "error": f"执行失败 ({type(e).__name__}): {e}"}

    def _run_background(
        self,
        cmd: str,
        timeout: int,
        cwd: str,
    ) -> Dict[str, Any]:
        """后台执行命令"""
        import uuid
        task_id = f"bg_{uuid.uuid4().hex[:8]}"
        output_file = os.path.join(tempfile.gettempdir(), f"{task_id}.output")

        def run_in_thread():
            shell_cmd, shell_args = self._resolve_shell(cmd)

            if self._platform == "win32" and "powershell" not in shell_cmd.lower():
                full_cmd = [shell_cmd] + shell_args + [cmd]
            else:
                full_cmd = [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]

            work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
            env = os.environ.copy()

            try:
                process = subprocess.Popen(
                    full_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=work_dir,
                    env=env,
                    shell=False,
                )

                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                    stdout_str = decode_output(stdout)
                    stderr_str = decode_output(stderr)

                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(stdout_str)
                        if stderr_str:
                            f.write("\n--- STDERR ---\n")
                            f.write(stderr_str)

                    return {
                        "task_id": task_id,
                        "output_file": output_file,
                        "exit_code": process.returncode,
                    }
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(f"Command timed out after {timeout} seconds")
                    return {
                        "task_id": task_id,
                        "output_file": output_file,
                        "error": "timeout",
                    }
            except Exception as e:
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(f"Error: {str(e)}")
                return {
                    "task_id": task_id,
                    "output_file": output_file,
                    "error": str(e),
                }

        future = self._get_executor().submit(run_in_thread)
        self._background_tasks[task_id] = future

        return {
            "status": "background_started",
            "task_id": task_id,
            "output_file": output_file,
            "message": "命令已在后台执行",
        }

    def kill_background_task(self, task_id: str) -> bool:
        """终止后台任务"""
        if task_id in self._background_tasks:
            task = self._background_tasks[task_id]
            if asyncio.iscoroutine(task):
                task.cancel()
            else:
                task.cancel()
            del self._background_tasks[task_id]
            return True
        return False

    def list_background_tasks(self) -> list:
        """列出所有后台任务"""
        return list(self._background_tasks.keys())

    def get_background_result(self, task_id: str, timeout: float = 0) -> Optional[Dict]:
        """获取后台任务结果（同步模式）"""
        if task_id not in self._background_tasks:
            return None

        task = self._background_tasks[task_id]

        if asyncio.iscoroutine(task):
            return None

        if timeout > 0:
            try:
                return task.result(timeout=timeout)
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            if task.done():
                try:
                    return task.result()
                except Exception as e:
                    return {"success": False, "error": str(e)}
        return None

    async def get_background_result_async(self, task_id: str) -> Optional[Dict]:
        """获取后台任务结果（异步模式）"""
        if task_id not in self._background_tasks:
            return None

        task = self._background_tasks[task_id]

        if not asyncio.iscoroutine(task):
            return None

        try:
            return await task
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ================================================================
    #  生命周期管理
    # ================================================================

    def terminate(self):
        """清理资源"""
        for task_id in list(self._background_tasks.keys()):
            self.kill_background_task(task_id)
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None

    def close(self):
        """关闭"""
        self.terminate()


# =============================================================================
# 兼容性别名
# =============================================================================

ShellTool = CelliumShell
BashTool = CelliumShell
PowerShellTool = CelliumShell
