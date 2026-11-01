# -*- coding: utf-8 -*-
"""fork 安全防护：子进程 fork 后关闭继承的监听 socket

仅在 Linux（依赖 /proc/self/fd）生效，其它平台自动跳过。
设置环境变量 CELLIUM_FORK_GUARD=0 可关闭。
"""

import os
import socket

_ENV_DISABLE = "CELLIUM_FORK_GUARD"

_installed = False


def _close_inherited_listeners() -> None:
    try:
        entries = os.listdir("/proc/self/fd")
    except OSError:
        return

    for entry in entries:
        try:
            fd = int(entry)
        except ValueError:
            continue

        probe = None
        is_listen = False
        try:
            probe = socket.socket(fileno=fd)
            is_listen = probe.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        except Exception:
            is_listen = False
        finally:
            if probe is not None:
                try:
                    probe.detach()
                except Exception:
                    pass

        if not is_listen:
            continue
        try:
            os.close(fd)
        except OSError:
            pass


def install() -> bool:
    """注册 fork 子进程回调，重复调用不会重复注册"""
    global _installed
    if _installed:
        return False
    if os.environ.get(_ENV_DISABLE, "1").strip().lower() in ("0", "false", "off"):
        return False
    register = getattr(os, "register_at_fork", None)
    if register is None:
        return False
    register(after_in_child=_close_inherited_listeners)
    _installed = True
    return True
