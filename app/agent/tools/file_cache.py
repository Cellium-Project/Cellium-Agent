# -*- coding: utf-8 -*-

import os
from collections import OrderedDict

_MAX_ENTRIES = 100
_read_file_cache = OrderedDict()


def _session_key(path: str, session_id: str = None) -> str:
    return f"{session_id or 'default'}::{os.path.normpath(path)}"


def cache_read(path: str, content: str, offset: int = None, limit: int = None, encoding: str = None, session_id: str = None):
    norm_path = os.path.normpath(path)
    key = _session_key(norm_path, session_id)
    _read_file_cache[key] = {
        "path": norm_path,
        "content": content,
        "timestamp": _safe_mtime(norm_path),
        "offset": offset,
        "limit": limit,
        "encoding": encoding,
    }
    _read_file_cache.move_to_end(key)
    _evict_if_needed()


def get_read_state(path: str, session_id: str = None):
    key = _session_key(path, session_id)
    state = _read_file_cache.get(key)
    if state:
        _read_file_cache.move_to_end(key)
    return state


def is_file_read(path: str, session_id: str = None) -> bool:
    key = _session_key(path, session_id)
    state = _read_file_cache.get(key)
    if not state:
        return False
    _read_file_cache.move_to_end(key)
    try:
        return not _has_been_modified_externally(state)
    except Exception:
        return False


def is_partial_view(path: str, session_id: str = None) -> bool:
    key = _session_key(path, session_id)
    state = _read_file_cache.get(key)
    if not state:
        return False
    return bool(state.get("offset") is not None or state.get("limit") is not None)


def touch_read_state(path: str, new_content: str, encoding: str = None, session_id: str = None):
    norm_path = os.path.normpath(path)
    key = _session_key(norm_path, session_id)
    entry = {
        "path": norm_path,
        "content": new_content,
        "timestamp": _safe_mtime(norm_path),
        "offset": None,
        "limit": None,
        "encoding": encoding,
    }
    _read_file_cache[key] = entry
    _read_file_cache.move_to_end(key)
    _evict_if_needed()


def clear_read_cache():
    _read_file_cache.clear()


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _has_been_modified_externally(state: dict) -> bool:
    try:
        current_mtime = os.path.getmtime(state["path"])
    except OSError:
        return True
    if current_mtime <= state["timestamp"]:
        return False
    is_full_read = state.get("offset") is None and state.get("limit") is None
    if is_full_read:
        try:
            with open(state["path"], 'r', encoding='utf-8', errors='replace') as f:
                current_content = f.read()
            if state["content"] == current_content.replace('\r\n', '\n'):
                state["timestamp"] = current_mtime
                return False
        except Exception:
            pass
    return True


def _evict_if_needed():
    while len(_read_file_cache) > _MAX_ENTRIES:
        _read_file_cache.popitem(last=False)
