# -*- coding: utf-8 -*-
"""Agent 工作目录"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_workdir: Optional[str] = None


def resolve_workdir(raw: str = None) -> Optional[str]:
    """解析配置里的 shell_cwd

    - 空 → None（调用方用启动目录）
    - 绝对路径 → 直接使用
    - 相对路径 → 按启动目录解析（不再拼到 config_root 下）
    - 目录不存在则尝试创建；失败回退 None
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    candidate = raw if os.path.isabs(raw) else os.path.join(os.getcwd(), raw)
    candidate = os.path.abspath(candidate)
    if os.path.isdir(candidate):
        return candidate
    try:
        os.makedirs(candidate, exist_ok=True)
        return candidate
    except Exception as e:
        logger.warning("[Workdir] 工作目录不可用: %s (%s)，回退启动目录", candidate, e)
        return None


def set_workdir(path: Optional[str]) -> str:
    global _workdir
    _workdir = os.path.abspath(path) if path else None
    return get_workdir()


def get_workdir() -> str:
    return _workdir or os.getcwd()


def resolve_path(path: str = None) -> str:
    """统一路径解析：空=工作目录，相对=按工作目录解析"""
    if not path:
        return get_workdir()
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(get_workdir(), path))
