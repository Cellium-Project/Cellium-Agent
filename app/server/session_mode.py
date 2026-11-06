# -*- coding: utf-8 -*-
"""会话级 Plan/Run 模式：TUI 与 WebUI 共享同一状态并实时广播"""

from typing import Dict, Optional

from app.core.security.run_mode import normalize_mode

_modes: Dict[str, str] = {}


def get_mode(session_id: str) -> str:
    return _modes.get(session_id or "default", "run")


def set_mode(session_id: str, mode: Optional[str], broadcast: bool = True) -> str:
    sid = session_id or "default"
    value = normalize_mode(mode)
    _modes[sid] = value
    if broadcast:
        _broadcast(sid, value)
    return value


def _broadcast(session_id: str, mode: str) -> None:
    payload = {"type": "mode_changed", "mode": mode, "session_id": session_id}
    try:
        from app.messaging.message_broker import get_message_broker
        get_message_broker().publish(session_id, dict(payload))
    except Exception:
        pass
    try:
        from app.server.routes.ws_event_manager import ws_publish_event
        ws_publish_event("chat_event", dict(payload), session_id=session_id)
    except Exception:
        pass
