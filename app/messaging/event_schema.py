# -*- coding: utf-8 -*-
"""规范事件类型常量与构建函数"""

EVENT_TYPES = {"thinking", "content", "tool_start", "tool_result", "done", "error"}


def norm_event(type_, session_id, **data):
    """构建规范事件。type_ 必须是 EVENT_TYPES 之一。返回带 type/session_id 的 dict。"""
    if type_ not in EVENT_TYPES:
        raise ValueError(f"非法事件类型: {type_}")
    event = {"type": type_, "session_id": session_id}
    event.update(data)
    return event
