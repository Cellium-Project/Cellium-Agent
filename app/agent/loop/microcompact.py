# -*- coding: utf-8 -*-

import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

TRIMMABLE_TOOLS = {
    "shell", "file", "memory", "web_search", "web_fetch",
    "read_file", "file_read", "grep", "glob", "ls", "cat",
}


def _gather_trimmable_ids(messages: List[Dict]) -> List[str]:
    ids = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_name = tc.get("function", {}).get("name", "")
                if tool_name.lower() in TRIMMABLE_TOOLS:
                    tc_id = tc.get("id")
                    if tc_id:
                        ids.append(tc_id)
    return ids


def trim_old_tool_results(messages: List[Dict], keep_count: int = 10) -> List[Dict]:
    if not messages:
        return messages

    trimmable_ids = _gather_trimmable_ids(messages)
    if not trimmable_ids:
        return messages

    keep_set = set(trimmable_ids[-keep_count:])
    remove_set = set(cid for cid in trimmable_ids if cid not in keep_set)

    if not remove_set:
        return messages

    result = []
    removed_count = 0
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id") in remove_set:
            msg = {**msg, "content": "[历史工具结果已清理]"}
            removed_count += 1
        result.append(msg)

    if removed_count > 0:
        logger.debug(
            "[ToolResultTrim] 清理 %d 条旧结果 | 保留 %d 条 | 总消息 %d",
            removed_count, len(keep_set), len(messages),
        )

    return result
