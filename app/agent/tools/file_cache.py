# -*- coding: utf-8 -*-

import os
from collections import OrderedDict

_MAX_ENTRIES = 100
_read_file_cache = OrderedDict()


def cache_read(path: str, content: str, offset: int = None, limit: int = None):
    norm_path = os.path.normpath(path)
    _read_file_cache[norm_path] = {
        "path": norm_path,
        "content": content,
        "timestamp": _safe_mtime(norm_path),
        "offset": offset,
        "limit": limit,
    }
    _read_file_cache.move_to_end(norm_path)
    _evict_if_needed()


def get_read_state(path: str):
    norm_path = os.path.normpath(path)
    state = _read_file_cache.get(norm_path)
    if state:
        _read_file_cache.move_to_end(norm_path)
    return state


def is_file_read(path: str) -> bool:
    norm_path = os.path.normpath(path)
    state = _read_file_cache.get(norm_path)
    if not state:
        return False
    _read_file_cache.move_to_end(norm_path)
    try:
        return not _has_been_modified_externally(state)
    except Exception:
        return False


def is_partial_view(path: str) -> bool:
    norm_path = os.path.normpath(path)
    state = _read_file_cache.get(norm_path)
    if not state:
        return False
    return bool(state.get("offset") is not None or state.get("limit") is not None)


def touch_read_state(path: str, new_content: str):
    norm_path = os.path.normpath(path)
    entry = {
        "path": norm_path,
        "content": new_content,
        "timestamp": _safe_mtime(norm_path),
        "offset": None,
        "limit": None,
    }
    _read_file_cache[norm_path] = entry
    _read_file_cache.move_to_end(norm_path)
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
