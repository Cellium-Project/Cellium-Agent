# -*- coding: utf-8 -*-

import os

_read_file_cache = {}


def cache_read(path: str, content: str):
    _read_file_cache[path] = {
        "path": path,
        "content": content,
        "timestamp": os.path.getmtime(path),
    }


def get_read_state(path: str):
    return _read_file_cache.get(path)


def is_file_read(path: str) -> bool:
    state = _read_file_cache.get(path)
    if not state:
        return False
    try:
        return not _has_been_modified_externally(state)
    except Exception:
        return False


def _has_been_modified_externally(state: dict) -> bool:
    try:
        current_mtime = os.path.getmtime(state["path"])
    except OSError:
        return True
    if current_mtime <= state["timestamp"]:
        return False
    try:
        with open(state["path"], 'r', encoding='utf-8', errors='replace') as f:
            current_content = f.read()
        if state["content"] == current_content.replace('\r\n', '\n'):
            state["timestamp"] = current_mtime
            return False
    except Exception:
        pass
    return True
