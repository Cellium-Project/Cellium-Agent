# -*- coding: utf-8 -*-
import json
import os
import re

def _split_inline_json(content: str) -> list:
    result = []
    brace_start = content.find("{")
    if brace_start == -1:
        if content.strip():
            result.append({"type": "text", "content": content})
        return result

    depth = 0
    json_end = -1
    for i in range(brace_start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                json_str = content[brace_start:i + 1]
                try:
                    data = json.loads(json_str)
                    if isinstance(data, dict) and "reasoning" in data and "action" in data:
                        if brace_start > 0:
                            before = content[:brace_start].strip()
                            if before:
                                result.append({"type": "text", "content": before})
                        result.append({"type": "thinking", "content": json.dumps(data, ensure_ascii=False, indent=2)})
                        after = content[i + 1:].strip()
                        if after:
                            result.extend(_split_inline_json(after))
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass
                break

    if content.strip():
        result.append({"type": "text", "content": content})
    return result


def split_content_with_thinking(content: str) -> list:
    if not content:
        return []

    result = []
    last_end = 0

    think_pat = re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL)
    for match in think_pat.finditer(content):
        if match.start() > last_end:
            text_part = content[last_end:match.start()].strip()
            if text_part:
                result.extend(_split_inline_json(text_part))
        result.append({"type": "thinking", "content": match.group(1).strip()})
        last_end = match.end()

    rest = content[last_end:] if last_end > 0 else content

    json_block_pattern = re.compile(r"```json\s*([\s\S]*?)\s*```", re.IGNORECASE)
    last_end = 0

    for match in json_block_pattern.finditer(rest):
        if match.start() > last_end:
            text_part = rest[last_end:match.start()].strip()
            if text_part:
                result.extend(_split_inline_json(text_part))

        json_str = match.group(1).strip()
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "reasoning" in data and "action" in data:
                result.append({"type": "thinking", "content": json.dumps(data, ensure_ascii=False, indent=2)})
            else:
                result.append({"type": "text", "content": match.group(0)})
        except (json.JSONDecodeError, ValueError):
            result.append({"type": "text", "content": match.group(0)})

        last_end = match.end()

    if last_end < len(rest):
        text_part = rest[last_end:].strip()
        if text_part:
            result.extend(_split_inline_json(text_part))

    return result


def load_session_messages(session_id: str, limit: int = 500, record_cap: int = 0) -> list:
    from app.agent.loop.session_manager import get_session_manager
    mgr = get_session_manager()
    raw_msgs = []
    archive = None

    if mgr and mgr.three_layer_memory:
        archive = mgr.three_layer_memory.archive
    if archive is None:
        try:
            from app.agent.memory.three_layer import ThreeLayerMemory
            from app.core.di.container import get_container
            container = get_container()
            if container.has(ThreeLayerMemory):
                archive = container.resolve(ThreeLayerMemory).archive
        except Exception:
            archive = None
    if archive is None:
        try:
            from app.agent.memory.archive_store import ArchiveStore
            from app.core.util.runtime_paths import resolve_dir_writable
            base = os.path.join(resolve_dir_writable("memory"), "archive")
            archive = ArchiveStore(str(base))
        except Exception:
            archive = None

    try:
        if archive:
            records = archive.get_by_session(session_id, limit=limit)
            if records:
                all_messages = []
                seen = set()
                for rec in reversed(records):
                    msgs = rec.get("messages")
                    if isinstance(msgs, list):
                        filtered = [
                            m for m in msgs
                            if not (m.get("role") == "user" and m.get("_is_compacted_notes"))
                        ]
                        for msg in reversed(filtered):
                            msg_key = json.dumps(msg, sort_keys=True, ensure_ascii=False)
                            if msg_key not in seen:
                                seen.add(msg_key)
                                all_messages.append(msg)
                raw_msgs = list(reversed(all_messages))
    except Exception:
        raw_msgs = []

    if not raw_msgs:
        try:
            info = mgr.get_or_create(session_id)
            raw_msgs = info.memory.get_messages()
        except Exception:
            raw_msgs = []

    return raw_msgs


def build_assistant_timeline(msg: dict, raw_messages: list, index: int) -> dict:
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")
    timeline = []

    if tool_calls:
        tc_map = {}
        if content:
            for seg in split_content_with_thinking(content):
                if seg["type"] == "thinking":
                    timeline.append({"kind": "thinking", "content": seg["content"]})
                else:
                    timeline.append({"kind": "text", "content": seg["content"]})

        for tc in tool_calls:
            tc_id = tc.get("id", "")
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}")) if fn.get("arguments") else {}
            except (json.JSONDecodeError, ValueError):
                args = {}
            tc_map[tc_id] = {"tool": fn.get("name", "unknown"), "arguments": args}

        j = index + 1
        while j < len(raw_messages):
            sub = raw_messages[j]
            if sub.get("role") == "tool":
                tc_id = sub.get("tool_call_id", "")
                if tc_id in tc_map:
                    try:
                        result = json.loads(sub.get("content", "{}"))
                    except (json.JSONDecodeError, ValueError):
                        result = {"output": sub.get("content", "")}
                    tc_map[tc_id]["result"] = result
                j += 1
            elif sub.get("role") == "assistant" and sub.get("tool_calls"):
                break
            elif sub.get("role") == "assistant" and sub.get("content") and not sub.get("tool_calls"):
                break
            elif sub.get("role") == "user":
                break
            else:
                j += 1

        for tc_info in tc_map.values():
            args = tc_info["arguments"]
            result = tc_info.get("result")
            duration_ms = 0
            if result and isinstance(result, dict):
                duration_ms = result.get("elapsed_ms", 0) or result.get("duration_ms", 0) or 0
                try:
                    duration_ms = round(float(duration_ms)) if duration_ms else 0
                except (TypeError, ValueError):
                    duration_ms = 0
            timeline.append({
                "kind": "tool",
                "tool": tc_info["tool"],
                "arguments": args,
                "result": result,
                "duration_ms": duration_ms,
            })

        if j < len(raw_messages) and raw_messages[j].get("role") == "assistant":
            sub = raw_messages[j]
            if not sub.get("tool_calls") and sub.get("content"):
                final = sub.get("content") or ""
                for seg in split_content_with_thinking(final):
                    if seg["type"] == "text":
                        timeline.append({"kind": "text", "content": seg["content"]})
                j += 1

        return {"content": content or "", "timeline": timeline, "end_index": j}

    if content:
        for seg in split_content_with_thinking(content):
            if seg["type"] == "thinking":
                timeline.append({"kind": "thinking", "content": seg["content"]})
            else:
                timeline.append({"kind": "text", "content": seg["content"]})

    return {"content": content or "", "timeline": timeline, "end_index": index + 1}


def build_history_plan(session_id: str, limit: int = 100, offset: int = 0) -> tuple:

    messages = load_session_messages(session_id, limit=10000)
    if not messages:
        return [], 0
    total = len(messages)
    end_idx = total - offset
    start_idx = max(0, end_idx - limit)
    if start_idx >= end_idx:
        return [], 0
    dropped = max(0, start_idx)
    messages = messages[start_idx:end_idx]
    plan = []
    i = 0
    while i < len(messages):
        m = messages[i]
        role = m.get("role")
        if role == "user":
            content = m.get("content")
            if content:
                plan.append({"kind": "user", "content": content})
            i += 1
        elif role == "assistant":
            content = m.get("content")
            if not content and not m.get("tool_calls"):
                i += 1
                continue
            built = build_assistant_timeline(m, messages, i)
            i = built["end_index"]
            text_parts = []
            card = None
            for seg in built["timeline"]:
                kind = seg["kind"]
                if kind == "thinking":
                    plan.append({"kind": "thinking", "content": seg["content"]})
                elif kind == "tool":
                    if card is None:
                        card = {"kind": "tool", "calls": []}
                        plan.append(card)
                    card["calls"].append({
                        "tool": seg["tool"],
                        "arguments": seg.get("arguments", {}),
                        "result": seg.get("result"),
                        "duration_ms": seg.get("duration_ms", 0),
                    })
                else:
                    c = seg.get("content")
                    if c and c.strip():
                        text_parts.append(c)
            if text_parts:
                plan.append({"kind": "assistant", "content": "\n\n".join(text_parts)})
        else:
            i += 1
    return plan, dropped
