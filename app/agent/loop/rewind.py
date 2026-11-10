# -*- coding: utf-8 -*-
"""消息撤回编排"""

import logging

from app.agent.loop.session_manager import get_session_manager
from app.agent.loop.session_store import get_session_store
from app.messaging.message_broker import get_message_broker

logger = logging.getLogger(__name__)


class RewindError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _locate_target(messages, message_id: str) -> int:
    index = next((i for i, msg in enumerate(messages) if msg.get("id") == message_id), -1)
    if index < 0:
        raise RewindError("not_found", "消息不存在或不属于该会话")
    if messages[index].get("role") != "user":
        raise RewindError("not_user", "只能撤回用户消息")
    boundary = next(
        (i for i, msg in enumerate(messages) if msg.get("_is_compacted_notes")), -1
    )
    if boundary >= 0 and index <= boundary:
        raise RewindError("compacted", "该消息已被压缩，无法撤回")
    return index


def _keep_user_ids(messages) -> list:
    return [
        msg["id"] for msg in messages
        if msg.get("role") == "user" and not msg.get("_is_compacted_notes") and msg.get("id")
    ]


def tracked_user_ids(session_id: str) -> set:
    """有文件追踪记录的轮次（turn_id = 该轮用户消息 id）"""
    try:
        from app.core.util.undo_store import get_undo_store
        return get_undo_store().tracked_turn_ids(session_id)
    except Exception:
        return set()


def has_tracking(session_id: str, message_id: str) -> bool:
    try:
        from app.core.util.undo_store import get_undo_store
        return get_undo_store().has_tracking(session_id, message_id)
    except Exception:
        return False


def rewindable_user_ids(session_id: str) -> set:
    """真正可撤回的用户消息：有文件追踪且未被压缩"""
    tracked = tracked_user_ids(session_id)
    if not tracked:
        return set()
    try:
        messages = get_session_manager().get_or_create(session_id).memory.messages
    except Exception:
        return tracked
    boundary = max(
        (i for i, m in enumerate(messages) if m.get("_is_compacted_notes")), default=-1
    )
    return {
        m["id"] for i, m in enumerate(messages)
        if i > boundary
        and m.get("role") == "user"
        and m.get("id")
        and m["id"] in tracked
    }


def preview_rewind(session_id: str, message_id: str) -> dict:
    manager = get_session_manager()
    messages = manager.get_or_create(session_id).memory.messages
    index = _locate_target(messages, message_id)
    files = []
    skipped = []
    warning = None
    try:
        from app.core.util.undo_store import get_undo_store
        result = get_undo_store().preview(session_id, _keep_user_ids(messages[:index]))
        files = result.get("files", [])
        skipped = result.get("skipped", [])
        warning = result.get("warning")
    except Exception as e:
        logger.warning("[Rewind] 文件预览失败 | session=%s | error=%s", session_id, e)
        warning = f"文件预览失败: {e}"
    return {
        "success": True,
        "removed_count": len(messages) - index,
        "files": files,
        "files_skipped": skipped,
        "warning": warning,
    }


def rewind_session(session_id: str, message_id: str) -> dict:
    manager = get_session_manager()
    info = manager.get_or_create(session_id)
    memory = info.memory
    messages = memory.messages

    index = _locate_target(messages, message_id)

    removed_count = len(messages) - index
    memory.messages = messages[:index]
    memory.clear_ephemeral_messages()

    archive = getattr(getattr(manager, "three_layer_memory", None), "archive", None)
    if archive is not None:
        try:
            archive.delete_after(session_id, message_id)
        except Exception as e:
            logger.warning("[Rewind] 归档清理失败 | session=%s | error=%s", session_id, e)

    try:
        store = get_session_store()
        meta = store.get_or_create_session(session_id)
        delta = len(memory.messages) - int(meta.message_count or 0)
        if delta:
            store.update_message_count(session_id, delta=delta)
    except Exception as e:
        logger.warning("[Rewind] 更新消息数失败 | session=%s | error=%s", session_id, e)

    files_restored = 0
    files_skipped = []
    files = []
    warning = None
    try:
        from app.core.util.undo_store import get_undo_store
        undo = get_undo_store()
        result = undo.rollback(session_id, _keep_user_ids(memory.messages))
        if isinstance(result, dict):
            files_restored = result.get("files_restored", result.get("restored", 0))
            files_skipped = result.get("files_skipped", result.get("skipped", []))
            files = result.get("files", [])
            warning = result.get("warning")
    except Exception as e:
        logger.warning("[Rewind] 文件回滚失败 | session=%s | error=%s", session_id, e)
        warning = f"文件回滚失败: {e}"

    try:
        get_message_broker().publish(session_id, {
            "type": "messages_rewound",
            "session_id": session_id,
            "message_id": message_id,
            "removed_count": removed_count,
        })
    except Exception as e:
        logger.warning("[Rewind] 撤回广播失败 | session=%s | error=%s", session_id, e)

    return {
        "success": True,
        "removed_count": removed_count,
        "files_restored": files_restored,
        "files_skipped": files_skipped,
        "files": files,
        "warning": warning,
    }
