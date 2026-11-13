# -*- coding: utf-8 -*-
"""Plan / Run 运行模式"""

import re
import shlex
from typing import Any, Dict, Optional

PLAN = "plan"
RUN = "run"
DELEGATE = "delegate"
VALID_MODES = (PLAN, RUN, DELEGATE)

PLAN_BLOCKED_TOOLS = frozenset({"edit", "skill_installer", "session_agent"})
DELEGATE_BLOCKED_TOOLS = frozenset({"edit", "skill_installer"})

# 前缀 / 模式名 / 引导语
_BLOCK_CONFIG = {
    PLAN: ("Plan 模式禁止修改", "只读计划模式", "请把该动作写进计划。"),
    DELEGATE: ("委托模式禁止修改", "会话委托模式", "请用 session_agent 委托目标会话执行。"),
}

_PLAN_WRITE_SUBCMDS = {
    "memory": frozenset({
        "store", "update", "delete", "forget", "merge",
        "set_embedding", "start_embedding_migration",
    }),
    "scheduler": frozenset({
        "create_interval", "create_daily", "create_weekly",
        "delete", "enable", "disable", "run_now",
    }),
    "config": frozenset({
        "add_model", "switch_model", "enable_intent", "set_intent_model",
        "set_telegram", "set_qq", "set_feishu", "enable_channel",
        "set_show_tool_cards", "set_show_thinking_plan",
    }),
    "component": frozenset({
        "generate", "generate_package", "install_local", "market_install",
        "sandbox_add", "sandbox_remove", "reload", "reset",
        "add_user", "send_private",
    }),
}

# 渠道文件工具：只放行 list
_PLAN_LIST_ONLY_TOOLS = frozenset({
    "qq_files", "telegram_files", "feishu_files", "weixin_files",
})

_FILE_WRITE_ACTIONS = frozenset({"create", "delete", "mkdir"})

_SHELL_SPLIT = re.compile(r"\|\||&&|\||;|\n|&")
_SHELL_CALL_OP = re.compile(r"(?:^|[;\n|]\s*)&\s+\S")
_SHELL_REDIRECT = re.compile(r">+")
_SHELL_ESCAPE = re.compile(
    r"\b(?:python[0-9.]*|py|perl|ruby|bash|sh|zsh|fish|node)\s+(?:-c|-e|-p|--eval)\b"
    r"|\bpython[0-9.]*\s*<<"
    r"|\b(?:powershell|pwsh)\S*\s+(?:-c|-command|-encodedcommand)\b"
    r"|\bcmd(?:\.exe)?\s+\/c\b"
    r"|\binvoke-expression\b|\biex\b|\beval\b",
    re.IGNORECASE,
)

_SHELL_WRITE_CMDS = frozenset({
    "rm", "del", "erase", "rd", "rmdir", "unlink", "shred", "truncate", "dd",
    "mv", "move", "ren", "rename", "cp", "copy", "xcopy", "robocopy",
    "touch", "mkdir", "md", "ln", "mklink", "chmod", "chown", "chattr",
    "icacls", "attrib", "tee", "kill", "pkill", "killall", "taskkill",
    "shutdown", "reboot", "poweroff", "halt", "sudo", "xargs", "nohup", "watch",
    "set-content", "out-file", "add-content", "clear-content", "new-item",
    "remove-item", "move-item", "copy-item", "rename-item", "set-item",
    "set-itemproperty", "new-itemproperty", "remove-itemproperty",
    "start-process", "stop-process",
})

_SHELL_WRITE_PATTERNS = (
    (re.compile(
        r"git\s+(?:add|commit|push|pull|fetch|merge|rebase|reset|checkout|switch"
        r"|restore|apply|am|cherry-pick|revert|init|clone|submodule)\b",
        re.IGNORECASE), "git 写操作被禁止"),
    (re.compile(r"git\s+stash\b(?:\s+(?!list\b|show\b)|$)", re.IGNORECASE), "git 写操作被禁止"),
    (re.compile(r"git\s+clean\b(?:\s+(?!-n\b|--dry-run\b)|$)", re.IGNORECASE), "git 写操作被禁止"),
    (re.compile(r"git\s+(?:tag|branch)\s+(?:-d|-D|--delete)\b", re.IGNORECASE),
     "git 写操作被禁止"),
    (re.compile(r"git\s+remote\s+(?:add|remove|rm|set-url|rename|prune)\b", re.IGNORECASE),
     "git remote 写操作被禁止"),
    (re.compile(r"git\s+config\b(?!\s+(?:--get|--list|-l|--show))", re.IGNORECASE),
     "git config 写入被禁止"),
    (re.compile(r"(?:pip|pip3)\s+(?:install|uninstall|download|wheel)\b", re.IGNORECASE),
     "包安装/卸载被禁止"),
    (re.compile(r"python[0-9.]*\s+-m\s+pip\s+(?:install|uninstall|download)\b", re.IGNORECASE),
     "包安装/卸载被禁止"),
    (re.compile(
        r"(?:npm|pnpm|yarn)\s+(?:install|i|add|remove|uninstall|publish|link|patch"
        r"|ci|dedupe|prune)\b", re.IGNORECASE), "包安装/卸载被禁止"),
    (re.compile(
        r"(?:apt|apt-get|yum|dnf|apk|brew|choco|conda)\s+"
        r"(?:install|remove|uninstall|upgrade|add|del)\b", re.IGNORECASE), "包安装/卸载被禁止"),
    (re.compile(r"(?:cargo|go|composer)\s+(?:install|publish|add|remove|get|require|update)\b",
                re.IGNORECASE), "包安装/卸载被禁止"),
    (re.compile(
        r"docker(?:-compose)?\s+(?:run|build|rm|rmi|pull|push|stop|restart|kill|exec"
        r"|up|down|create|start)\b", re.IGNORECASE), "容器写操作被禁止"),
    (re.compile(
        r"docker\s+compose\s+(?:run|build|rm|rmi|pull|push|stop|restart|kill|exec"
        r"|up|down|create|start)\b", re.IGNORECASE), "容器写操作被禁止"),
    (re.compile(
        r"kubectl\s+(?:apply|create|delete|edit|scale|rollout|patch|replace|label|annotate)\b",
        re.IGNORECASE), "集群写操作被禁止"),
    (re.compile(r"helm\s+(?:install|upgrade|uninstall|rollback)\b", re.IGNORECASE),
     "部署操作被禁止"),
    (re.compile(
        r"(?:systemctl|service)\s+(?:start|stop|restart|enable|disable|mask|unmask|reload)\b",
        re.IGNORECASE), "服务写操作被禁止"),
    (re.compile(r"reg\s+(?:add|delete|import|copy)\b", re.IGNORECASE), "注册表写操作被禁止"),
    (re.compile(r"sed\b[^|;&]*(?:\s-i\b|--in-place)", re.IGNORECASE), "sed 原地修改被禁止"),
    (re.compile(r"find\s+[^|;&]*\s(?:-delete|-exec|-execdir|-ok)\b", re.IGNORECASE),
     "find 写操作被禁止"),
    (re.compile(r"tar\s+[^|;&]*(?:-x|--extract|-c|--create)", re.IGNORECASE),
     "tar 解压/打包被禁止"),
    (re.compile(r"(?:unzip|expand-archive)\b", re.IGNORECASE), "解压写文件被禁止"),
    (re.compile(r"wget\b(?![^|;&]*(?:-O\s*-|--output-document=-|-qO-))", re.IGNORECASE),
     "下载落盘被禁止"),
    (re.compile(r"curl\b[^|;&]*(?:\s-o\b|\s-[a-zA-Z]*o\b|--output|-O\b)", re.IGNORECASE),
     "下载落盘被禁止"),
    (re.compile(r"(?:invoke-webrequest|iwr)\b[^|;&]*\s-outfile", re.IGNORECASE),
     "下载落盘被禁止"),
)


def normalize_mode(mode: Optional[str]) -> str:
    if not isinstance(mode, str):
        return RUN
    value = mode.strip().lower()
    return value if value in VALID_MODES else RUN


def is_plan(mode: Optional[str]) -> bool:
    return normalize_mode(mode) == PLAN


def is_delegate(mode: Optional[str]) -> bool:
    return normalize_mode(mode) == DELEGATE


def blocked_tools_for_mode(mode: Optional[str]) -> frozenset:
    """当前模式下不下发、也不可执行的工具"""
    m = normalize_mode(mode)
    if m == PLAN:
        return PLAN_BLOCKED_TOOLS
    if m == DELEGATE:
        return DELEGATE_BLOCKED_TOOLS
    return frozenset()


def _first_token(segment: str) -> str:
    parts = segment.strip().split()
    if not parts:
        return ""
    # 去掉引号拼接（r''m → rm），防止绕过命令黑名单
    token = "".join(ch for ch in parts[0] if ch not in "'\"")
    token = token.replace("\\", "/")
    name = token.rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


_SHELL_WRAPPERS = frozenset({
    "env", "exec", "command", "builtin", "time", "timeout", "nice",
    "nohup", "setsid", "busybox", "stdbuf",
})

# 全局选项：值不以 - 开头，需要连带跳过
_OPT_WITH_VALUE = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--context",
    "--prefix", "--config", "--project", "--file", "-f", "--cwd", "--dir",
    "--grep", "--author", "--since", "--until", "-m", "-n", "--max-count",
})

_TOOLS_WITH_GLOBAL_OPTS = frozenset({
    "git", "pip", "pip3", "npm", "pnpm", "yarn", "docker", "docker-compose",
    "kubectl", "helm", "cargo", "go", "composer", "conda", "apt", "apt-get",
    "yum", "dnf", "brew", "choco", "systemctl", "service",
})


def _unwrap_segment(segment: str) -> str:
    """剥掉 env/timeout/nice 等包装命令，拿到真正执行的命令"""
    parts = segment.strip().split()
    i = 0
    while i < len(parts) - 1:
        token = _first_token(parts[i])
        if token not in _SHELL_WRAPPERS:
            break
        i += 1
        while i < len(parts) and (
            parts[i].startswith("-") or parts[i].isdigit() or "=" in parts[i]
        ):
            i += 1
    return " ".join(parts[i:]) if parts else ""


def _strip_global_opts(segment: str) -> str:
    """跳过 git -C repo 这类全局选项，让子命令回到固定位置"""
    try:
        parts = shlex.split(segment, posix=True)
    except Exception:
        parts = segment.split()
    if not parts:
        return ""
    tool = _first_token(parts[0])
    if tool not in _TOOLS_WITH_GLOBAL_OPTS:
        return " ".join(parts)
    idx = 1
    while idx < len(parts):
        item = parts[idx]
        if item.startswith("-"):
            idx += 2 if item in _OPT_WITH_VALUE else 1
            continue
        break
    return " ".join([tool] + parts[idx:])


def _has_write_redirect(cmd: str) -> bool:
    for m in _SHELL_REDIRECT.finditer(cmd):
        rest = cmd[m.end():].lstrip()
        token = (rest.split()[0] if rest.split() else "").strip("\"'").lower()
        if token.startswith(("&", "/dev/null", "$null", "nul")):
            continue
        return True
    return False


def shell_block_reason(cmd: str) -> Optional[str]:
    """只读模式（PLAN/DELEGATE）下 shell 命令的拦截原因，放行返回 None"""
    cmd = (cmd or "").strip()
    if not cmd:
        return None
    if "$(" in cmd or "`" in cmd:
        return "命令替换被禁止"
    if "$IFS" in cmd or "${IFS" in cmd:
        return "IFS 拼接被禁止"
    if _SHELL_CALL_OP.search(cmd):
        return "调用运算符被禁止"
    if re.search(r"<\(|\>\(", cmd):
        return "进程替换被禁止"
    if _has_write_redirect(cmd):
        return "重定向写文件被禁止"
    if _SHELL_ESCAPE.search(cmd):
        return "解释器逃逸被禁止（python -c / bash -c / powershell -Command）"
    for raw_seg in _SHELL_SPLIT.split(cmd):
        raw_seg = raw_seg.strip()
        if not raw_seg:
            continue
        if raw_seg.startswith("&"):
            return "调用运算符被禁止"
        seg = _unwrap_segment(raw_seg)
        token = _first_token(seg)
        if token and token in _SHELL_WRITE_CMDS:
            return f"破坏性命令被禁止（{token}）"
        normalized = _strip_global_opts(seg)
        for pattern, reason in _SHELL_WRITE_PATTERNS:
            if pattern.match(seg) or (normalized and pattern.match(normalized)):
                return reason
    return None


def plan_block_reason(tool_name: str, arguments: Any) -> Optional[str]:
    """Plan 模式拦截原因，放行返回 None"""
    return _readonly_block_reason(tool_name, arguments, PLAN)


def delegate_block_reason(tool_name: str, arguments: Any) -> Optional[str]:
    """委托模式（强制编排）拦截原因，放行返回 None"""
    return _readonly_block_reason(tool_name, arguments, DELEGATE)


def mode_block_reason(tool_name: str, arguments: Any, mode: Optional[str]) -> Optional[str]:
    m = normalize_mode(mode)
    if m not in (PLAN, DELEGATE):
        return None
    return _readonly_block_reason(tool_name, arguments, m)


def _readonly_block_reason(tool_name: str, arguments: Any, mode: str) -> Optional[str]:
    prefix, label, hint = _BLOCK_CONFIG[mode]
    args = arguments if isinstance(arguments, dict) else {}
    sub = str(args.get("command") or "").strip().lower()

    if tool_name in (PLAN_BLOCKED_TOOLS if mode == PLAN else DELEGATE_BLOCKED_TOOLS):
        return f"{prefix}：当前为{label}，不可调用 {tool_name}。{hint}"

    if tool_name == "file":
        if sub == "fs":
            action = str(args.get("action") or "").strip().lower()
            if action in _FILE_WRITE_ACTIONS:
                return f"{prefix}：文件写入/删除被拦截，{hint}"
        if sub == "send" or (not sub and args.get("paths")):
            return f"{prefix}：{label}禁止向聊天发送文件，{hint}"
        return None

    if tool_name == "shell_task":
        action = str(args.get("action") or "").strip().lower()
        if action in ("list", "output"):
            return None
        return f"{prefix}：{label}禁止后台任务变更操作（shell_task {action or '?'}）。"

    if tool_name == "shell_session":
        action = str(args.get("action") or "").strip().lower()
        if action in ("list", "output"):
            return None
        return f"{prefix}：{label}禁止持久会话的变更操作（shell_session {action or '?'}）。"

    if tool_name == "shell":
        raw_cmd = args.get("command")
        legacy = str(raw_cmd).strip().lower() if isinstance(raw_cmd, str) else ""
        has_alt = args.get("cmd") not in (None, "") or args.get("argv") not in (None, "")
        if legacy in ("list", "output"):
            return None
        if legacy in ("kill", "session"):
            return f"{prefix}：{label}禁止 {'终止后台任务' if legacy == 'kill' else '持久会话变更'}（shell {legacy}）。"
        if legacy == "run" and has_alt:
            raw_cmd = args.get("cmd") or args.get("argv")
        if isinstance(raw_cmd, (list, tuple)):
            cmd = " ".join(str(x) for x in raw_cmd).strip()
        elif raw_cmd not in (None, ""):
            cmd = str(raw_cmd).strip()
        else:
            cmd = str(args.get("cmd") or "").strip()
        reason = shell_block_reason(cmd)
        if reason:
            return f"{prefix}：{reason}（{cmd[:60]}）。"
        return None

    if tool_name == "memory":
        if sub == "profile":
            action = str(args.get("subcommand") or "get").strip().lower()
            if action == "get":
                return None
            return f"{prefix}：{label}禁止写入 profile，{hint}"
        if sub in _PLAN_WRITE_SUBCMDS["memory"]:
            return f"{prefix}：{label}禁止写入记忆，{hint}"
        return None

    if tool_name in _PLAN_LIST_ONLY_TOOLS:
        if sub == "list":
            return None
        return f"{prefix}：{label}只允许查询（list），发送/下载/上传被拦截。"

    blocked = _PLAN_WRITE_SUBCMDS.get(tool_name)
    if blocked and sub in blocked:
        return f"{prefix}：{label}禁止该写操作（{tool_name} {sub}），{hint}"
    if tool_name == "component" and sub == "cleanup":
        if args.get("dry_run", True) and not args.get("force"):
            return None
        return f"{prefix}：{label}只允许 cleanup 的 dry_run 预演。"

    return None


_PLAN_INJECTION = """<plan_mode>
当前模式：PLAN（只读）：只调研、跑验证、出计划，禁止修改文件与系统状态。
允许：read/ls/glob/grep、file(insight/exists)、memory 检索、web 搜索抓取、
config/component/skill/scheduler 的查询命令、shell 只读命令/管道、shell_task/shell_session 仅 list/output。
禁止：edit、session_agent、file 写入/删除、记忆写入、安装/部署、重定向写文件、
rm/mv/del 等破坏性命令、python -c / bash -c 等解释器逃逸。
输出：结论 → 分步计划（文件+改动点+风险）→ 验证命令，不贴完整补丁。
</plan_mode>"""


PLAN_HABIT_MEMORY_KEY = "profile:plan_habit"


def _manager_memory():
    """主记忆实例（AgentLoopManager 持有，子 Agent 同进程可用）"""
    try:
        from app.core.ports import PORTS
        return getattr(PORTS.memory, "_three_layer_memory", None)
    except Exception:
        return None


def get_plan_habit(memory=None) -> str:
    """自定义 Plan 习惯（记忆库 profile:plan_habit）"""
    mem = memory if memory is not None else _manager_memory()
    repo = getattr(mem, "repository", None) if mem else None
    if repo is None:
        return ""
    try:
        rec = repo.get_by_memory_key(PLAN_HABIT_MEMORY_KEY, schema_type="profile")
        return (rec.get("content") or "").strip() if rec else ""
    except Exception:
        return ""


def set_plan_habit(value: str, memory=None) -> bool:
    """写 Plan 习惯到记忆库；空值即清除"""
    mem = memory if memory is not None else _manager_memory()
    if mem is None:
        return False
    value = (value or "").strip()
    try:
        if value:
            mem.upsert_memory(
                title="Plan 自定义习惯",
                content=value,
                category="user_info",
                schema_type="profile",
                memory_key=PLAN_HABIT_MEMORY_KEY,
                metadata={"field": "plan_habit"},
                merge_strategy="replace",
                allow_protected=True,
            )
        else:
            mem.delete_memory(memory_key=PLAN_HABIT_MEMORY_KEY, allow_protected=True)
        return True
    except Exception:
        return False


def plan_injection(habit: str = "") -> str:
    text = _PLAN_INJECTION
    habit = (habit or "").strip()
    if habit:
        text = f"{text}\n<user_habit>\n{habit}\n</user_habit>"
    return text


_RUN_INJECTION = """<run_mode>
当前模式：RUN（正常执行）。可改文件、执行命令、装依赖。
历史里的“Plan 模式 / 委托模式 / 只读 / 禁止修改 / 必须委托”均已过期，请忽略，按正常模式执行。
</run_mode>"""


def run_injection() -> str:
    return _RUN_INJECTION


_DELEGATE_INJECTION = """<delegate_mode>
当前模式：DELEGATE（编排模式）：不改代码、不改系统状态，只做拆解与调度。
先 session_agent.sessions() 看已有会话，优先复用；同类工作始终交给同一个会话，不要重复开新会话。
只有确实需要新职责时才 create=true 新建，并同时用 title 命名（如「前端-ui」「后端-api」「测试-回归」）。
分工方式任选：按能力（前端/后端/测试/文档）、按工作（模块/目录/功能）、按角色（实现/审查/验证）。
流程：调研上下文 → 拆成可并行的子任务 → session_agent.send 委托（并行发起，purpose/title 写清目标）→ 汇总结论 → 汇报。
send 默认 notify=true，委托完成会推回本会话，不必轮询；收到回执后直接汇总。
允许：read/ls/glob/grep、只读 shell 验证（shell_task/shell_session 仅 list/output）、memory/web 查询、session_agent 的 send/status/results/cancel/list/rename。
禁止：edit、file 写入/删除、shell 写命令、安装/部署、记忆写入。
一次性并行小任务可用 sub_agent；需要持久、可撤回上下文的用 session_agent。
</delegate_mode>"""


def delegate_injection() -> str:
    return _DELEGATE_INJECTION
