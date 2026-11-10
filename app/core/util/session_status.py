# -*- coding: utf-8 -*-
"""会话运行状态判定（TUI / WebUI 共用）"""

from app.core.ports import PORTS


def is_session_running(session_id: str) -> bool:
    if not session_id:
        return False
    task = PORTS.task
    if task is not None:
        try:
            if task.has_running_task(session_id):
                return True
        except Exception:
            pass
    delegated = PORTS.delegated
    if delegated is not None:
        try:
            if delegated.is_delegated(session_id):
                return True
        except Exception:
            pass
    return False
