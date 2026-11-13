# -*- coding: utf-8 -*-
"""进程级视觉能力缓存：记录哪些模型不支持图片输入"""

import threading

_lock = threading.Lock()
_unsupported: set = set()


def mark_unsupported(model: str):
    with _lock:
        _unsupported.add(str(model or ""))


def is_unsupported(model: str) -> bool:
    with _lock:
        return str(model or "") in _unsupported


def clear(model: str = None):
    with _lock:
        if model is None:
            _unsupported.clear()
        else:
            _unsupported.discard(str(model or ""))
