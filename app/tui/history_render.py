# -*- coding: utf-8 -*-
import hashlib
import json
import os
import re

_NOTICE_HEADER_RE = re.compile(r"^\[(会话委托完成|子Agent完成)\]")


def is_internal_user_message(msg: dict) -> bool:
    """Agent 内部注入的 user 消息（如"回复为空重试"），不属于真实用户输入"""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    return isinstance(content, str) and content.lstrip().startswith("[系统")


def is_gene_phase_message(msg: dict) -> bool:
    """Gene 评估/写入阶段是内部流程，不应展示在历史里"""
    return (
        msg.get("role") == "system"
        and isinstance(msg.get("content"), str)
        and msg["content"].lstrip().startswith("[Gene")
    )


def is_delegation_notice(content) -> bool:
    text = (content or "").strip()
    if not _NOTICE_HEADER_RE.match(text):
        return False
    has_target = re.search(r"(^|\n)\s*(目标会话|名称)[:：]\s*\S", text)
    has_status = re.search(r"(^|\n)\s*状态[:：]\s*(成功|失败)", text)
    has_footer = "如需回滚文件" in text or "如需完整结果" in text
    return bool(has_target and has_status and has_footer)


def parse_delegation_notice(content) -> dict:
    lines = (content or "").strip().splitlines()
    header = (lines[0] if lines else "").strip("[]")
    info = {}
    body = []
    footer = ""
    in_body = False
    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            if in_body:
                body.append("")
            continue
        if line.startswith("结论:") or line.startswith("结论："):
            in_body = True
            rest = line[3:].strip()
            if rest:
                body.append(rest)
            continue
        if line.startswith("（") or line.startswith("如需完整结果"):
            footer = f"{footer}\n{line}" if footer else line
            continue
        match = re.match(r"^([^:：]{1,8})[:：]\s*(.*)$", line)
        if not in_body and match:
            info[match.group(1)] = match.group(2)
            continue
        body.append(raw)
    return {
        "title": info.get("目标会话") or info.get("名称") or re.sub(r"完成$", "", header),
        "header": header,
        "status": info.get("状态", ""),
        "elapsed": info.get("耗时", ""),
        "body": "\n".join(body).strip(),
        "error": info.get("错误", ""),
        "footer": footer,
    }


def _is_incomplete_thought_fragment(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if re.match(r"^```json\b", text, re.IGNORECASE):
        if '"reasoning"' in text and '"action"' in text:
            return True
        return "```" not in text[7:]
    if re.match(r'^\{\s*"reasoning\b', text):
        depth = 0
        in_string = False
        escaped = False
        for ch in text:
            if escaped:
                escaped = False
            elif ch == "\\" and in_string:
                escaped = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string and ch == "{":
                depth += 1
            elif not in_string and ch == "}":
                depth -= 1
        return depth > 0
    return False


def _is_thought_protocol_block(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if not (text.startswith("```") or text.startswith("{")):
        return False
    if "```" in text:
        return text.lstrip().lower().startswith("```json") and '"reasoning"' in text
    return re.match(r'^\{\s*"reasoning\b', text) is not None


def _extract_protocol_reasoning(content: str) -> str:
    text = (content or "").strip()
    m = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    payload = m.group(1) if m else text
    try:
        data = json.loads(payload)
    except Exception:
        match = re.search(r'"reasoning"\s*:\s*"((?:\\.|[^"\\])*)"', payload, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""
    if isinstance(data, dict):
        r = data.get("reasoning")
        if isinstance(r, str):
            return r.strip()
    return ""


_FENCE_LINE_RE = re.compile(r"^[ \t]*(```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _code_spans(content: str):
    spans = []
    start = None
    pos = 0
    for line in content.splitlines(keepends=True):
        if _FENCE_LINE_RE.match(line):
            if start is None:
                start = pos
            else:
                spans.append((start, pos + len(line)))
                start = None
        pos += len(line)
    if start is not None:
        spans.append((start, len(content)))
    for match in _INLINE_CODE_RE.finditer(content):
        spans.append((match.start(), match.end()))
    return spans


def _split_inline_json(content: str) -> list:
    spans = _code_spans(content)

    def _in_code(idx):
        return any(s <= idx < e for s, e in spans)

    search_from = 0
    while True:
        brace_start = content.find("{", search_from)
        if brace_start == -1:
            break
        if _in_code(brace_start):
            search_from = brace_start + 1
            continue

        depth = 0
        json_end = -1
        for i in range(brace_start, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    json_end = i
                    break
        if json_end == -1:
            break

        json_str = content[brace_start:json_end + 1]
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict) and "reasoning" in data and "action" in data:
            result = []
            before = content[:brace_start].strip()
            if before:
                result.append({"type": "text", "content": before})
            result.append({"type": "thinking", "content": json.dumps(data, ensure_ascii=False, indent=2)})
            after = content[json_end + 1:].strip()
            if after:
                result.extend(_split_inline_json(after))
            return result

        search_from = brace_start + 1

    if content.strip():
        return [{"type": "text", "content": content}]
    return []


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


_ARCHIVE_CACHE = {}
_ARCHIVE_CACHE_MAX = 8


def _fallback_archive():
    from app.core.util.runtime_paths import resolve_dir_writable
    memory_dir = resolve_dir_writable("memory")
    archive_dir = os.path.join(memory_dir, "archive")
    try:
        from app.core.util.agent_config import get_config
        backend = (get_config().get("memory.archive_backend", "sqlite") or "sqlite").lower()
    except Exception:
        backend = "sqlite"
    db_path = os.path.join(memory_dir, "archive.db")
    if backend != "jsonl" and os.path.exists(db_path):
        try:
            from app.agent.memory.archive_sqlite import SqliteArchiveStore
            return SqliteArchiveStore(db_path, jsonl_dir=archive_dir, dual_write=False)
        except Exception:
            pass
    from app.agent.memory.archive_store import ArchiveStore
    return ArchiveStore(archive_dir)


def _resolve_archive():
    from app.agent.loop.session_manager import get_session_manager
    mgr = get_session_manager()
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
            archive = _fallback_archive()
        except Exception:
            archive = None
    return mgr, archive


def history_revision(session_id: str = None):
    _, archive = _resolve_archive()
    return _archive_revision(archive, session_id) if archive is not None else None


def _archive_signature(archive):
    base = getattr(archive, "base_dir", None)
    if not base:
        return None
    try:
        files = [f for f in os.listdir(base) if f.endswith(".jsonl")]
        if not files:
            return None
        newest = max(files, key=lambda f: os.stat(os.path.join(base, f)).st_mtime_ns)
        st = os.stat(os.path.join(base, newest))
    except OSError:
        return None
    return (len(files), newest, st.st_mtime_ns, st.st_size)


def _archive_revision(archive, session_id=None):
    rev = getattr(archive, "revision", None)
    if callable(rev):
        try:
            return ("rev", int(rev(session_id) if session_id else rev()))
        except TypeError:
            try:
                return ("rev", int(rev()))
            except Exception:
                pass
        except Exception:
            pass
    return ("sig", _archive_signature(archive))


def load_session_messages(session_id: str, limit: int = 500, record_cap: int = 0) -> list:
    mgr, archive = _resolve_archive()
    raw_msgs = []
    rev = _archive_revision(archive, session_id) if archive is not None else None
    cache_key = (session_id, int(limit), int(record_cap), rev) if rev is not None and rev[1] is not None else None

    if cache_key is not None:
        cached = _ARCHIVE_CACHE.get(cache_key)
        if cached is not None:
            return list(cached)

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

    if cache_key is not None and raw_msgs:
        if len(_ARCHIVE_CACHE) >= _ARCHIVE_CACHE_MAX:
            _ARCHIVE_CACHE.clear()
        _ARCHIVE_CACHE[cache_key] = list(raw_msgs)

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

    rc = msg.get("reasoning_content")
    if isinstance(rc, str) and rc.strip():
        timeline.append({"kind": "reasoning", "content": rc.strip(), "duration_ms": msg.get("reasoning_duration_ms", 0)})

    if tool_calls:
        tc_map = {}
        if content:
            if _is_thought_protocol_block(content):
                reasoning = _extract_protocol_reasoning(content)
                if reasoning:
                    timeline.append({"kind": "thinking", "content": reasoning})
            else:
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
                if is_internal_user_message(sub):
                    j += 1
                    continue
                break
            elif sub.get("role") == "system" and is_gene_phase_message(sub):
                break
            else:
                j += 1

        for tc_id, tc_info in tc_map.items():
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
                "call_id": tc_id,
                "tool": tc_info["tool"],
                "arguments": args,
                "result": result,
                "duration_ms": duration_ms,
            })

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
            if content and not is_internal_user_message(m):
                plan.append({
                    "kind": "notice" if is_delegation_notice(content) else "user",
                    "content": content,
                })
            i += 1
        elif role == "assistant":
            content = m.get("content")
            if not content and not m.get("tool_calls"):
                i += 1
                continue
            built = build_assistant_timeline(m, messages, i)
            i = built["end_index"]
            tool_card = None
            for seg in built["timeline"]:
                kind = seg["kind"]
                if kind == "thinking":
                    if tool_card is not None:
                        plan.append(tool_card)
                        tool_card = None
                    plan.append({"kind": "thinking", "content": seg["content"]})
                elif kind == "reasoning":
                    if tool_card is not None:
                        plan.append(tool_card)
                        tool_card = None
                    plan.append({"kind": "reasoning", "content": seg["content"], "duration_ms": seg.get("duration_ms", 0)})
                elif kind == "tool":
                    if tool_card is None:
                        tool_card = {"kind": "tool", "calls": []}
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
                        plan.append({"kind": "assistant", "content": c})
            if tool_card is not None:
                plan.append(tool_card)
        elif role == "system" and is_gene_phase_message(m):
            i += 1
            while i < len(messages):
                nxt = messages[i]
                if nxt.get("role") == "user" and not is_internal_user_message(nxt):
                    break
                i += 1
        else:
            i += 1
    return plan, dropped
