# -*- coding: utf-8 -*-
"""会话运行状态判定（TUI / WebUI 共用）"""


def is_session_running(session_id: str) -> bool:
    if not session_id:
        return False
    try:
        from app.server.task_manager import get_task_manager
        if get_task_manager().has_running_task(session_id):
            return True
    except Exception:
        pass
    try:
        from components.session_agent import is_delegated
        if is_delegated(session_id):
            return True
    except Exception:
        pass
    return False
