# -*- coding: utf-8 -*-

import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def _find_user_message_positions(messages: List[Dict]) -> List[int]:
    positions = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user" and not msg.get("tool_call_id"):
            positions.append(i)
    return positions


def _is_compact_boundary(msg: Dict) -> bool:
    return msg.get("_is_compacted_notes") is True


def _find_system_messages(messages: List[Dict]) -> List[int]:
    positions = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            positions.append(i)
    return positions


def trim_old_rounds(messages: List[Dict], keep_rounds: int = 3) -> List[Dict]:
    if not messages:
        return messages

    user_positions = _find_user_message_positions(messages)
    if len(user_positions) <= keep_rounds:
        return messages

    system_positions = set(_find_system_messages(messages))
    compact_boundary_idx = None
    for i, msg in enumerate(messages):
        if _is_compact_boundary(msg):
            compact_boundary_idx = i
            break

    cut_position = user_positions[-keep_rounds]

    if compact_boundary_idx is not None and compact_boundary_idx < cut_position:
        cut_position = compact_boundary_idx + 1

    if cut_position <= 0:
        return messages

    result = []
    removed_count = 0
    for i, msg in enumerate(messages):
        if i < cut_position:
            if i in system_positions:
                result.append(msg)
            elif _is_compact_boundary(msg):
                result.append(msg)
            else:
                removed_count += 1
        else:
            result.append(msg)

    if removed_count > 0:
        logger.debug(
            "[RoundTrimmer] 裁剪 %d 条旧消息 | 保留 %d 轮 | 总消息 %d -> %d",
            removed_count, keep_rounds, len(messages), len(result),
        )

    return result
