# -*- coding: utf-8 -*-
"""
通用安全策略引擎

位置：app/core/security/policy.py（原 app/agent/security/policy.py）
原因：
  - SecurityPolicy 是通用安全决策引擎
  - Shell、组件审核等模块都可能使用
  - 不应绑定到 Agent 层

agent/security/__init__.py 保留重导出以兼容旧代码。
"""

import fnmatch
import os
import re
import shlex
from typing import List, Dict, Optional
from enum import Enum


_HOST_RUNTIMES = {
    "python", "python3", "py", "node", "deno", "java", "powershell", "pwsh",
    "cellium", "cellium-agent", "cmd", "bash", "sh", "zsh", "fish",
}
_SHUTDOWN_PROGRAMS = {
    "shutdown", "reboot", "poweroff", "halt", "stop-computer", "restart-computer",
}
_PIPE_INTERPRETERS = {
    "bash", "sh", "zsh", "fish", "python", "python3", "py", "perl", "ruby",
    "node", "deno", "pwsh", "powershell", "iex", "invoke-expression",
}
_REVERSIBLE_PROGRAMS = {
    "rm", "del", "erase", "rmdir", "rd", "remove-item", "chmod", "mv", "move",
}
_SCRIPT_RUNNERS = _PIPE_INTERPRETERS | {"cmd"}
_WRAPPERS = {
    "sudo", "doas", "env", "nohup", "nice", "time", "command", "exec",
    "xargs", "start", "runas", "start-process", "invoke-item", "call",
}
_HOST_RUNTIME_RE = re.compile(
    r'^(?:python|py|node|deno|java|powershell|pwsh|cellium|cellium-agent|'
    r'cmd|bash|sh|zsh|fish)[0-9.]*$'
)
_BALANCED_SKIP_PATTERNS = {
    r'\bshutdown\b',
    r'\breboot\b',
    r'stop-computer',
    r'restart-computer',
    r'\bkillall\s+',
    r'\|\s*(?:bash|sh|zsh)\b',
    r'\|\s*(?:python|python3|perl|ruby|node)\s+-[cex]',
}


def _split_unquoted(text: str, seps) -> List[str]:
    """按未引号包裹的分隔符切分，避免把引号内文本当命令"""
    out, buf, quote, i = [], [], None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        matched = next((s for s in seps if text.startswith(s, i)), None)
        if matched:
            out.append("".join(buf))
            buf = []
            i += len(matched)
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _tokens(segment: str) -> List[str]:
    posix = os.name != "nt" and "\\" not in segment
    try:
        parts = shlex.split(segment, posix=posix)
    except Exception:
        parts = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', segment)
    return [str(p).strip('"\'') for p in parts]


def _program(tokens: List[str]) -> str:
    if not tokens:
        return ""
    base = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _is_host_runtime(name: str) -> bool:
    base = os.path.basename(str(name)).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base in _HOST_RUNTIMES or bool(_HOST_RUNTIME_RE.match(base))


_WIN_ABS_RE = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\)')


def _is_foreign_abs(path: str) -> bool:
    """当前系统解析不了的另一种平台的绝对路径（如 POSIX 上的 C:\\Windows\\Temp）"""
    return os.name != "nt" and bool(_WIN_ABS_RE.match(path or ""))



class RiskLevel(Enum):
    """风险等级"""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SecurityPolicy:
    """安全策略 — 命令安全检查与风险分级"""

    # 危险模式黑名单）— 全部使用 re.IGNORECASE
    DANGEROUS_PATTERNS = [
        # ═══ Git 不可回退操作（.git 不在 undo 快照范围） ═══
        (r'\bgit\s+reset\s+--hard\b', RiskLevel.CRITICAL, "禁止 git reset --hard（工作区改动不可回退）"),
        (r'\bgit\s+clean\s+(?:-[a-z]*f|-[a-z]*x|--force)', RiskLevel.CRITICAL, "禁止 git clean（未跟踪文件不可回退）"),
        (r'\bgit\s+checkout\s+(?:--\s+)?\.(?=$|[\s;&|])', RiskLevel.CRITICAL, "禁止 git checkout .（工作区改动不可回退）"),
        (r'\bgit\s+restore\b(?!\s+--staged)', RiskLevel.CRITICAL, "禁止 git restore（工作区改动不可回退）"),

        # ═══ 文件系统破坏 ═══
        (r'remove-item\s+-recurse\s+', RiskLevel.CRITICAL, "禁止递归删除目录"),
        (r'remove-item\s+-force\s+', RiskLevel.CRITICAL, "禁止强制删除"),
        (r'rm\s+-rf\s+(?:/|\\)(?=$|[\s;&|"\'`*])', RiskLevel.CRITICAL, "禁止递归删除根/全盘"),
        (r'rm\s+[^|;&\n]*--no-preserve-root', RiskLevel.CRITICAL, "禁止绕过根目录保护删除"),
        (r'rm\s+-rf\s+\$(?:\{)?HOME\b', RiskLevel.CRITICAL, "禁止删除用户主目录"),
        (r'del\s+/[fqs]+\s+', RiskLevel.HIGH, "禁止强制批量删除"),
        (r'rmdir\s+/s\s+/q', RiskLevel.HIGH, "禁止强制删除目录"),

        # ═══ 磁盘破坏性操作 ═══
        (r'\bformat\b\s+\w:', RiskLevel.CRITICAL, "禁止格式化磁盘"),
        (r'\bdiskpart\b', RiskLevel.CRITICAL, "禁止磁盘分区管理"),
        (r'\bmkfs\.\w+', RiskLevel.CRITICAL, "禁止Linux格式化文件系统"),
        (r'\bdd\s+if=\S+\s+of=', RiskLevel.CRITICAL, "禁止dd磁盘写入"),
        (r'\bclean\b.*\bdisk\b', RiskLevel.CRITICAL, "禁止clean清除磁盘数据"),

        # ═══ 注册表危险操作 ═══
        (r'\breg\s+delete\b', RiskLevel.CRITICAL, "禁止删除注册表项"),
        (r'\breg\s+import\b', RiskLevel.HIGH, "禁止导入注册表"),
        (r'remove-itemproperty\s+.*(?:HKLM|HKCU|HKCR|HKU):', RiskLevel.CRITICAL, "禁止修改注册表"),
        (r'set-itemproperty\s+.*(?:HKLM|HKCU|HKCR|HKU):', RiskLevel.CRITICAL, "禁止修改注册表键值"),
        (r'new-item\s+-path\s+.*(?:Registry::|HKLM|HKCU)', RiskLevel.HIGH, "禁止创建注册表项"),

        # ═══ 启动配置 / BCD ═══
        (r'\bbcdedit\b', RiskLevel.CRITICAL, "禁止修改启动配置(BCD)"),
        (r'\bbootcfg\b', RiskLevel.CRITICAL, "禁止修改启动配置"),
        (r'\bmsconfig\b', RiskLevel.HIGH, "禁止修改系统配置(msconfig)"),

        # ═══ 服务管理（高危） ═══
        (r'\bsc\s+(?:config|delete|create)\b', RiskLevel.HIGH, "禁止修改/删除/创建服务"),
        (r'set-service\s+.*-(?:startup|status)\s+', RiskLevel.HIGH, "禁止修改服务状态"),
        (r'stop-service\s+-name\s+.*(?:winmgmt|eventlog|cryptsvc|dns|rpcss|lanmanserver)',
         RiskLevel.CRITICAL, "禁止停止核心系统服务"),
        (r'restart-service\s+-name\s+.*(?:winmgmt|eventlog|cryptsvc|dns|rpcss|lanmanserver)',
         RiskLevel.CRITICAL, "禁止重启核心系统服务"),

        # ═══ 组策略 / 权限提升 ═══
        (r'\bgpupdate\b', RiskLevel.HIGH, "禁止强制更新组策略"),
        (r'set-executionpolicy', RiskLevel.HIGH, "禁止修改执行策略"),
        (r'enable-psremoting', RiskLevel.HIGH, "禁止开启PS远程管理"),
        (r'net\s+user\s+.*\s+/add', RiskLevel.MEDIUM, "禁止创建用户账号"),
        (r'net\s+localgroup\s+.*administrators', RiskLevel.HIGH, "禁止提权到管理员组"),
        (r'\buseradd\b', RiskLevel.MEDIUM, "禁止创建Linux用户"),
        (r'\bchown\b\s+-R\s+/', RiskLevel.HIGH, "禁止递归修改所有者"),
        (r'\bchmod\s+777\b', RiskLevel.HIGH, "禁止设置全局可执行权限"),

        # ═══ 关机 / 重启 ═══
        (r'\bshutdown\b', RiskLevel.HIGH, "禁止关机命令"),
        (r'\breboot\b', RiskLevel.HIGH, "禁止重启命令"),
        (r'stop-computer', RiskLevel.HIGH, "禁止PowerShell关机"),
        (r'restart-computer', RiskLevel.HIGH, "禁止PowerShell重启"),

        # ═══ 防火墙 / 网络 ═══
        (r'netsh\s+firewall', RiskLevel.HIGH, "禁止修改防火墙"),
        (r'netsh\s+advfirewall', RiskLevel.HIGH, "禁止修改高级防火墙"),
        (r'iptables\s+-F', RiskLevel.HIGH, "禁止清空防火墙规则"),
        (r'ufw\s+(?:disable|reset)', RiskLevel.HIGH, "禁止关闭/重置防火墙"),

        # ═══ 敏感文件访问 ═══
        # Windows 路径格式
        (r'(?:type|get-content)\s+["\']?[A-Za-z]:\\[^\s]*?(?:password|credentials|\.pem|\.key)', RiskLevel.MEDIUM, "禁止读取密码/密钥文件"),
        # Unix 路径格式 (Linux/macOS)
        (r'(?:cat|less|more|head|tail)\s+["\']?/[^\s]*?(?:password|credentials|\.pem|\.key|shadow|passwd|id_rsa|id_dsa)\b', RiskLevel.HIGH, "禁止读取敏感文件"),
        (r'get-content\s+.*\\sam\b', RiskLevel.HIGH, "禁止读取SAM注册表配置"),
        (r'(?:type|cat)\s+.*[/\\]shadow\b', RiskLevel.HIGH, "禁止读取shadow密码文件"),
        (r'cat\s+/etc/(?:passwd|shadow|group|gshadow)\b', RiskLevel.HIGH, "禁止读取系统配置文件"),

        # ═══ 进程终止（核心进程保护） ═══
        (r'taskkill\s+/f\s+.*(?:explorer|csrss|lsass|services|svchost|wininit|winlogon)',
         RiskLevel.CRITICAL, "禁止强杀Windows核心进程"),
        (r'stop-process\s+-name\s+.*(?:explorer|csrss|lsass|services|svchost|wininit|winlogon)',
         RiskLevel.CRITICAL, "禁止终止Windows核心进程"),

        # ═══ 按名称/命令行模式杀进程═══
        (r'\bpkill\b', RiskLevel.HIGH,
         "pkill 按名称/命令行匹配，可能误杀当前进程（如 python 宿主），请先查 PID 再 kill <pid>"),
        (r'\bpgrep\b[^|;&\n]*\|\s*(?:xargs\s+)?(?:kill|pkill)\b', RiskLevel.HIGH,
         "按模式批量杀进程可能命中当前进程，请先查 PID 再 kill <pid>"),
        (r'\bkill\b[^|;&\n]*(?:\$\(|`)\s*(?:pgrep|ps)\b', RiskLevel.HIGH,
         "按模式取 PID 再 kill 可能命中当前进程，请先查 PID 再单独 kill <pid>"),

        # ═══ 计划任务（持久化攻击面） ═══
        (r'\bschtasks\b\s+/(?:create|delete|change)', RiskLevel.HIGH, "禁止创建/删除计划任务"),
        (r'register-scheduledtask', RiskLevel.HIGH, "禁止PowerShell注册计划任务"),
        (r'unregister-scheduledtask', RiskLevel.HIGH, "禁止注销计划任务"),

        # ═══ PowerShell 编码执行 / 动态代码执行 ═══
        (r'powershell\s+-(?:e|enc|encodedcommand)\s+', RiskLevel.CRITICAL, "禁止编码执行PowerShell"),
        (r'invoke-expression\b', RiskLevel.HIGH, "禁止动态执行代码(IEX)"),
        (r'\|\s*iex\b', RiskLevel.HIGH, "禁止IEX动态执行"),
        (r'\biex\s+\$', RiskLevel.HIGH, "禁止IEX动态执行变量"),
        (r'invoke-webrequest.*downloadstring', RiskLevel.HIGH, "禁止下载并执行远程脚本"),
        (r'invoke-webrequest.*downloadfile\s+.*\|\s*(?:iex|invoke-expression)', RiskLevel.HIGH, "禁止下载并执行文件"),

        # ═══ Windows 危险工具 ═══
        (r'\bcertutil\s+-urlcache\b', RiskLevel.HIGH, "禁止certutil下载"),
        (r'\bbitsadmin\s+/transfer\b', RiskLevel.HIGH, "禁止bitsadmin传输"),
        (r'\bmshta\s+', RiskLevel.HIGH, "禁止mshta执行HTA"),
        (r'\brundll32\s+', RiskLevel.HIGH, "禁止rundll32加载DLL"),
        (r'\bregsvr32\s+', RiskLevel.HIGH, "禁止regsvr32注册DLL"),
        (r'\bwmic\s+process\s+call\s+create\b', RiskLevel.HIGH, "禁止WMIC创建进程"),

        # ═══ Windows 权限操作 ═══
        (r'\btakeown\s+', RiskLevel.HIGH, "禁止夺取文件所有权"),
        (r'\bicacls\s+.*(?:/grant|/deny|/remove|/reset|/setowner|/inheritance)', RiskLevel.HIGH, "禁止修改ACL权限"),
        (r'\bcipher\s+/w\b', RiskLevel.HIGH, "禁止擦除磁盘数据"),
        (r'\bnew-service\b', RiskLevel.HIGH, "禁止创建新服务"),

        # ═══ Linux/Mac 系统控制 ═══
        (r'\binit\s+[06]\b', RiskLevel.HIGH, "禁止init切换运行级别"),
        (r'\bsystemctl\s+(?:poweroff|reboot|halt)\b', RiskLevel.HIGH, "禁止systemctl关机/重启"),
        (r'\bkill\s+-9\s+-1\b', RiskLevel.CRITICAL, "禁止杀死所有进程"),
        (r'\bkillall\s+', RiskLevel.HIGH, "禁止killall批量杀进程"),

        # ═══ Linux/Mac 定时任务（持久化攻击） ═══
        (r'\bcrontab\s+-[er]\b', RiskLevel.HIGH, "禁止修改crontab"),
        (r'\|\s*crontab\b', RiskLevel.HIGH, "禁止管道写入crontab"),

        # ═══ 远程执行 ═══
        (r'\bssh\s+.*(?:bash|sh|python)\s+-c\b', RiskLevel.HIGH, "禁止SSH远程执行命令"),
        (r'\bscp\s+.*(?:\|\||&&)', RiskLevel.MEDIUM, "可疑SCP命令链"),

        # ═══ Fork 炸弹 ═══
        (r':\(\)\s*\{[^}]*:\|:&?[^}]*\}', RiskLevel.CRITICAL, "检测到Fork炸弹"),
        (r'\./:\(\)', RiskLevel.CRITICAL, "检测到Fork炸弹执行"),
        (r'fork\s*\(\s*\)\s*;\s*fork\s*\(\s*\)', RiskLevel.HIGH, "检测到Fork炸弹模式"),

        # ═══ 管道执行任意代码 ═══
        (r'\|\s*(?:bash|sh|zsh)\b', RiskLevel.HIGH, "禁止管道执行Shell"),
        (r'\|\s*(?:python|python3|perl|ruby|node)\s+-[cex]', RiskLevel.HIGH, "禁止管道执行脚本"),

        # ═══ 环境变量注入 ═══
        (r'LD_PRELOAD\s*=', RiskLevel.CRITICAL, "禁止LD_PRELOAD注入"),
        (r'DYLD_INSERT_LIBRARIES\s*=', RiskLevel.CRITICAL, "禁止macOS库注入"),

        # ═══ 覆盖关键配置文件 ═══
        (r'>\s*~/.bashrc', RiskLevel.HIGH, "禁止覆盖bashrc"),
        (r'>\s*~/.zshrc', RiskLevel.HIGH, "禁止覆盖zshrc"),
        (r'>\s*~/.ssh/', RiskLevel.HIGH, "禁止覆盖SSH配置"),
        (r'>\s*~/.gnupg/', RiskLevel.HIGH, "禁止覆盖GPG配置"),
        (r'>\s*/etc/passwd', RiskLevel.CRITICAL, "禁止覆盖passwd文件"),
        (r'>\s*/etc/shadow', RiskLevel.CRITICAL, "禁止覆盖shadow文件"),
        (r'>\s*/etc/hosts', RiskLevel.HIGH, "禁止覆盖hosts文件"),
        (r'>\s*/etc/resolv.conf', RiskLevel.HIGH, "禁止覆盖DNS配置"),
        (r'>\s*/etc/sudoers', RiskLevel.CRITICAL, "禁止覆盖sudoers文件"),
        (r'>\s*/etc/ssh/', RiskLevel.CRITICAL, "禁止覆盖SSH系统配置"),

        # ═══ 删除关键系统目录 ═══
        # Linux/macOS
        (r'rm\s+-rf\s+/(?:bin|usr|lib|boot|home|var|etc|sbin|opt)(?:/|\b)', RiskLevel.CRITICAL, "禁止删除系统关键目录"),
        (r'rm\s+-rf\s+/tmp(?:/)?(?=$|[\s;&|"\'`])', RiskLevel.CRITICAL, "禁止删除系统临时目录"),
        (r'rm\s+-rf\s+~(?:/)?(?=$|[\s;&|"\'`])', RiskLevel.CRITICAL, "禁止删除用户主目录"),
        (r'rm\s+-rf\s+\.\*(?=$|[\s;&|"\'`])', RiskLevel.HIGH, "禁止通配删除隐藏文件"),
        (r'rm\s+-rf\s+\.(?=$|[\s;&|"\'`])', RiskLevel.HIGH, "禁止删除当前目录"),
        (r'rm\s+-rf\s+~\/\.', RiskLevel.HIGH, "禁止删除用户配置目录"),
        # Windows
        (r'remove-item\s+-recurse\s+[A-Za-z]:\\(?:Windows|Program Files|ProgramData)', RiskLevel.CRITICAL, "禁止删除Windows系统目录"),
        (r'rmdir\s+/s\s+[A-Za-z]:\\', RiskLevel.CRITICAL, "禁止删除Windows盘符根目录"),
    ]

    # 安全白名单（常用安全命令）
    SAFE_PATTERNS = [
        r'^(Get-|Select-|Where-|Sort-|Measure-|Format-)',
        r'^(ls|dir|Get-ChildItem)',
        r'^(Get-Process|ps)',
        r'^(Get-Service)',
        r'^(Get-Content|cat|type)\s+(?!.*password)',
        r'^(echo|Write-Host|Write-Output)',
        r'^(Test-Connection|ping)',
        r'^(Get-Date|date)',
        r'^(Get-Location|pwd|cd)',
    ]

    def __init__(self, max_timeout: int = 30, max_iterations: int = 10,
                 forbidden_dirs: list = None, command_blacklist: list = None,
                 permission_level: str = None, block_mode: str = None):
        self.max_timeout = max_timeout
        self.max_iterations = max_iterations
        self.forbidden_dirs: List[str] = forbidden_dirs or []
        self._user_blacklist: List[str] = command_blacklist or []
        self._blacklist_explicit = command_blacklist is not None
        self.permission_level = permission_level
        self.block_mode = block_mode
        self._load_user_blacklist_from_config()

    def _load_user_blacklist_from_config(self):
        try:
            from app.core.util.agent_config import get_config
            config = get_config()
            if self.permission_level is None:
                self.permission_level = config.get("security.permission_level", "standard")
            if self.block_mode is None:
                self.block_mode = config.get("security.block_mode", "balanced")
            if self._blacklist_explicit:
                return
            blacklist = config.get("security.command_blacklist", [])
            if isinstance(blacklist, list):
                self._user_blacklist = [str(item).lower() for item in blacklist if item]
        except Exception:
            pass

    def reload_blacklist(self):
        self._load_user_blacklist_from_config()

    def check_command(self, cmd: str, cwd: str = None, undo_ok: bool = False,
                      undo_root: str = None) -> Dict:
        perm = (self.permission_level or "standard").lower()

        if perm == "unrestricted":
            return {"allowed": True, "risk_level": RiskLevel.SAFE.value, "message": "无限制模式，全部放行"}

        if self.forbidden_dirs:
            path_result = self._check_forbidden_path(cmd)
            if path_result:
                return path_result

        self_protection_result = self._check_self_termination(cmd)
        if self_protection_result:
            return self_protection_result

        if perm == "read_only":
            for pattern, risk_level, _msg in self.DANGEROUS_PATTERNS:
                if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and re.search(pattern, cmd, re.IGNORECASE):
                    return {"allowed": False, "risk_level": risk_level.value, "message": "只读模式，禁止执行写入/高风险命令"}
            if not re.search(r'[;&|`>]|\$\(', cmd) and self._is_safe_command(cmd):
                return {"allowed": True, "risk_level": RiskLevel.SAFE.value, "message": "安全命令"}
            return {"allowed": False, "risk_level": RiskLevel.HIGH.value, "message": "只读模式，禁止执行写入/高风险命令"}

        balanced = (self.block_mode or "balanced").lower() == "balanced" and perm != "admin"

        if balanced:
            positional = self._positional_deny(cmd)
            if positional:
                return positional
            if undo_ok:
                if self._workspace_reversible(cmd, cwd, undo_root):
                    return {
                        "allowed": True,
                        "risk_level": RiskLevel.LOW.value,
                        "message": "工作区内可回退操作，已放行",
                    }
                if self._targets_of(cmd) is not None:
                    return {
                        "allowed": False,
                        "risk_level": RiskLevel.HIGH.value,
                        "message": "删除/移动目标在工作区外或未纳入回退快照，请缩小到工作区内",
                    }

        for pattern, risk_level, message in self.DANGEROUS_PATTERNS:
            if balanced and pattern in _BALANCED_SKIP_PATTERNS:
                continue
            if perm == "admin" and risk_level != RiskLevel.CRITICAL:
                continue
            if re.search(pattern, cmd, re.IGNORECASE):
                return {"allowed": False, "risk_level": risk_level.value, "message": message}

        hit = self._user_blacklist_hit(cmd)
        if hit:
            return {
                "allowed": False,
                "risk_level": RiskLevel.HIGH.value,
                "message": f"用户自定义黑名单拦截: {hit}"
            }

        if self._is_safe_command(cmd):
            return {"allowed": True, "risk_level": RiskLevel.SAFE.value, "message": "安全命令"}

        return {
            "allowed": True,
            "risk_level": RiskLevel.MEDIUM.value,
            "message": "命令已放行（中等风险）"
        }

    def _user_blacklist_hit(self, cmd: str) -> Optional[str]:
        """用户黑名单：带通配符按 glob，纯命令按前缀匹配（不做 substring 误伤）"""
        text = (cmd or "").strip().lower()
        for pattern in self._user_blacklist:
            p = (pattern or "").strip().lower()
            if not p:
                continue
            if any(ch in p for ch in "*?["):
                if fnmatch.fnmatch(text, p) or fnmatch.fnmatch(text, p + " *"):
                    return pattern
            elif any(ch in p for ch in "|;&(){}<>`$"):
                if p in text:
                    return pattern
            elif text == p or text.startswith(p + " "):
                return pattern
        return None

    @staticmethod
    def _positional_deny(cmd: str, _depth: int = 0) -> Optional[Dict]:
        """按命令位判断：关机/重启、管道执行、killall 宿主运行时"""
        for segment in _split_unquoted(cmd, ("\n", ";", "&&", "||")):
            parts = _split_unquoted(segment, ("|",))
            for idx, part in enumerate(parts):
                toks = _tokens(part)
                prog = _program(toks)
                # 剥离 sudo/env/nohup 等包装器与 PowerShell 调用运算符
                while prog in _WRAPPERS and len(toks) > 1:
                    toks = toks[1:]
                    while toks and (toks[0].startswith("-") or (prog == "env" and "=" in toks[0])):
                        toks = toks[1:]
                    prog = _program(toks)
                if prog in ("&", ".") and len(toks) > 1:
                    toks = toks[1:]
                    prog = _program(toks)
                if not prog:
                    continue
                if prog in _SHUTDOWN_PROGRAMS:
                    if prog == "init" and not (len(toks) > 1 and toks[1] in ("0", "6")):
                        continue
                    return {"allowed": False, "risk_level": RiskLevel.HIGH.value, "message": "禁止关机/重启命令"}
                if prog == "systemctl" and len(toks) > 1 and toks[1].lower() in ("poweroff", "reboot", "halt"):
                    return {"allowed": False, "risk_level": RiskLevel.HIGH.value, "message": "禁止 systemctl 关机/重启"}
                if idx > 0 and prog in _PIPE_INTERPRETERS:
                    return {"allowed": False, "risk_level": RiskLevel.HIGH.value, "message": "禁止管道执行脚本/Shell"}
                if prog == "killall":
                    names = [t for t in toks[1:] if not t.startswith("-")]
                    if any(_is_host_runtime(n) for n in names):
                        return {
                            "allowed": False,
                            "risk_level": RiskLevel.HIGH.value,
                            "message": "killall 目标包含宿主运行时，请先查 PID 再 kill <pid>",
                        }
                if _depth < 2 and prog in _SCRIPT_RUNNERS:
                    script = SecurityPolicy._script_of(toks)
                    if script:
                        inner = SecurityPolicy._positional_deny(script, _depth + 1)
                        if inner:
                            return inner
        return None

    @staticmethod
    def _script_of(toks: List[str]) -> Optional[str]:
        """取出解释器 -c / -Command 后紧跟的内联脚本"""
        for i, tok in enumerate(toks[1:], 1):
            if tok.lower() in ("-c", "/c", "-command", "--command", "-e") and i + 1 < len(toks):
                return toks[i + 1]
        return None

    def _targets_of(self, cmd: str) -> Optional[List[str]]:
        """命令全部由可回退程序组成时返回目标路径，否则 None"""
        segments = _split_unquoted(cmd, ("\n", ";", "&&", "||"))
        if not segments:
            return None
        targets: List[str] = []
        for segment in segments:
            for part in _split_unquoted(segment, ("|",)):
                toks = _tokens(part)
                prog = _program(toks)
                if prog not in _REVERSIBLE_PROGRAMS:
                    return None
                found = self._target_paths(prog, toks[1:])
                if found is None:
                    return None
                targets.extend(found)
        return targets

    def _workspace_reversible(self, cmd: str, cwd: str, root: str = None) -> bool:
        """工作区内、且 undo 已快照的写操作 → 视为可回退"""
        base = os.path.abspath(cwd or os.getcwd())
        boundary = os.path.realpath(os.path.abspath(root or base))
        if not os.path.isdir(boundary):
            return False
        if boundary in ("/", os.path.expanduser("~")):
            return False
        if os.name == "nt" and re.fullmatch(r"[A-Za-z]:\\?", boundary):
            return False

        targets = self._targets_of(cmd)
        if targets is None:
            return False
        boundary_key = os.path.normcase(boundary)
        for target in targets:
            if _is_foreign_abs(target):
                return False
            full = os.path.abspath(
                target if os.path.isabs(target) else os.path.join(base, target)
            )
            real = os.path.realpath(full)
            key = os.path.normcase(real)
            try:
                if os.path.commonpath([boundary_key, key]) != boundary_key:
                    return False
            except ValueError:
                return False
            if key == boundary_key:
                return False
            if ".git" in real.split(os.sep):
                return False
        return True

    @staticmethod
    def _target_paths(prog: str, args: List[str]) -> Optional[List[str]]:
        targets: List[str] = []
        skip_mode = prog == "chmod"
        for arg in args:
            if not arg or arg.startswith("-"):
                continue
            if prog in ("del", "erase", "rmdir", "rd") and re.fullmatch(r"/[A-Za-z]+", arg):
                continue
            if skip_mode:
                skip_mode = False
                continue
            if any(ch in arg for ch in "*?[]$%`"):
                return None
            targets.append(arg)
        return targets

    def _check_self_termination(self, cmd: str) -> Optional[Dict]:
        """检查是否尝试终止当前进程"""
        import os
        current_pid = os.getpid()

        taskkill_im_match = re.search(
            r'taskkill\s+(?:/f\s+)?/im\s+(\S+)',
            cmd,
            re.IGNORECASE
        )
        if taskkill_im_match:
            if re.search(rf'/fi\s+["\']?PID\s+ne\s+{current_pid}', cmd, re.IGNORECASE):
                return None

            modified_cmd = cmd.rstrip()
            modified_cmd += f' /fi "PID ne {current_pid}"'
            return {
                "allowed": True,
                "risk_level": RiskLevel.MEDIUM.value,
                "message": f"已自动添加排除参数，保护当前进程 (PID: {current_pid})",
                "modified_command": modified_cmd
            }

        stop_process_name_match = re.search(
            r'stop-process\s+(-name\s+\S+)',
            cmd,
            re.IGNORECASE
        )
        if stop_process_name_match:
            if re.search(rf'-id\s+{current_pid}', cmd) or \
               re.search(r'-exclude.*{current_pid}', cmd):
                return None
            
            return {
                "allowed": False,
                "risk_level": RiskLevel.HIGH.value,
                "message": f"Stop-Process -Name 会误杀当前进程 (PID: {current_pid})，请指定具体 PID 或使用 taskkill"
            }

        taskkill_pattern = rf'taskkill\s+.*\b{current_pid}\b'
        if re.search(taskkill_pattern, cmd, re.IGNORECASE):
            return {
                "allowed": False,
                "risk_level": RiskLevel.CRITICAL.value,
                "message": f"禁止终止当前进程 (PID: {current_pid})"
            }

        kill_pattern = rf'kill\s+.*\b{current_pid}\b'
        if re.search(kill_pattern, cmd, re.IGNORECASE):
            return {
                "allowed": False,
                "risk_level": RiskLevel.CRITICAL.value,
                "message": f"禁止终止当前进程 (PID: {current_pid})"
            }

        stop_process_pattern = rf'stop-process\s+.*\b{current_pid}\b'
        if re.search(stop_process_pattern, cmd, re.IGNORECASE):
            return {
                "allowed": False,
                "risk_level": RiskLevel.CRITICAL.value,
                "message": f"禁止终止当前进程 (PID: {current_pid})"
            }

        return None

    def _is_safe_command(self, cmd: str) -> bool:
        """检查是否在白名单中"""
        for pattern in self.SAFE_PATTERNS:
            if re.match(pattern, cmd, re.IGNORECASE):
                return True
        return False

    def _check_forbidden_path(self, cmd: str) -> Optional[Dict]:
        """路径级访问控制 - 支持 Windows 和 Unix 路径格式"""
        if not self.forbidden_dirs:
            return None

        cmd_upper = cmd.upper()
        for forbidden in self.forbidden_dirs:
            if not forbidden:
                continue
            f_upper = forbidden.upper().replace("*", "")

            # Windows 路径检测 (C:\path\to\dir)
            if re.search(r'[A-Za-z]:\\', cmd):
                paths_in_cmd = re.findall(r'[A-Za-z]:\\[^"\']*', cmd)
                for p in paths_in_cmd:
                    if f_upper in p.upper():
                        return {"allowed": False, "risk_level": RiskLevel.CRITICAL.value, "message": f"禁止访问目录: {forbidden}"}
            
            # Unix 路径检测 (/path/to/dir 或 ~/path)
            if f_upper.startswith("/") or f_upper.startswith("~/"):
                # 检测绝对路径 /path
                unix_paths = re.findall(r'["\']?(/[^"\'\s]+)', cmd)
                # 检测用户目录 ~/path
                home_paths = re.findall(r'(~\/[^"\'\s]+)', cmd)
                all_paths = unix_paths + home_paths
                
                for p in all_paths:
                    p_upper = p.upper()
                    # 完全匹配或前缀匹配
                    if p_upper.startswith(f_upper.rstrip("*")) or \
                       (f_upper.endswith("*") and p_upper.startswith(f_upper[:-1])):
                        return {"allowed": False, "risk_level": RiskLevel.CRITICAL.value, "message": f"禁止访问目录: {forbidden}"}
        return None

    def set_forbidden_dirs(self, dirs: List[str]):
        """运行时动态设置禁止目录列表"""
        self.forbidden_dirs = list(dirs)

    def get_forbidden_dirs(self) -> List[str]:
        """获取当前禁止目录列表"""
        return list(self.forbidden_dirs)

    def get_timeout(self, risk_level: RiskLevel) -> int:
        """根据风险等级获取超时"""
        timeouts = {
            RiskLevel.SAFE: 60, RiskLevel.LOW: 60, RiskLevel.MEDIUM: 120,
            RiskLevel.HIGH: 180, RiskLevel.CRITICAL: 0,
        }
        return timeouts.get(risk_level, 120)
