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

import re
from typing import List, Dict, Optional
from enum import Enum


class RiskLevel(Enum):
    """风险等级"""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SecurityPolicy:
    """安全策略 — 命令安全检查与风险分级"""

    # 危险模式黑名单（正则）— ★ 全部使用 re.IGNORECASE
    DANGEROUS_PATTERNS = [
        # ═══ 文件系统破坏 ═══
        (r'remove-item\s+-recurse\s+', RiskLevel.CRITICAL, "禁止递归删除目录"),
        (r'remove-item\s+-force\s+', RiskLevel.CRITICAL, "禁止强制删除"),
        (r'rm\s+-rf\s+(?:/|\\)', RiskLevel.CRITICAL, "禁止递归删除根/全盘"),
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
        (r'netsh\s+ firewall', RiskLevel.HIGH, "禁止修改防火墙"),
        (r'netsh\s+advfirewall', RiskLevel.HIGH, "禁止修改高级防火墙"),
        (r'iptables\s+-F', RiskLevel.HIGH, "禁止清空防火墙规则"),
        (r'ufw\s+(?:disable|reset)', RiskLevel.HIGH, "禁止关闭/重置防火墙"),

        # ═══ 敏感文件访问 ═══
        (r'(?:type|cat|get-content)\s+["\']?[A-Za-z]:\\[^\s]*?(?:password|credentials|\.pem|\.key)', RiskLevel.MEDIUM, "禁止读取密码/密钥文件"),
        (r'(?:type|cat|get-content)\s+["\']?/[^\s]*?(?:password|credentials|\.pem|\.key|shadow)\b', RiskLevel.HIGH, "禁止读取敏感文件"),
        (r'get-content\s+.*\\sam\b', RiskLevel.HIGH, "禁止读取SAM注册表配置"),
        (r'(?:type|cat)\s+.*[/\\]shadow\b', RiskLevel.HIGH, "禁止读取shadow密码文件"),

        # ═══ 进程终止（核心进程保护） ═══
        (r'taskkill\s+/f\s+.*(?:explorer|csrss|lsass|services|svchost|wininit|winlogon)',
         RiskLevel.CRITICAL, "禁止强杀Windows核心进程"),
        (r'stop-process\s+-name\s+.*(?:explorer|csrss|lsass|services|svchost|wininit|winlogon)',
         RiskLevel.CRITICAL, "禁止终止Windows核心进程"),

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
        (r'\bicacls\s+', RiskLevel.HIGH, "禁止修改ACL权限"),
        (r'\bcipher\s+/w\b', RiskLevel.HIGH, "禁止擦除磁盘数据"),
        (r'\bnew-service\b', RiskLevel.HIGH, "禁止创建新服务"),

        # ═══ Linux/Mac 系统控制 ═══
        (r'\binit\s+[06]\b', RiskLevel.HIGH, "禁止init切换运行级别"),
        (r'\bsystemctl\s+(?:poweroff|reboot|halt)\b', RiskLevel.HIGH, "禁止systemctl关机/重启"),
        (r'\bkill\s+-9\s+-1\b', RiskLevel.CRITICAL, "禁止杀死所有进程"),
        (r'\bkillall\s+', RiskLevel.HIGH, "禁止killall批量杀进程"),
        (r'\bpkill\s+-9\b', RiskLevel.HIGH, "禁止pkill强制杀进程"),

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
        (r'>\s*/etc/passwd', RiskLevel.CRITICAL, "禁止覆盖passwd文件"),
        (r'>\s*/etc/shadow', RiskLevel.CRITICAL, "禁止覆盖shadow文件"),

        # ═══ 删除关键系统目录 ═══
        (r'rm\s+-rf\s+/(?:bin|usr|lib|boot|home|var|etc)\b', RiskLevel.CRITICAL, "禁止删除系统关键目录"),
        (r'remove-item\s+-recurse\s+[A-Za-z]:\\(?:Windows|Program Files|ProgramData)', RiskLevel.CRITICAL, "禁止删除Windows系统目录"),

        # ═══ Python/脚本内联执行 ═══
        (r'\bpython(?:3)?\s+-c\s+["\']', RiskLevel.MEDIUM, "Python内联代码执行"),
        (r'\bperl\s+-e\s+["\']', RiskLevel.MEDIUM, "Perl内联代码执行"),
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
                 forbidden_dirs: list = None, command_blacklist: list = None):
        self.max_timeout = max_timeout
        self.max_iterations = max_iterations
        self.forbidden_dirs: List[str] = forbidden_dirs or []
        # 用户自定义黑名单（从配置文件读取）
        self._user_blacklist: List[str] = command_blacklist or []
        self._load_user_blacklist_from_config()

    def _load_user_blacklist_from_config(self):
        """从配置文件加载用户自定义黑名单"""
        try:
            from app.core.util.agent_config import get_config
            config = get_config()
            blacklist = config.get("security.command_blacklist", [])
            if isinstance(blacklist, list):
                self._user_blacklist = [str(item).lower() for item in blacklist if item]
        except Exception:
            pass

    def reload_blacklist(self):
        """热重载用户自定义黑名单"""
        self._load_user_blacklist_from_config()

    def check_command(self, cmd: str) -> Dict:
        """检查命令安全性"""
        if self.forbidden_dirs:
            path_result = self._check_forbidden_path(cmd)
            if path_result:
                return path_result

        self_protection_result = self._check_self_termination(cmd)
        if self_protection_result:
            return self_protection_result

        # 优先检查用户自定义黑名单
        cmd_lower = cmd.lower()
        for pattern in self._user_blacklist:
            if pattern and pattern in cmd_lower:
                return {
                    "allowed": False,
                    "risk_level": RiskLevel.HIGH.value,
                    "message": f"用户自定义黑名单拦截: {pattern}"
                }

        # 再检查硬编码危险模式（保底检测）
        for pattern, risk_level, message in self.DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return {"allowed": False, "risk_level": risk_level.value, "message": message}

        # 最后检查白名单
        if self._is_safe_command(cmd):
            return {"allowed": True, "risk_level": RiskLevel.SAFE.value, "message": "安全命令"}

        return {
            "allowed": True,
            "risk_level": RiskLevel.MEDIUM.value,
            "message": "命令已放行（中等风险）"
        }

    def _check_self_termination(self, cmd: str) -> Optional[Dict]:
        """检查是否尝试终止当前进程"""
        import os
        current_pid = os.getpid()

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
        """路径级访问控制"""
        if not self.forbidden_dirs:
            return None

        cmd_upper = cmd.upper()
        for forbidden in self.forbidden_dirs:
            if not forbidden:
                continue
            f_upper = forbidden.upper().replace("*", "")

            if re.search(r'[A-Z]:\\\\', cmd, re.IGNORECASE):
                paths_in_cmd = re.findall(r'[A-Z]:\\\\[^\s"\']*', cmd)
                for p in paths_in_cmd:
                    if f_upper in p.upper():
                        return {"allowed": False, "risk_level": RiskLevel.CRITICAL.value, "message": f"禁止访问目录: {forbidden}"}

            if f_upper.startswith("/") or "/" in forbidden:
                unix_paths = re.findall(r'["\']?(/[^\s"\']+)', cmd)
                for p in unix_paths:
                    if p.startswith(f_upper.rstrip("*")) or \
                       (f_upper.endswith("*") and p.startswith(f_upper[:-1])):
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
