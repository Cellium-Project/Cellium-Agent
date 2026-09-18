# -*- coding: utf-8 -*-
import hashlib
import json


def message_identity(msg: dict):
    content = msg.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, sort_keys=True) if content else ""
    tool_calls = msg.get("tool_calls")
    return (
        msg.get("role", ""),
        hashlib.md5(content.encode("utf-8")).hexdigest(),
        msg.get("tool_call_id", ""),
        json.dumps(tool_calls, ensure_ascii=False, sort_keys=True) if tool_calls else "",
    )


def resolve_anchor_index(messages: list, anchor, fallback: int = 0) -> int:
    total = len(messages)
    if total <= 0:
        return 0
    fallback = max(0, min(int(fallback or 0), total))
    if anchor is None:
        return fallback
    if fallback < total and message_identity(messages[fallback]) == anchor:
        return fallback
    for idx in range(total):
        if message_identity(messages[idx]) == anchor:
            return idx
    return fallback


def plan_slice(messages: list, start_index: int, end_index: int) -> list:
    from app.tui.history_render import build_assistant_timeline
    page = messages[start_index:end_index]
    plan = []
    i = 0
    while i < len(page):
        m = page[i]
        role = m.get("role")
        if role == "user":
            content = m.get("content")
            if content:
                span = (start_index + i, start_index + i + 1)
                plan.append({"kind": "user", "content": content, "span": span})
            i += 1
            continue
        if role != "assistant":
            i += 1
            continue
        content = m.get("content")
        if not content and not m.get("tool_calls"):
            i += 1
            continue
        built = build_assistant_timeline(m, page, i)
        span = (start_index + i, start_index + built["end_index"])
        i = built["end_index"]
        tool_card = None
        for seg in built["timeline"]:
            kind = seg["kind"]
            if kind == "thinking":
                if tool_card is not None:
                    plan.append(tool_card)
                    tool_card = None
                plan.append({"kind": "thinking", "content": seg["content"], "span": span})
            elif kind == "reasoning":
                if tool_card is not None:
                    plan.append(tool_card)
                    tool_card = None
                plan.append({
                    "kind": "reasoning",
                    "content": seg["content"],
                    "duration_ms": seg.get("duration_ms", 0),
                    "span": span,
                })
            elif kind == "tool":
                if tool_card is None:
                    tool_card = {"kind": "tool", "calls": [], "span": span}
                tool_card["calls"].append({
                    "tool": seg["tool"],
                    "arguments": seg.get("arguments", {}),
                    "result": seg.get("result"),
                    "duration_ms": seg.get("duration_ms", 0),
                })
            else:
                if tool_card is not None:
                    plan.append(tool_card)
                    tool_card = None
                c = seg.get("content", "")
                if c and c.strip():
                    plan.append({"kind": "assistant", "content": c, "span": span})
        if tool_card is not None:
            plan.append(tool_card)
    return plan


def build_history_plan_range(session_id: str, end_index: int = None, limit: int = 100, messages: list = None) -> tuple:
    from app.tui.history_render import load_session_messages
    if messages is None:
        messages = load_session_messages(session_id, limit=10000)
    total = len(messages)
    if total <= 0:
        return [], {"start": 0, "end": 0, "total": 0, "anchor": None, "has_more": False}
    if end_index is None:
        end_index = total
    end_index = max(0, min(int(end_index), total))
    start_index = max(0, end_index - max(1, int(limit)))
    if start_index >= end_index:
        anchor = message_identity(messages[start_index]) if start_index < total else None
        return [], {"start": start_index, "end": end_index, "total": total, "anchor": anchor, "has_more": start_index > 0}
    meta = {
        "start": start_index,
        "end": end_index,
        "total": total,
        "anchor": message_identity(messages[start_index]),
        "has_more": start_index > 0,
    }
    return plan_slice(messages, start_index, end_index), meta
