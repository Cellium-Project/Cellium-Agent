# -*- coding: utf-8 -*-
"""OpenCode 网关 session 标识：按业务会话分配稳定 ID，落盘复用"""

import contextvars
import json
import os
import secrets
import string
import threading

_ALPHABET = string.ascii_letters + string.digits
_LOCK = threading.RLock()
_STORE = {}
_MTIME = 0.0
_STORE_PATH = None
_CURRENT = contextvars.ContextVar("gateway_session_id", default="")


def _path() -> str:
    if _STORE_PATH:
        return _STORE_PATH
    from app.core.util.runtime_paths import resolve_dir_writable
    return os.path.join(resolve_dir_writable("workspace"), ".cache", "gateway_session.json")


def _mtime() -> float:
    try:
        return os.path.getmtime(_path())
    except OSError:
        return 0.0


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception:
        return {}


def _refresh() -> None:
    global _STORE, _MTIME
    m = _mtime()
    if m == _MTIME:
        return
    with _LOCK:
        if m == _MTIME:
            return
        _STORE = _load()
        _MTIME = m


def _save() -> None:
    global _MTIME
    path = _path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with _LOCK:
            payload = dict(_STORE)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        _MTIME = _mtime()
    except Exception:
        pass


def _new_id() -> str:
    return "ses_" + secrets.token_hex(6) + "".join(
        secrets.choice(_ALPHABET) for _ in range(10)
    )


def gateway_id(session_id: str) -> str:
    """返回该业务会话的稳定网关 session ID，不存在则生成并落盘"""
    key = str(session_id or "global").strip() or "global"
    _refresh()
    with _LOCK:
        existing = _STORE.get(key)
        if existing:
            return existing
        _STORE[key] = _new_id()
        value = _STORE[key]
    _save()
    return value


def bind(session_id: str) -> None:
    _CURRENT.set(str(session_id or ""))


def current() -> str:
    return _CURRENT.get()


def header_value() -> str:
    """当前请求应使用的 X-Opencode-Session；未绑定会话时返回空串"""
    sid = _CURRENT.get()
    return gateway_id(sid) if sid else ""


def clear_session(session_id: str) -> None:
    key = str(session_id or "")
    if not key:
        return
    _refresh()
    with _LOCK:
        if key not in _STORE:
            return
        _STORE.pop(key, None)
    _save()


_STORE = _load()
_MTIME = _mtime()
