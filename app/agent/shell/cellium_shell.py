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
import threading
import tempfile
import logging
import subprocess
import shutil
from typing import Dict, Any, Optional, Callable, Union, List, Tuple
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

logger = logging.getLogger(__name__)


# =============================================================================
# 常量定义
# =============================================================================

DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
HARD_TIMEOUT_SECONDS = 300
PREVIEW_SIZE_BYTES = 500
SESSION_MAX = 4
SESSION_BUFFER_LINES = 500
SESSION_REAP_IDLE = 120

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


class ShellSession:
    """持久命令会话：保持进程存活，支持多次 send 输入 / 读取增量输出"""

    def __init__(self, session_id: str, argv: List[str], cwd: str, env: Dict[str, str],
             window: bool = False, pty: bool = False):
        self.session_id = session_id
        self.argv = list(argv)
        self._window = window
        self._pty = pty
        if sys.platform != "win32" and pty:
            import pty as _pty
            import termios
            import tty
            self._pty_master, slave = _pty.openpty()
            try:
                settings = termios.tcgetattr(slave)
            except Exception:
                settings = None
            if settings is not None:
                settings[3] = settings[3] & ~termios.ECHO 
                try:
                    termios.tcsetattr(slave, termios.TCSANOW, settings)
                except Exception:
                    pass
            self._proc = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                close_fds=True,
            )
            os.close(slave)
            self._lines = []
            self._consumed = 0
            self.exit_time: Optional[float] = None
            self._closed = False
            self._reader = threading.Thread(target=self._drain_pty, daemon=True, name=f"pty-{session_id}")
            self._reader.start()
            return
        if sys.platform == "win32" and window:
            # 独立控制台窗口：进程拥有真实 TTY（ssh 密码/全屏编辑器可用）
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = getattr(subprocess, "SW_SHOWNORMAL", 1)
            self._proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                startupinfo=si,
            )
            self._lines = []
            self._consumed = 0
            self.exit_time: Optional[float] = None
            self._closed = False
            return
        start_info = None
        creation_flags = 0
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            start_info = si
            # DETACHED_PROCESS：进程独立于任何控制台，避免 Windows 为其伴生 conhost.exe
            creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0)
            creation_flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            text=True,
            bufsize=1,
            creationflags=creation_flags,
            startupinfo=start_info,
        )
        self._lock = threading.Lock()
        self._lines: List[str] = []
        self._consumed = 0
        self.exit_time: Optional[float] = None
        self._closed = False
        self._reader = threading.Thread(target=self._drain, daemon=True, name=f"ses-{session_id}")
        self._reader.start()

    def _drain(self):
        try:
            for line in iter(self._proc.stdout.readline, ""):
                with self._lock:
                    self._lines.append(line)
                    excess = len(self._lines) - SESSION_BUFFER_LINES
                    if excess > 0:
                        del self._lines[:excess]
                        self._consumed = max(0, self._consumed - excess)
                        self._dropped = getattr(self, "_dropped", 0) + excess
        except Exception:
            pass
        finally:
            if self.exit_time is None:
                self.exit_time = time.time()

    def _drain_pty(self):
        """读取 PTY master 输出（按块读，避免行缓冲堵住）"""
        try:
            while True:
                chunk = os.read(self._pty_master, 4096)
                if not chunk:
                    break
                with self._lock:
                    self._lines.append(chunk)
                    total = sum(len(l) for l in self._lines)
                    while total > 65536:
                        removed = self._lines.pop(0)
                        total -= len(removed)
                        self._consumed = max(0, self._consumed - len(removed))
        except OSError:
            pass
        except Exception:
            pass
        finally:
            if self.exit_time is None:
                self.exit_time = time.time()

    def send(self, text: str) -> bool:
        if self._closed:
            return False
        if self._window:
            return False
        if self._pty:
            try:
                payload = text if text.endswith("\n") else text + "\n"
                os.write(self._pty_master, payload.encode("utf-8", errors="replace"))
                return True
            except Exception:
                return False
        try:
            payload = text if text.endswith("\n") else text + "\n"
            if "\n" in text.rstrip("\n") and not payload.endswith("\n\n"):
                payload = payload.rstrip("\n") + "\n\n"
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
            return True
        except Exception:
            return False

    def output(self, new_only: bool = False) -> str:
        if self._window:
            return ""
        if self._pty:
            # PTY 输出为原始字节块，按"自上次读取以来"返回并消费
            if new_only:
                with self._lock:
                    data = "".join(self._lines)
                    self._lines.clear()
                return data
            with self._lock:
                return "".join(self._lines)
        with self._lock:
            start = self._consumed if new_only else 0
            data = "".join(self._lines[start:])
            if new_only:
                self._consumed = len(self._lines)
        dropped = getattr(self, "_dropped", 0)
        if dropped:
            data = f"\n…(会话输出超过缓冲上限，已丢弃最旧的 {dropped} 行。如需完整输出，请改用 background 任务落盘查看)\n" + data
        self._note_exit()
        return data

    def wait_for_output(self, timeout: float = 5.0) -> str:
        """阻塞等待新输出，最多 timeout 秒；进程退出时立即返回剩余输出"""
        if self._window:
            return ""
        if self._pty:
            deadline = time.time() + timeout
            while time.time() < deadline:
                with self._lock:
                    has_new = bool(self._lines)
                if has_new or not self.is_alive():
                    break
                time.sleep(0.05)
            return self.output(new_only=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                has_new = len(self._lines) > self._consumed
            if has_new or not self.is_alive():
                break
            time.sleep(0.05)
        return self.output(new_only=True)

    def _note_exit(self):
        if self.exit_time is None and self._proc.poll() is not None:
            self.exit_time = time.time()

    def is_alive(self) -> bool:
        self._note_exit()
        return not self._closed and self._proc.poll() is None

    def exit_code(self) -> Optional[int]:
        self._note_exit()
        if self._closed:
            return None
        return self._proc.poll()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        if getattr(self, "_pty", False):
            try:
                os.close(self._pty_master)
            except Exception:
                pass
        for stream in (getattr(self._proc, "stdin", None),
                       getattr(self._proc, "stdout", None),
                       getattr(self._proc, "stderr", None)):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass


# =============================================================================
# 辅助函数
# =============================================================================

def check_dangerous_command(command: str) -> Optional[str]:
    """检查危险命令，返回警告信息或 None"""
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


_INTERACTIVE_PROGRAMS = {
    "ssh", "sftp", "telnet", "ftp", "vim", "vi", "nvim", "nano",
    "less", "more", "top", "htop", "mosh", "tsh",
    "mysql", "psql", "redis-cli", "sqlite3",
}

_SSH_OPT_WITH_VALUE = {
    "-b", "-c", "-D", "-E", "-e", "-F", "-i", "-J", "-l", "-L",
    "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w", "-g",
}

_SSH_QUERY_ONLY = {"-V", "-G", "-Q"}
_SSH_CTRL_CMDS = {"check", "exit", "stop", "forward", "cancel"}


def _ssh_interactive(argv: List[str]) -> bool:
    """ssh: 仅有目标主机（无远程命令）为交互式会话。
    -V/-G/-Q 查询选项、-O 控制指令只打印信息/执行查询即退出，视为非交互。
    """
    args = argv[1:]
    positionals = []
    query_like = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            positionals.extend(args[i + 1:])
            break
        if a.startswith("-") and a != "-":
            if "=" in a:
                opt, val = a.split("=", 1)
                if opt in _SSH_QUERY_ONLY:
                    query_like = True
                elif opt == "-O" and val in _SSH_CTRL_CMDS:
                    query_like = True
            elif a in _SSH_QUERY_ONLY:
                query_like = True
            elif a == "-O" and i + 1 < len(args) and args[i + 1] in _SSH_CTRL_CMDS:
                query_like = True
                i += 1
            elif a in _SSH_OPT_WITH_VALUE and i + 1 < len(args):
                i += 1
        else:
            positionals.append(a)
        i += 1
    if query_like:
        return False
    return len(positionals) <= 1


def _is_interactive_argv(argv) -> bool:
    if not argv or not isinstance(argv, (list, tuple)):
        return False
    base = os.path.basename(str(argv[0])).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base == "ssh":
        return _ssh_interactive(list(argv))
    if base in _INTERACTIVE_PROGRAMS:
        return True
    if base in ("bash", "sh", "zsh"):
        return "-c" not in argv and not any(a for a in argv[1:] if a and not a.startswith("-"))
    if base in ("python", "python3", "ipython", "node"):
        return not any(a for a in argv[1:] if a and not a.startswith("-"))
    return False


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] in ("'", '"') and token[-1] == token[0]:
        return token[1:-1]
    return token


_TERMINAL_PROGRAMS = {
    "sftp", "telnet", "ftp", "vim", "vi", "nvim", "nano",
    "top", "htop", "mosh",
}


def _needs_terminal(argv) -> bool:
    if not argv or not isinstance(argv, (list, tuple)):
        return False
    base = os.path.basename(str(argv[0])).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base == "ssh":
        return _ssh_interactive(list(argv))
    return base in _TERMINAL_PROGRAMS


def _split_cmd_argv(cmd: str) -> List[str]:
    """按 shell 规则拆分命令 token，并去掉参数上的包裹引号"""
    try:
        import shlex
        if os.name == "nt":
            parts = shlex.split(cmd, posix=False)
        else:
            parts = shlex.split(cmd, posix=True)
    except Exception:
        parts = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', cmd)
    return [_strip_quotes(p) for p in parts]


def _is_interactive_cmd(cmd: str) -> bool:
    if not cmd or not cmd.strip():
        return False
    return _is_interactive_argv(_split_cmd_argv(cmd))


def _resolve_interactive_argv(cmd: str) -> Optional[List[str]]:
    if not cmd or not cmd.strip():
        return None
    if any(ch in cmd for ch in "$`;|&><()"):
        return None
    parts = _split_cmd_argv(cmd)
    if not parts:
        return None
    if not os.path.exists(parts[0]) and shutil.which(parts[0]) is None:
        return None
    return parts


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
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _patch_python_c_for_stdin(argv: List[str]) -> Tuple[List[str], Optional[bytes]]:
    if len(argv) < 3:
        return argv, None

    exe = os.path.basename(argv[0]).lower()
    if exe not in ("python", "python.exe", "python3", "python3.exe"):
        return argv, None

    for i, arg in enumerate(argv):
        if arg == "-c" and i + 1 < len(argv):
            script = argv[i + 1]
            patched = argv[:i] + ["-"] + argv[i + 2:]
            stdin_data = script.encode('utf-8')
            return patched, stdin_data

    return argv, None


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
        "diskpart", "mkfs", "dd if=", "cipher /w",
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
        self._sessions: Dict[str, ShellSession] = {}
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
        # 会话级环境变量：跨命令持久化（命令内 $env:NAME=value / set NAME=value
        # 会更新此状态，后续命令可读取），初始为当前进程环境快照
        self._session_env: Dict[str, str] = os.environ.copy()
        self._init_platform_shell()

        # 全局活动子进程注册表（thread_id -> [Popen, ...]）：停止时按线程 kill 子进程
        self._active_processes: Dict[int, List] = {}
        self._processes_lock = threading.Lock()

    def _register_process(self, process) -> None:
        """注册当前线程的活动子进程"""
        tid = threading.get_ident()
        with self._processes_lock:
            self._active_processes.setdefault(tid, []).append(process)

    def _unregister_process(self, process) -> None:
        """取消注册子进程"""
        tid = threading.get_ident()
        with self._processes_lock:
            procs = self._active_processes.get(tid)
            if procs:
                try:
                    procs.remove(process)
                except ValueError:
                    pass
                if not procs:
                    self._active_processes.pop(tid, None)

    def kill_thread_processes(self, thread_id: int = None) -> int:
        """kill 指定线程正在运行的子进程，返回杀掉的进程数"""
        killed = 0
        tid = thread_id or threading.get_ident()
        with self._processes_lock:
            procs = self._active_processes.pop(tid, [])
        for proc in procs:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                    killed += 1
            except Exception:
                pass
        return killed

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
                
                if "modified_command" in result:
                    return {
                        "success": True,
                        "allowed": True,
                        "modified_command": result["modified_command"],
                        "message": result.get("message", ""),
                    }
                
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
        if isinstance(command, dict):
            cmd_value = command.get("cmd", command.get("command", ""))
            if isinstance(cmd_value, list):
                logger.info("[Shell] execute(array) | cmd=%s", cmd_value[:5])
                return self._run_argv(
                    cmd_value,
                    timeout=command.get("timeout", DEFAULT_TIMEOUT_SECONDS),
                    run_in_background=command.get("run_in_background", False),
                    cwd=command.get("cwd", self._cwd),
                )
            cmd_str = cmd_value
            logger.info("[Shell] execute(string) | cmd=%s", cmd_str[:200] if cmd_str else "(空)")
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
        if isinstance(command, dict):
            cmd_value = command.get("cmd", command.get("command", ""))
            if isinstance(cmd_value, list):
                logger.info("[Shell] execute_async(array) | cmd=%s", cmd_value[:5])
                return self._run_argv(
                    cmd_value,
                    timeout=command.get("timeout", DEFAULT_TIMEOUT_SECONDS),
                    run_in_background=command.get("run_in_background", False),
                    cwd=command.get("cwd", self._cwd),
                )
            cmd_str = cmd_value
            logger.info("[Shell] execute_async(string) | cmd=%s", cmd_str[:200] if cmd_str else "(空)")
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
            - 有 PowerShell 可用时，默认使用 PowerShell（与 description 声明一致）
            - 检测到 cmd.exe 专属语法（如 &、&&、||、>nul）时用 cmd.exe
            - 无 PowerShell 时回退到 cmd.exe
        Linux/Mac:
            - 用 bash
        """
        if self._platform != "win32":
            return ("/bin/bash", ["-c"])

        cmd_exe_indicators = [
            r">nul\b", r"2>nul\b", r"1>nul\b",
            r"%\w+%", r"\bcmd(?:\.exe)?\s*/\s*c\b",
        ]
        for indicator in cmd_exe_indicators:
            if re.search(indicator, cmd, re.IGNORECASE):
                return ("cmd.exe", ["/c"])

        if self._pwsh_path:
            return (self._pwsh_path, ["-NoProfile", "-NonInteractive", "-Command"])

        return ("cmd.exe", ["/c"])

    def resolve_session_argv(self, cmd: str) -> List[str]:
        """将命令解析为会话可启动的 argv。

        session 是逐条 send 输入的 REPL 语义，命令本身应为"程序+参数"而非
        shell 脚本，故直接解析为程序 argv（首程序存在时），避免 shell 包装层
        （pwsh/bash）不等子进程退出导致会话假死。仅首程序不存在时回退包装。
        """
        direct = _resolve_interactive_argv(cmd)
        if direct:
            return direct
        parts = _split_cmd_argv(cmd)
        if not parts:
            shell_cmd, shell_args = self._resolve_shell(cmd)
            return [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]
        first = parts[0]
        first_lower = os.path.basename(first).lower()
        # Windows shell 内建命令 / .bat .cmd：交由 shell 包装执行（dir/md/type/echo 等）
        if self._platform == "win32" and (
            first_lower.endswith((".bat", ".cmd"))
            or first_lower in (
                "dir", "cd", "echo", "type", "copy", "del", "ren", "move",
                "md", "rd", "cls", "date", "time", "ver", "set", "path",
                "title", "color", "prompt", "vol", "label", "more", "find",
            )
        ):
            shell_cmd, shell_args = self._resolve_shell(cmd)
            return [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]
        if os.path.exists(first) or shutil.which(first) is not None:
            return parts
        shell_cmd, shell_args = self._resolve_shell(cmd)
        return [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]

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

    def _track_env_assignments(self, cmd: str) -> None:
        """从命令中提取环境变量赋值，更新会话级环境。

        支持：
          - PowerShell: $env:NAME = "value" / $env:NAME = 'value'
          - cmd: set NAME=value
        仅识别"赋值"语句，读取（$env:NAME 无等号）不受影响。
        """
        if not cmd:
            return
        try:
            # PowerShell: $env:NAME = value（值可为引号包裹，引号内允许 ; 等字符）
            for m in re.finditer(r"\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)", cmd):
                name = m.group(1)
                val = m.group(2).strip()
                if val and val[0] in ('"', "'"):
                    quote = val[0]
                    end = val.find(quote, 1)
                    if end != -1:
                        val = val[1:end]
                else:
                    val = re.split(r"\s*[;&|]\s*", val, maxsplit=1)[0].strip()
                if name and val:
                    self._session_env[name] = val

            # cmd: set NAME=value
            for m in re.finditer(
                r"(?:^|[;&|])\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^&\r\n]*)",
                cmd,
                re.IGNORECASE,
            ):
                name = m.group(1)
                val = m.group(2).strip()
                if name and val:
                    self._session_env[name] = val
        except Exception as e:
            logger.debug("[Shell] 环境变量跟踪失败: %s", e)

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

        effective_cmd = sec.get("modified_command", cmd)
        security_message = sec.get("message", "")

        # 提取命令中的环境变量赋值并持久化到会话环境
        self._track_env_assignments(effective_cmd)

        cmd_type = classify_command(effective_cmd)
        effective_timeout = timeout or sec.get("timeout", DEFAULT_TIMEOUT_SECONDS)

        if self._platform == "win32" and not run_in_background and _is_interactive_cmd(effective_cmd):
            direct = _resolve_interactive_argv(effective_cmd)
            if direct:
                result = self._execute_interactive(direct, cwd)
            else:
                shell_cmd, shell_args = self._resolve_shell(effective_cmd)
                full_cmd = [shell_cmd] + shell_args + [effective_cmd] if shell_args else [shell_cmd, effective_cmd]
                result = self._execute_interactive(full_cmd, cwd)
        elif run_in_background:
            result = self._run_background(effective_cmd, effective_timeout, cwd)
        else:
            result = self._execute_sync(effective_cmd, effective_timeout, cwd, cmd_type)

        if security_message and result.get("success"):
            result["security_note"] = security_message

        return result

    def _run_argv(
        self,
        argv: List[str],
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        run_in_background: bool = False,
        cwd: str = None,
    ) -> Dict[str, Any]:
        if not argv:
            return {"success": False, "error": "argv 为空"}

        cmd_str = " ".join(argv)
        sec = self._check_security(cmd_str)
        if not sec["allowed"]:
            return {"success": False, "error": f"安全拦截: {sec['reason']}"}

        effective_timeout = timeout or sec.get("timeout", DEFAULT_TIMEOUT_SECONDS)

        if self._platform == "win32" and not run_in_background and _is_interactive_argv(argv):
            return self._execute_interactive(argv, cwd)

        if run_in_background:
            return self._run_background_argv(argv, effective_timeout, cwd)

        return self._execute_argv_sync(argv, effective_timeout, cwd)

    def _execute_interactive(self, argv: List[str], cwd: str = None) -> Dict[str, Any]:
        """在独立控制台窗口中运行交互式命令，避免其 TTY/ANSI 输出破坏 TUI 终端"""
        work_dir = cwd if cwd and os.path.isdir(cwd) else self._cwd
        env = self._session_env.copy()

        popen_kwargs = dict(
            cwd=work_dir,
            env=env,
            shell=False,
        )
        if self._platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = getattr(subprocess, "SW_SHOWNORMAL", 1)
            popen_kwargs["startupinfo"] = si
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

        start_time = time.time()
        try:
            process = subprocess.Popen(argv, **popen_kwargs)
            self._register_process(process)
            try:
                process.wait()
            except KeyboardInterrupt:
                try:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
                except Exception:
                    pass
                raise
            finally:
                self._unregister_process(process)
            elapsed = int((time.time() - start_time) * 1000)
            code = process.returncode
            return {
                "success": code == 0,
                "output": f"\n[交互式命令在独立控制台窗口中运行]\n退出码: {code}",
                "exit_code": code,
                "elapsed_ms": elapsed,
                "interactive": True,
            }
        except FileNotFoundError:
            return {"success": False, "error": f"命令未找到: {argv[0]}"}
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return {"success": False, "error": f"执行失败 ({type(e).__name__}): {e}"}

    def _execute_argv_sync(
        self,
        argv: List[str],
        timeout: int,
        cwd: str,
    ) -> Dict[str, Any]:
        work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
        env = self._session_env.copy()
        start_time = time.time()

        popen_kwargs = dict(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work_dir,
            env=env,
            shell=False,
        )

        try:
            process = subprocess.Popen(argv, **popen_kwargs)
            self._register_process(process)

            try:
                stdout, stderr = process.communicate(timeout=timeout)
                elapsed = time.time() - start_time

                stdout_str = decode_output(stdout).strip()
                stderr_str = decode_output(stderr).strip()
                output, truncated = truncate_output(stdout_str, MAX_OUTPUT_BYTES)

                result = {
                    "success": True,
                    "output": output,
                    "exit_code": process.returncode,
                    "elapsed_ms": int(elapsed * 1000),
                }

                if stderr_str:
                    result["stderr"] = stderr_str
                if truncated:
                    result["truncated"] = True
                if process.returncode != 0:
                    result["error"] = stderr_str or f"Exit code: {process.returncode}"

                self._update_cwd(" ".join(argv), process.returncode, stdout_str)
                return result

            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return {
                    "error": f"命令超时（{timeout}秒）",
                    "timed_out": True,
                    "timeout_seconds": timeout,
                }
            except KeyboardInterrupt:
                # 用户停止：kill 子进程并向上传播中断（交由上层标记停止）
                try:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
                except Exception:
                    pass
                raise
            finally:
                self._unregister_process(process)

        except FileNotFoundError:
            return {"success": False, "error": f"命令未找到: {argv[0]}"}
        except PermissionError as e:
            return {"success": False, "error": f"权限拒绝: {e}"}
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return {"success": False, "error": f"执行失败 ({type(e).__name__}): {e}"}

    def _run_background_argv(
        self,
        argv: List[str],
        timeout: int,
        cwd: str,
    ) -> Dict[str, Any]:
        import uuid
        task_id = f"bg_{uuid.uuid4().hex[:8]}"
        output_file = os.path.join(tempfile.gettempdir(), f"{task_id}.output")

        process_ref = {}
        def run_in_thread():
            work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
            env = self._session_env.copy()

            popen_kwargs = dict(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=work_dir,
                env=env,
                shell=False,
            )
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                popen_kwargs["startupinfo"] = si
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            try:
                process = subprocess.Popen(argv, **popen_kwargs)
                process_ref['p'] = process

                with open(output_file, "w", encoding="utf-8") as f:
                    start_time = time.time()
                    while True:
                        if timeout and (time.time() - start_time) > timeout:
                            process.kill()
                            process.wait()
                            f.write(f"\n--- TIMEOUT ({timeout}s) ---\n")
                            return {
                                "task_id": task_id,
                                "output_file": output_file,
                                "error": "timeout",
                            }

                        stdout_line = process.stdout.readline()
                        if stdout_line:
                            f.write(decode_output(stdout_line))
                            f.flush()

                        stderr_line = process.stderr.readline()
                        if stderr_line:
                            f.write(decode_output(stderr_line))
                            f.flush()

                        if not stdout_line and not stderr_line and process.poll() is not None:
                            break

                    return {
                        "task_id": task_id,
                        "output_file": output_file,
                        "exit_code": process.returncode,
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
        self._background_tasks[task_id] = {"future": future, "process_ref": process_ref, "output_file": output_file}

        return {
            "status": "background_started",
            "task_id": task_id,
            "output_file": output_file,
            "message": "命令已在后台执行",
        }

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

        # 提取命令中的环境变量赋值并持久化到会话环境
        self._track_env_assignments(cmd)

        cmd_type = classify_command(cmd)
        effective_timeout = timeout or sec.get("timeout", DEFAULT_TIMEOUT_SECONDS)

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
        shell_cmd, shell_args = self._resolve_shell(cmd)

        if self._platform == "win32" and "powershell" not in shell_cmd.lower():
            full_cmd = [shell_cmd] + shell_args + [cmd]
        else:
            full_cmd = [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]

        stdin_data = None
        if self._platform == "win32" and self._shell_name == "powershell":
            patched, stdin_data = _patch_python_c_for_stdin(full_cmd)
            if stdin_data:
                full_cmd = patched

        work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
        env = self._session_env.copy()

        start_time = time.time()

        popen_kwargs = dict(
            stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work_dir,
            env=env,
            shell=False,
        )
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            popen_kwargs["startupinfo"] = si
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            process = subprocess.Popen(full_cmd, **popen_kwargs)
            self._register_process(process)

            try:
                if stdin_data:
                    stdout, stderr = process.communicate(input=stdin_data, timeout=timeout)
                else:
                    stdout, stderr = process.communicate(timeout=timeout)
                elapsed = time.time() - start_time

                stdout_str = decode_output(stdout).strip()
                stderr_str = decode_output(stderr).strip()

                output, truncated = truncate_output(stdout_str, MAX_OUTPUT_BYTES)

                result = {
                    "success": True,
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
            except (KeyboardInterrupt):
                # 用户停止：kill 子进程并向上传播中断
                try:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
                except Exception:
                    pass
                raise
            finally:
                self._unregister_process(process)

        except FileNotFoundError:
            return {"success": False, "error": f"命令未找到: {shell_cmd}"}
        except PermissionError as e:
            return {"success": False, "error": f"权限拒绝: {e}"}
        except KeyboardInterrupt:
            raise
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
        shell_cmd, shell_args = self._resolve_shell(cmd)

        if self._platform == "win32" and "powershell" not in shell_cmd.lower():
            full_cmd = [shell_cmd] + shell_args + [cmd]
        else:
            full_cmd = [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]

        stdin_data = None
        if self._platform == "win32" and self._shell_name == "powershell":
            patched, stdin_data = _patch_python_c_for_stdin(full_cmd)
            if stdin_data:
                full_cmd = patched

        work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
        env = self._session_env.copy()

        start_time = time.time()
        stdout_lines = []
        stderr_lines = []

        popen_kwargs = dict(
            stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
        )
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            popen_kwargs["startupinfo"] = si
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            process = await asyncio.create_subprocess_exec(*full_cmd, **popen_kwargs)

            if stdin_data:
                try:
                    if timeout > 0:
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(input=stdin_data),
                            timeout=timeout,
                        )
                    else:
                        stdout, stderr = await process.communicate(input=stdin_data)

                    elapsed = time.time() - start_time
                    stdout_str = decode_output(stdout).strip()
                    stderr_str = decode_output(stderr).strip()
                    output, truncated = truncate_output(stdout_str, MAX_OUTPUT_BYTES)

                    result = {
                        "success": True,
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

                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    return {
                        "error": f"命令超时（{timeout}秒）",
                        "timed_out": True,
                        "timeout_seconds": timeout,
                    }

            async def read_stream(stream: asyncio.StreamReader, is_stdout: bool):
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
                    "success": True,
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
        import uuid
        task_id = f"bg_{uuid.uuid4().hex[:8]}"
        output_file = os.path.join(tempfile.gettempdir(), f"{task_id}.output")

        process_ref = {}
        def run_in_thread():
            shell_cmd, shell_args = self._resolve_shell(cmd)

            if self._platform == "win32" and "powershell" not in shell_cmd.lower():
                full_cmd = [shell_cmd] + shell_args + [cmd]
            else:
                full_cmd = [shell_cmd] + shell_args + [cmd] if shell_args else [shell_cmd, cmd]

            stdin_data = None
            if self._platform == "win32" and self._shell_name == "powershell":
                patched, stdin_data = _patch_python_c_for_stdin(full_cmd)
                if stdin_data:
                    full_cmd = patched

            work_dir = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
            env = self._session_env.copy()

            try:
                process = subprocess.Popen(
                    full_cmd,
                    stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=work_dir,
                    env=env,
                    shell=False,
                )
                process_ref['p'] = process

                if stdin_data:
                    process.stdin.write(stdin_data)
                    process.stdin.close()

                with open(output_file, "w", encoding="utf-8") as f:
                    start_time = time.time()
                    while True:
                        if timeout and (time.time() - start_time) > timeout:
                            process.kill()
                            process.wait()
                            f.write(f"\n--- TIMEOUT ({timeout}s) ---\n")
                            return {
                                "task_id": task_id,
                                "output_file": output_file,
                                "error": "timeout",
                            }

                        stdout_line = process.stdout.readline()
                        if stdout_line:
                            f.write(decode_output(stdout_line))
                            f.flush()

                        stderr_line = process.stderr.readline()
                        if stderr_line:
                            f.write(decode_output(stderr_line))
                            f.flush()

                        if not stdout_line and not stderr_line and process.poll() is not None:
                            break

                    return {
                        "task_id": task_id,
                        "output_file": output_file,
                        "exit_code": process.returncode,
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
        self._background_tasks[task_id] = {"future": future, "process_ref": process_ref, "output_file": output_file}

        return {
            "status": "background_started",
            "task_id": task_id,
            "output_file": output_file,
            "message": "命令已在后台执行",
        }

    def kill_background_task(self, task_id: str) -> bool:
        """终止后台任务"""
        if task_id not in self._background_tasks:
            return False
        task = self._background_tasks[task_id]
        proc = task["process_ref"].get("p")
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        try:
            task["future"].cancel()
        except Exception:
            pass
        del self._background_tasks[task_id]
        return True

    def list_background_tasks(self) -> list:
        """列出所有后台任务"""
        tasks = []
        for task_id, task in self._background_tasks.items():
            proc = task["process_ref"].get("p")
            future = task.get("future")
            is_running = proc and proc.poll() is None
            is_done = future and future.done() if future else False

            tasks.append({
                "task_id": task_id,
                "status": "running" if is_running else ("finished" if is_done else "unknown"),
                "output_file": task.get("output_file"),
                "exit_code": proc.returncode if proc and not is_running else None,
                "pid": proc.pid if proc else None,
            })
        return tasks

    def get_background_result(self, task_id: str, timeout: float = 0) -> Optional[Dict]:
        """获取后台任务结果（同步模式）"""
        if task_id not in self._background_tasks:
            return None

        task = self._background_tasks[task_id]
        future = task["future"]

        if asyncio.iscoroutine(future):
            return None

        if timeout > 0:
            try:
                return future.result(timeout=timeout)
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            if future.done():
                try:
                    return future.result()
                except Exception as e:
                    return {"success": False, "error": str(e)}
        return None

    async def get_background_result_async(self, task_id: str) -> Optional[Dict]:
        """获取后台任务结果（异步模式）"""
        if task_id not in self._background_tasks:
            return None

        task = self._background_tasks[task_id]
        future = task["future"]

        if not asyncio.iscoroutine(future):
            return None

        try:
            return await future
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ================================================================
    #  持久会话管理（ssh 复用 / REPL 二次交互）
    # ================================================================

    def start_session(self, argv: List[str], cwd: str = None, window: bool = False,
                  pty: bool = False) -> Dict[str, Any]:
        self._reap_sessions()
        if len(self._sessions) >= SESSION_MAX:
            return {"success": False, "error": f"会话数已达上限({SESSION_MAX})，请先关闭不用的会话"}
        work_dir = cwd if cwd and os.path.isdir(cwd) else self._cwd
        sid = f"ses_{uuid4().hex[:8]}"
        try:
            sess = ShellSession(sid, argv, work_dir, self._session_env.copy(), window=window, pty=pty)
        except Exception as e:
            return {"success": False, "error": f"会话启动失败: {e}"}
        self._sessions[sid] = sess
        mode = "window" if window else ("pty" if pty else "pipe")
        if window:
            return {
                "success": True,
                "session_id": sid,
                "argv": argv,
                "cmd": " ".join(argv),
                "mode": mode,
                "tip": "命令已在独立终端窗口运行（需密码/全屏交互），请在窗口中操作；用 session list 查状态、session close <id> 结束",
            }
        if pty:
            return {
                "success": True,
                "session_id": sid,
                "argv": argv,
                "cmd": " ".join(argv),
                "mode": mode,
                "tip": "命令以伪终端运行，可用 session send 喂输入（如密码）、session output 读输出",
            }
        return {
            "success": True,
            "session_id": sid,
            "argv": argv,
            "cmd": " ".join(argv),
            "mode": mode,
            "tip": "后续用 shell session send <id> <text> 写入、shell session output <id> 读取输出",
        }

    def session_send(self, session_id: str, text: str) -> Dict[str, Any]:
        if not text or not text.strip():
            return {"success": False, "error": "缺少要发送的文本"}
        sess = self._sessions.get(session_id)
        if sess is None:
            return {"success": False, "error": f"会话不存在: {session_id}，可用 shell session list 查看"}
        if not sess.is_alive():
            self._sessions.pop(session_id, None)
            return {"success": False, "error": f"会话已退出: {session_id}"}
        if not sess.send(text):
            return {"success": False, "error": f"写入失败，会话可能已退出: {session_id}"}
        return {"success": True, "session_id": session_id, "sent": text}

    def session_output(self, session_id: str, new_only: bool = True, wait: float = 0) -> Dict[str, Any]:
        sess = self._sessions.get(session_id)
        if sess is None:
            return {"success": False, "error": f"会话不存在: {session_id}"}
        if getattr(sess, "_window", False):
            # 独立窗口会话：输出在窗口内，仅报告状态
            return {
                "success": True,
                "session_id": session_id,
                "output": "",
                "alive": sess.is_alive(),
                "exit_code": sess.exit_code(),
                "mode": "window",
                "note": "该会话在独立终端窗口运行，请检查窗口内容",
            }
        if not sess.is_alive() and not sess.output(new_only=False):
            self._sessions.pop(session_id, None)
            return {"success": False, "error": f"会话已退出: {session_id}"}
        if wait and wait > 0 and sess.is_alive():
            data = sess.wait_for_output(timeout=wait)
        else:
            data = sess.output(new_only=new_only)
        return {
            "success": True,
            "session_id": session_id,
            "output": data,
            "alive": sess.is_alive(),
            "exit_code": sess.exit_code(),
            "new_only": bool(new_only),
            "wait": wait,
        }

    def close_session(self, session_id: str) -> Dict[str, Any]:
        sess = self._sessions.get(session_id)
        if sess is None:
            return {"success": False, "error": f"会话不存在: {session_id}"}
        sess.close()
        self._sessions.pop(session_id, None)
        return {"success": True, "session_id": session_id, "closed": True}

    def list_sessions(self) -> Dict[str, Any]:
        self._reap_sessions()
        items = []
        for sid, sess in self._sessions.items():
            mode = "window" if getattr(sess, "_window", False) else (
                "pty" if getattr(sess, "_pty", False) else "pipe")
            items.append({
                "session_id": sid,
                "cmd": " ".join(sess.argv),
                "alive": sess.is_alive(),
                "mode": mode,
            })
        return {"success": True, "sessions": items, "count": len(items)}

    def _reap_sessions(self):
        now = time.time()
        for sid in list(self._sessions.keys()):
            sess = self._sessions[sid]
            if sess.is_alive():
                continue
            exit_time = getattr(sess, "exit_time", None)
            if exit_time is not None and now - exit_time >= SESSION_REAP_IDLE:
                try:
                    sess.close()
                except Exception:
                    pass
                self._sessions.pop(sid, None)

    # ================================================================
    #  生命周期管理
    # ================================================================

    def terminate(self):
        """清理资源"""
        for task_id in list(self._background_tasks.keys()):
            self.kill_background_task(task_id)
        for sid in list(self._sessions.keys()):
            try:
                self._sessions[sid].close()
            except Exception:
                pass
        self._sessions.clear()
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
