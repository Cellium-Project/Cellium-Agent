# -*- coding: utf-8 -*-
"""Plan / Run 运行模式"""

import re
from typing import Any, Dict, Optional

PLAN = "plan"
RUN = "run"
VALID_MODES = (PLAN, RUN)

PLAN_TOOLS = frozenset({
    "read", "ls", "glob", "grep", "file", "memory", "shell",
    "web_search", "web_fetch", "sub_agent",
})

# 允许的验证/只读命令
_VERIFY_CMD = re.compile(
    r"""^(?:
        pytest(?:\s|$) |
        python3?(?:\.\d+)?\s+-m\s+(?:pytest|unittest)(?:\s|$) |
        (?:npm|pnpm|yarn)\s+test(?:\s|$) |
        (?:npm|pnpm|yarn)\s+run\s+(?:test|tests|lint|lint:check|typecheck|check)(?:\s|$) |
        npx\s+(?:jest|vitest|mocha)(?:\s|$) |
        (?:jest|vitest|mocha)(?:\s|$) |
        node\s+--test(?:\s|$) |
        go\s+(?:test|vet)(?:\s|$) |
        cargo\s+(?:test|check|clippy)(?:\s|$) |
        (?:ruff|flake8|mypy|pyright|eslint)(?:\s|$) |
        tsc\s+--noEmit(?:\s|$) |
        git\s+(?:status|diff|log|show|branch|rev-parse|blame|remote)(?:\s|$) |
        (?:ls|dir|rg|grep|type|cat|head|tail|findstr)(?:\s|$) |
        (?:Get-ChildItem|Get-Content|Select-String)(?:\s|$) |
        pip\s+(?:list|show)(?:\s|$) |
        (?:python3?|node|git)\s+--version$ |
        (?:npm|pnpm|yarn)\s+--version$
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# 串接/管道/重定向/命令替换
_SHELL_OPERATORS = re.compile(r"[;&|<>`]|\$\(|\n|\r")

PLAN_BLACKLISTED_MEMORY_CMDS = frozenset({
    "store", "update", "delete", "forget", "merge", "set_embedding", "start_embedding_migration",
})

_BLOCK_PREFIX = "Plan 模式禁止修改"


def normalize_mode(mode: Optional[str]) -> str:
    mode = (mode or "").strip().lower()
    return mode if mode in VALID_MODES else RUN


def is_plan(mode: Optional[str]) -> bool:
    return normalize_mode(mode) == PLAN


def is_verify_command(cmd: str) -> bool:
    cmd = (cmd or "").strip()
    if not cmd or _SHELL_OPERATORS.search(cmd):
        return False
    return bool(_VERIFY_CMD.match(cmd))


def plan_block_reason(tool_name: str, arguments: Any) -> Optional[str]:
    """Plan 模式拦截原因，放行返回 None"""
    args = arguments if isinstance(arguments, dict) else {}
    sub = str(args.get("command") or "").strip().lower()

    if tool_name not in PLAN_TOOLS:
        return f"{_BLOCK_PREFIX}：当前为只读计划模式，不可调用 {tool_name}。请把该动作写进计划。"

    if tool_name == "file":
        if sub == "insight":
            return None
        action = str(args.get("action") or "").strip().lower()
        if sub == "fs" and action == "exists":
            return None
        return f"{_BLOCK_PREFIX}：文件写入/删除被拦截，请把改动写进计划。"

    if tool_name == "shell":
        if sub in ("list", "output", "kill", "session"):
            return f"{_BLOCK_PREFIX}：Plan 模式只允许单次验证命令，禁止 session/kill 等操作。"
        if sub in ("", "run"):
            cmd = str(args.get("cmd") or "").strip()
        else:
            cmd = sub
        if not is_verify_command(cmd):
            return (
                f"{_BLOCK_PREFIX}：命令不在验证白名单内（{cmd[:60]}）。"
                "Plan 模式只允许 pytest/测试/lint/git status 等只读验证命令。"
            )
        return None

    if tool_name == "memory":
        if sub == "profile":
            action = str(args.get("subcommand") or "get").strip().lower()
            if action == "get":
                return None
            return f"{_BLOCK_PREFIX}：Plan 模式禁止写入 profile（含 Plan 习惯），请把结论写进计划。"
        if sub in PLAN_BLACKLISTED_MEMORY_CMDS:
            return f"{_BLOCK_PREFIX}：Plan 模式禁止写入记忆，请把结论写进计划。"
        if not sub:
            return f"{_BLOCK_PREFIX}：Plan 模式请明确指定只读子命令（如 search/list）。"
        return None

    return None


_PLAN_INJECTION = """<plan_mode>
当前模式：PLAN（只读）：只调研、跑验证、出计划，禁止修改。
可调研 read/ls/glob/grep/file(insight)/memory(检索)/web。
shell 仅单条验证命令（pytest、python -m pytest、npm test、npm run lint|typecheck、
go test、cargo test|check、ruff、mypy、eslint、tsc --noEmit、git status|diff|log），
禁串接/管道/重定向。
edit、file(fs create/delete/mkdir)、记忆写入等会被拒绝。
输出：结论 → 分步计划（文件+改动点+风险）→ 验证命令，不贴完整补丁。
</plan_mode>"""


PLAN_HABIT_MEMORY_KEY = "profile:plan_habit"


def _manager_memory():
    """主记忆实例（AgentLoopManager 持有，子 Agent 同进程可用）"""
    try:
        from app.agent.loop.agent_loop_manager import AgentLoopManager
        return getattr(AgentLoopManager.get_instance(), "_three_layer_memory", None)
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
历史里的“Plan 模式 / 只读 / 禁止修改”均已过期，请忽略，按正常模式执行。
</run_mode>"""


def run_injection() -> str:
    return _RUN_INJECTION
