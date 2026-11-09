# -*- coding: utf-8 -*-
"""会话委托的跨热重载/跨进程持久化状态"""

import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict

STORE_PATH = None

_FIELDS = (
    "task_id", "parent", "target", "message", "purpose", "status",
    "started_at", "updated_at", "finished_at", "cancel_requested", "notify",
    "timeout", "current_tool", "tool_calls", "last_tool", "last_result_preview",
    "result_content", "error", "turn_id", "interrupted",
)
_MAX_STORED_RESULT = 4000
_ACTIVE_STATUSES = ("queued", "running", "cancelling")

DELEGATIONS: Dict[str, Dict[str, Any]] = {}
DELEGATED_ACTIVE: set = set()
PENDING_NOTIFY: Dict[str, deque] = {}
NOTIFY_WORKERS: Dict[str, Any] = {}
LOCK = threading.RLock()


def _path() -> str:
    if STORE_PATH:
        return STORE_PATH
    from app.core.util.runtime_paths import resolve_dir_writable
    return os.path.join(resolve_dir_writable("workspace"), ".cache", "delegations.json")


def _serialize(info: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key in _FIELDS:
        value = info.get(key)
        if key == "result_content" and isinstance(value, str):
            value = value[:_MAX_STORED_RESULT]
        out[key] = value
    return out


def save() -> None:
    path = _path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with LOCK:
            payload = {"delegations": {k: _serialize(v) for k, v in DELEGATIONS.items()}}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def load() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    records = data.get("delegations") if isinstance(data, dict) else None
    if not isinstance(records, dict):
        return {}
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in records.items():
        if not isinstance(value, dict):
            continue
        info = dict(value)
        # 重启后进程里的后台任务已经不存在，标记为中断而不是继续 running
        if info.get("status") in _ACTIVE_STATUSES:
            info.update({
                "status": "unknown",
                "interrupted": True,
                "error": "委托进程已重启，任务中断",
                "finished_at": info.get("updated_at") or info.get("started_at") or now,
                "cancel_requested": False,
            })
        out[str(key)] = info
    return out


# 进程启动即恢复历史记录，供 status/results 查询
DELEGATIONS = load()
save()
