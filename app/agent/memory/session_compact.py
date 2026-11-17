# -*- coding: utf-8 -*-
"""
SessionCompactor — 会话记忆压缩器

功能：
  - 检查压缩触发条件（Token 阈值 / 工具调用阈值）
  - 同步执行压缩（避免与主循环 LLM 调用冲突）
  - 使用 LLM 生成结构化摘要
  - 用笔记替代旧消息，保留最近 N 条原文
"""

import json
import logging
import time
from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.llm.engine import BaseLLMEngine
    from app.agent.loop.memory import MemoryManager
    from app.agent.memory.repository import MemoryRepository
    from app.agent.memory.session_notes import SessionNotes

logger = logging.getLogger(__name__)


def _as_items(value) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    return [value]


def _as_text_list(value) -> list:
    return [str(item).strip() for item in _as_items(value) if str(item).strip()]


def _summary_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return ""


_FILE_PATH_KEYS = ("file_path", "path", "filepath", "filename", "target_file")
_MODIFY_TOOL_HINTS = ("edit", "write", "create", "patch", "apply")
_RECENT_FILES_LIMIT = 10
_SYSTEM_USER_PREFIXES = ("[系统", "[上下文", "[约束", "[控制", "<system-reminder>")


def _is_user_task_message(msg: Dict) -> bool:
    if msg.get("role") != "user" or msg.get("_is_compacted_notes"):
        return False
    content = str(msg.get("content") or "").lstrip()
    return bool(content) and not content.startswith(_SYSTEM_USER_PREFIXES)


def _tool_file_paths(args: dict) -> list:
    if not isinstance(args, dict):
        return []
    paths = []
    for key in _FILE_PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    files = args.get("files")
    if isinstance(files, dict):
        paths.extend(str(key).strip() for key in files if str(key).strip())
    elif isinstance(files, list):
        paths.extend(str(item).strip() for item in files if isinstance(item, str) and item.strip())
    return paths


def _recent_files(messages: list, limit: int = _RECENT_FILES_LIMIT) -> list:
    seen = set()
    modified = []
    read = []
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        for call in reversed(msg.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            name = str(fn.get("name") or "")
            raw = fn.get("arguments")
            if isinstance(raw, str):
                try:
                    args = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            elif isinstance(raw, dict):
                args = raw
            else:
                continue
            kind = "edit" if any(hint in name.lower() for hint in _MODIFY_TOOL_HINTS) else "read"
            for path in _tool_file_paths(args):
                key = path.replace("\\", "/")
                if key in seen:
                    continue
                seen.add(key)
                (modified if kind == "edit" else read).append((path, kind))
    return (modified + read)[:limit]


LLM_SUMMARIZE_PROMPT = """你是一个会话记忆压缩助手。请根据以下对话历史，生成供后续继续任务使用的结构化摘要。

## 对话历史
{messages}

## 要求
- 若开头已有历史摘要或会话笔记，请在其基础上更新，不要丢失仍然有效的目标、约束、错误和待办。
- 保留精确的文件路径、命令、函数名、配置项和报错原文。
- 已完成与待处理分开写，不确定的信息不要编造。

## 输出格式
只输出 JSON：
{{
    "goal": "用户当前的主要目标（一句话）",
    "actions": ["已完成的关键操作"],
    "findings": ["关键信息、文件路径、配置、决策"],
    "errors": ["尚未解决的错误；已解决的写成 错误 → 解决方案"],
    "pending": ["待处理事项或下一步"],
    "summary": "不超过 800 字的整体摘要，按 目标 / 当前状态 / 关键决策 / 下一步 组织"
}}
"""


SUMMARY_CACHE_TAIL = """以上是完整的会话历史，请基于上面全部内容生成供后续继续任务使用的结构化摘要。

## 要求
- 若上面已有历史摘要或会话笔记，请在其基础上更新，不要丢失仍然有效的目标、约束、错误和待办。
- 保留精确的文件路径、命令、函数名、配置项和报错原文。
- 已完成与待处理分开写，不确定的信息不要编造。
- 不要调用任何工具，直接输出 JSON。

## 输出格式
只输出 JSON：
{{
    "goal": "用户当前的主要目标（一句话）",
    "actions": ["已完成的关键操作"],
    "findings": ["关键信息、文件路径、配置、决策"],
    "errors": ["尚未解决的错误；已解决的写成 错误 → 解决方案"],
    "pending": ["待处理事项或下一步"],
    "summary": "不超过 800 字的整体摘要，按 目标 / 当前状态 / 关键决策 / 下一步 组织"
}}
"""


class SessionCompactor:
    """
    会话记忆压缩器

    触发条件：
      - Token 数量超过阈值
      - 工具调用次数超过阈值
      """


    def __init__(
        self,
        llm_engine: "BaseLLMEngine" = None,
        token_threshold: int = None,
        tool_call_threshold: int = 21,
        keep_recent_messages: int = 20,
        keep_recent_tokens: int = 0,
        max_notes_length: int = 2000,
        repository: "MemoryRepository" = None,
        archive=None,
        compact_ratio: float = 0.8,
    ):
        self.llm = llm_engine
        # token_threshold 显式给出时按绝对值，否则按模型窗口 × compact_ratio
        self._explicit_threshold = int(token_threshold) if token_threshold else 0
        self.compact_ratio = float(compact_ratio or 0.8)
        self.token_threshold = self._explicit_threshold or 100000
        self.tool_call_threshold = tool_call_threshold
        self.keep_recent_messages = keep_recent_messages
        self.keep_recent_tokens = max(0, int(keep_recent_tokens or 0))
        self.max_notes_length = max_notes_length
        self._pending_compact = False
        self._tool_call_count = 0
        self._pending_fallback = False
        self._compact_fail_count = 0
        self._compact_cooldown_until = 0.0
        self._repository = repository
        self._archive = archive

    def track_tool_call(self):
        self._tool_call_count += 1

    def token_usage(
        self,
        memory: "MemoryManager",
        current_messages: List[Dict] = None,
        tools_count: int = 0,
    ) -> int:
        if current_messages is not None:
            if self.llm is not None and hasattr(self.llm, "estimate_tokens_calibrated"):
                token_count = self.llm.estimate_tokens_calibrated(current_messages)
            else:
                from app.agent.llm.engine import _estimate_messages_tokens
                token_count = _estimate_messages_tokens(current_messages)
            if tools_count:
                token_count += tools_count * 150
            if self.llm is not None and hasattr(self.llm, "get_last_prompt_tokens"):
                actual = self.llm.get_last_prompt_tokens()
                if actual > token_count:
                    token_count = actual
        else:
            token_count = self._estimate_tokens(memory)
        return token_count

    def should_compact(
        self,
        memory: "MemoryManager",
        current_messages: List[Dict] = None,
        tools_count: int = 0,
    ) -> bool:
        """检查是否需要压缩"""
        return self.should_compact_tokens(
            self.token_usage(memory, current_messages=current_messages, tools_count=tools_count)
        )

    def compact_threshold(self) -> int:
        """触发压缩的 token 阈值：窗口已知时按窗口 × compact_ratio 自适应，显式配置仅作兜底"""
        cw = getattr(self.llm, "context_window", 0) or 0
        if cw > 0:
            return max(1, int(cw * self.compact_ratio))
        return self._explicit_threshold or self.token_threshold

    def should_compact_tokens(self, token_count: int) -> bool:
        if self._compact_cooldown_until and time.time() < self._compact_cooldown_until:
            return False

        if self.tool_call_threshold > 0 and self._tool_call_count >= self.tool_call_threshold:
            logger.info("[SessionCompactor] 工具调用触发压缩 | tool_calls=%d", self._tool_call_count)
            return True

        if self.llm is None:
            return False

        threshold = self.compact_threshold()
        if token_count >= threshold:
            logger.info(
                "[SessionCompactor] Token 阈值触发压缩 | tokens=%d | threshold=%d",
                token_count, threshold,
            )
            return True

        return False

    def _estimate_tokens(self, memory: "MemoryManager") -> int:
        if self.llm and hasattr(self.llm, "get_last_prompt_tokens"):
            actual = self.llm.get_last_prompt_tokens()
            if actual > 0:
                return actual
        from app.agent.llm.engine import _estimate_messages_tokens
        return _estimate_messages_tokens(memory.messages)

    def is_over_window(
        self,
        memory: "MemoryManager",
        current_messages: List[Dict] = None,
        tools_count: int = 0,
    ) -> bool:
        """估算输入是否已达到上下文窗口"""
        return self.is_over_window_tokens(
            self.token_usage(memory, current_messages=current_messages, tools_count=tools_count)
        )

    def is_over_window_tokens(self, token_count: int) -> bool:
        if self.llm is None:
            return False
        cw = getattr(self.llm, "context_window", None)
        if not cw or cw <= 0:
            return False
        return token_count > cw

    def request_compact(self, fallback: bool = False):
        self._pending_compact = True
        if fallback:
            self._pending_fallback = True
        logger.info("[SessionCompactor] 已标记待执行压缩")

    @property
    def pending_fallback(self) -> bool:
        return self._pending_fallback

    def has_pending_compact(self) -> bool:
        return self._pending_compact

    async def compact_now(
        self,
        memory: "MemoryManager",
        notes: "SessionNotes",
        fallback: bool = False,
        cache_prefix: List[Dict] = None,
        tools: List[Dict] = None,
    ):
        """执行压缩"""
        self._pending_compact = False
        self._pending_fallback = False
        self._tool_call_count = 0

        if self.llm is None:
            logger.warning("[SessionCompactor] 无 LLM 引擎，跳过压缩")
            return False

        keep_n = max(1, self.keep_recent_messages)
        keep_tokens = self.keep_recent_tokens
        if fallback:
            keep_n = max(2, keep_n // 5)
            keep_tokens = max(2000, keep_tokens // 5) if keep_tokens else 0
            logger.warning(
                "[SessionCompactor] 输入已达上下文窗口，执行保护性压缩 | keep_recent=%d", keep_n
            )

        notes.load()

        cut = self._tail_cut_index(memory.messages, keep_tokens, keep_n)
        if cut <= 0:
            if not fallback:
                logger.debug("[SessionCompactor] 消息数不足，无需压缩")
                return False
            cut = self._tail_cut_index(memory.messages, 0, max(1, len(memory.messages) - 1))

        # 增量压缩：只压上次快照后的新消息
        has_prior_notes = notes.exists() and (
            notes.get_goal() or notes.get_completed() or notes.get_findings()
            or notes.get_errors() or notes.get_pending()
        )
        if has_prior_notes:
            compact_idx = next(
                (i for i, m in enumerate(memory.messages) if m.get("_is_compacted_notes")), -1
            )
            if compact_idx >= 0:
                old_messages = memory.messages[compact_idx + 1:cut]
                context_messages = memory.messages[compact_idx:cut]
            else:
                old_messages = memory.messages[:cut]
                context_messages = old_messages
        else:
            old_messages = memory.messages[:cut]
            context_messages = old_messages

        if not old_messages:
            logger.debug("[SessionCompactor] 无新消息需要压缩")
            return False

        # 归档补写（只写新消息；已归档过原文的修剪消息跳过，避免修剪版覆盖原文）
        if self._archive:
            try:
                to_archive = [
                    m for m in old_messages
                    if not m.get("_origin_archived")
                ]
                if to_archive:
                    self._archive.append_messages(session_id=notes.session_id, messages=to_archive)
                logger.info(
                    "[SessionCompactor] 归档补写 | %d 条（跳过已归档原文 %d 条）",
                    len(to_archive), len(old_messages) - len(to_archive),
                )
            except Exception as e:
                logger.warning("[SessionCompactor] 归档补写失败，跳过本次压缩: %s", e)
                return False

        # 传给 LLM 的上下文包含历史压缩笔记 + 新消息
        summary_data = await self._generate_summary_with_llm(
            lambda: self._format_messages(context_messages),
            cache_prefix=cache_prefix,
            tools=tools,
        )

        summary_text = _summary_text(summary_data.get("summary"))
        if not summary_text:
            self._compact_fail_count += 1
            if self._compact_fail_count >= 2:
                self._compact_cooldown_until = time.time() + 300
                logger.warning(
                    "[SessionCompactor] 连续 %d 次压缩失败，冷却 300 秒 | 保持原消息不变",
                    self._compact_fail_count,
                )
            logger.warning("[SessionCompactor] 摘要缺失或格式不符，跳过本次压缩")
            return False

        notes_changed = False

        goal_value = summary_data.get("goal")
        if isinstance(goal_value, str) and goal_value.strip():
            notes.update_goal_from_summary(goal_value.strip())
            notes_changed = True

        actions_value = summary_data.get("actions")
        if isinstance(actions_value, list):
            actions = _as_text_list(actions_value)
            if actions:
                notes.set_completed(actions)
                notes_changed = True

        pending_value = summary_data.get("pending")
        if isinstance(pending_value, list):
            pending = _as_text_list(pending_value)
            if pending:
                notes.set_pending(pending)
                notes_changed = True

        findings_value = summary_data.get("findings")
        if isinstance(findings_value, list):
            before = len(notes.get_findings())
            for finding in _as_text_list(findings_value):
                notes.add_finding(finding)
            if len(notes.get_findings()) != before:
                notes_changed = True

        errors_value = summary_data.get("errors")
        if isinstance(errors_value, list):
            before = len(notes.get_errors())
            for error in errors_value:
                if isinstance(error, dict):
                    message = str(error.get("error", "")).strip()
                    if message:
                        resolution = str(error.get("resolution", "")).strip() or None
                        notes.add_error(message, resolution=resolution)
                elif isinstance(error, str) and error.strip():
                    notes.add_error(error.strip())
            if len(notes.get_errors()) != before:
                notes_changed = True

        if notes_changed:
            if len(notes.get_findings()) > 50:
                notes._content["findings"] = notes.get_findings()[-50:]
            if len(notes.get_errors()) > 50:
                notes._content["errors"] = notes.get_errors()[-50:]
            notes.save()
            if self._repository:
                self._persist_notes_to_long_term(notes, summary_data)

        self._replace_old_messages(memory, notes, summary_text, keep_n=keep_n, cut=cut, keep_tokens=keep_tokens)

        self._compact_fail_count = 0
        self._compact_cooldown_until = 0.0
        if self.llm and hasattr(self.llm, "reset_last_prompt_tokens"):
            self.llm.reset_last_prompt_tokens()

        logger.info(
            "[SessionCompactor] 压缩完成 | %d 条消息",
            len(old_messages),
        )
        return True

    SUMMARY_BUDGET_RATIO = 0.45
    SUMMARY_BUDGET_FLOOR = 30000
    SUMMARY_BUDGET_CEIL = 800000
    SUMMARY_MIN_MSG_TOKENS = 60
    SUMMARY_OUTPUT_RESERVE = 8000

    def _summary_budget(self) -> int:
        cw = getattr(self.llm, "context_window", 0) or 0
        if cw > 0:
            cap = int(cw * self.SUMMARY_BUDGET_RATIO)
            cap = min(cap, max(2000, cw - self.SUMMARY_OUTPUT_RESERVE - 1000))
            return max(2000, min(cap, self.SUMMARY_BUDGET_CEIL))
        return self.SUMMARY_BUDGET_FLOOR

    @staticmethod
    def _shrink_to_tokens(text: str, max_tokens: int) -> str:
        from app.agent.llm.engine import _estimate_tokens
        text = str(text or "")
        if not text:
            return ""
        est = _estimate_tokens(text)
        if est <= max_tokens:
            return text
        if max_tokens <= 0:
            return ""
        if max_tokens < 120:
            keep = max(1, int(len(text) * max_tokens / est))
            return text[:keep] + "…"
        head_budget = int(max_tokens * 0.4)
        tail_budget = max_tokens - head_budget
        head_len = max(1, int(len(text) * head_budget / est))
        tail_len = max(1, int(len(text) * tail_budget / est))
        return f"{text[:head_len]}...[中间省略]...{text[-tail_len:]}"

    def _format_messages(self, messages: List[Dict], budget_tokens: int = None) -> str:
        """按 token 预算格式化消息，预算随模型窗口自适应"""
        from app.agent.llm.engine import _estimate_tokens

        total_budget = self._summary_budget() if budget_tokens is None else max(1000, int(budget_tokens))
        budget = int(total_budget * 0.95)

        call_id_to_name = {}
        for msg in messages:
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and "id" in tc:
                    fn = tc.get("function", {})
                    call_id_to_name[tc["id"]] = (fn.get("name", "") if isinstance(fn, dict) else "") or tc["id"]

        notes_blocks = []
        for msg in messages:
            if not msg.get("_is_compacted_notes"):
                continue
            content = str(msg.get("content") or "")
            if not content.strip():
                continue
            allowed = max(2000, budget // 3)
            allowed = min(allowed, max(budget, 0))
            if allowed <= 0:
                continue
            block = f"[历史压缩摘要]: {self._shrink_to_tokens(content, allowed)}"
            notes_blocks.append(block)
            budget -= _estimate_tokens(block)

        selected = []
        omitted = 0
        for msg in reversed(messages):
            if msg.get("_is_compacted_notes"):
                continue
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if role == "tool":
                call_id = msg.get("tool_call_id", "")
                tool_name = call_id_to_name.get(call_id, call_id) or "unknown"
                prefix = f"[工具-{tool_name}]: "
                raw = content or "(空)"
            elif role == "user":
                prefix = "[用户]: "
                raw = content
            elif role == "assistant":
                prefix = "[助手]: "
                raw = content
            else:
                continue
            if not str(raw).strip():
                continue
            prefix_tokens = _estimate_tokens(prefix)
            remaining = budget - prefix_tokens
            if remaining < self.SUMMARY_MIN_MSG_TOKENS:
                omitted += 1
                continue
            line = prefix + self._shrink_to_tokens(raw, remaining)
            selected.append(line)
            budget -= _estimate_tokens(line)

        selected.reverse()
        lines = []
        if omitted:
            lines.append(f"[已省略较早的 {omitted} 条消息，可用 memory.recall 从归档检索]")
        lines.extend(notes_blocks)
        lines.extend(selected)
        return "\n".join(lines)

    def _parse_summary_json(self, content: str) -> Dict:
        import re
        content = content or "{}"
        if "```" in content:
            content = re.sub(r"```(?:json)?\s*", "", content)
            content = re.sub(r"```", "", content)
        content = re.sub(r"[\x00-\x1f]", lambda m: "\\u" + format(ord(m.group()), "04x"), content)
        content = re.sub(r",\s*([}\]])", r"\1", content)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(content[start:end])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {}

    SUMMARY_PREFIX_MAX_RATIO = 0.85
    _SUMMARY_MSG_KEYS = ("role", "content", "name", "tool_calls", "tool_call_id", "reasoning_content")

    @classmethod
    def _clean_prefix_messages(cls, prefix: List[Dict]) -> List[Dict]:
        out = []
        for msg in prefix or []:
            if not isinstance(msg, dict):
                continue
            item = {k: msg[k] for k in cls._SUMMARY_MSG_KEYS if k in msg and msg[k] is not None}
            if not item.get("role"):
                continue
            out.append(item)
        return out

    def _build_summary_payload(self, messages_text, cache_prefix: List[Dict] = None):
        """构造摘要请求：优先复用正常调用的完整前缀以命中上下文缓存，超窗则退回格式化文本"""
        if cache_prefix:
            from app.agent.llm.engine import _estimate_messages_tokens
            prefix = self._clean_prefix_messages(cache_prefix)
            tail = {"role": "user", "content": SUMMARY_CACHE_TAIL}
            est = _estimate_messages_tokens(prefix + [tail])
            cw = getattr(self.llm, "context_window", 0) or 0
            if cw > 0:
                limit = min(
                    int(cw * self.SUMMARY_PREFIX_MAX_RATIO),
                    max(2000, cw - self.SUMMARY_OUTPUT_RESERVE - 1000),
                )
            else:
                limit = 400000
            if prefix and est <= limit:
                return prefix + [tail], True
            if prefix:
                logger.info(
                    "[SessionCompactor] 前缀过大，退回格式化摘要路径 | 前缀≈%d tokens | 上限=%d",
                    est, limit,
                )
        text = messages_text() if callable(messages_text) else messages_text
        return [
            {"role": "user", "content": LLM_SUMMARIZE_PROMPT.format(messages=text)}
        ], False

    async def _generate_summary_with_llm(
        self, messages_text: str, cache_prefix: List[Dict] = None, tools: List[Dict] = None
    ) -> Dict:
        payload, reused = self._build_summary_payload(messages_text, cache_prefix)
        extra = {"tools": tools} if (reused and tools) else {}
        thinking_backup = getattr(self.llm, "_thinking", None)
        try:
            if thinking_backup is not None:
                self.llm._thinking = None
            logger.info(
                "[SessionCompactor] 生成摘要 | 复用对话前缀=%s | messages=%d | tools=%d",
                reused, len(payload), len(extra.get("tools") or []),
            )
            response = await self.llm.chat(
                messages=payload,
                temperature=0.3,
                max_tokens=8000,
                **extra,
            )
            parsed = self._parse_summary_json(response.content or "")
            if not _summary_text(parsed.get("summary")):
                logger.warning("[SessionCompactor] 摘要格式不符，重试一次")
                response = await self.llm.chat(
                    messages=payload,
                    temperature=0.2,
                    max_tokens=8000,
                    **extra,
                )
                parsed = self._parse_summary_json(response.content or "")
            return parsed
        except Exception as e:
            logger.error("[SessionCompactor] LLM 摘要失败 | error=%s", e)
            return {}
        finally:
            if thinking_backup is not None:
                self.llm._thinking = thinking_backup

    @staticmethod
    def _message_tokens(msg: Dict) -> int:
        from app.agent.llm.engine import _estimate_messages_tokens
        return _estimate_messages_tokens([msg])

    def _tail_cut_index(self, messages: List[Dict], keep_tokens: int = None, max_messages: int = None) -> int:
        total = len(messages)
        if total == 0:
            return 0
        budget = self.keep_recent_tokens if keep_tokens is None else max(0, int(keep_tokens))
        keep_n = max(1, int(max_messages if max_messages is not None else self.keep_recent_messages))
        if budget > 0:
            idx = total
            used = 0
            i = total - 1
            while i >= 0:
                tokens = self._message_tokens(messages[i])
                if used + tokens > budget and idx < total:
                    break
                used += tokens
                idx = i
                i -= 1
            idx = min(idx, total - keep_n)
        else:
            idx = total - keep_n
        idx = max(0, idx)
        while idx < total and messages[idx].get("role") == "tool":
            idx += 1
        return idx

    @staticmethod
    def _safe_cut_index(messages: List[Dict], keep_n: int) -> int:
        total = len(messages)
        idx = max(0, total - keep_n)
        while idx < total and messages[idx].get("role") == "tool":
            idx += 1
        return idx

    GIANT_MSG_CHARS = 4000

    @classmethod
    def _truncate_giant_message(cls, msg: Dict) -> Dict:
        role = msg.get("role")
        if role not in ("user", "tool") or msg.get("_is_compacted_notes"):
            return msg
        content = msg.get("content")
        if isinstance(content, str):
            if len(content) <= cls.GIANT_MSG_CHARS:
                return msg
            new = dict(msg)
            new["content"] = cls._clip_giant(content, role)
            return new
        if isinstance(content, list):
            changed = False
            parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and len(part["text"]) > cls.GIANT_MSG_CHARS:
                    part = dict(part)
                    part["text"] = cls._clip_giant(part["text"], role)
                    changed = True
                parts.append(part)
            if not changed:
                return msg
            new = dict(msg)
            new["content"] = parts
            return new
        return msg

    @classmethod
    def _clip_giant(cls, content: str, role: str) -> str:
        if role == "tool":
            head, tail = 1000, 2600
        else:
            head, tail = 3200, 500
        omitted = len(content) - head - tail
        return f"{content[:head]}\n\n...[中间省略约 {omitted} 字符]...\n\n{content[-tail:]}"

    PRUNED_TOOL_MIN_CHARS = 400
    PRUNED_TOOL_HEAD = 300

    def _trim_tool_results(self, messages: List[Dict], budget: int, source: List[Dict] = None) -> List[Dict]:
        if budget <= 0 or not messages:
            return messages
        from app.agent.llm.engine import _estimate_messages_tokens
        total = _estimate_messages_tokens(messages)
        if total <= budget:
            return messages
        names = {}
        for msg in (source or messages):
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    fn = tc.get("function") or {}
                    names[tc["id"]] = fn.get("name") or ""
        result = list(messages)
        for i, msg in enumerate(result[:-2]):
            if total <= budget:
                break
            if msg.get("role") != "tool" or msg.get("_pruned") or msg.get("_is_compacted_notes"):
                continue
            content = msg.get("content")
            if not isinstance(content, str) or len(content) <= self.PRUNED_TOOL_MIN_CHARS:
                continue
            old_tokens = _estimate_messages_tokens([msg])
            name = names.get(msg.get("tool_call_id"), "")
            tag = f"[工具结果已清理: {name}]" if name else "[工具结果已清理]"
            new = dict(msg)
            new["content"] = f"{tag}\n{content[:self.PRUNED_TOOL_HEAD]}"
            new["_pruned"] = True
            result[i] = new
            total += _estimate_messages_tokens([new]) - old_tokens
        return result

    def _replace_old_messages(self, memory: "MemoryManager", notes: "SessionNotes", summary: str = "", keep_n: int = None, cut: int = None, keep_tokens: int = None):
        notes_content = notes.render_for_prompt(max_length=self.max_notes_length)
        if summary:
            notes_content = f"**摘要**: {summary}\n\n{notes_content}"
        parts = [f"[系统压缩] 之前的对话已压缩为以下摘要：\n\n{notes_content}"]
        files = _recent_files(memory.messages)
        if files:
            file_lines = ["## 最近涉及的文件"]
            file_lines.extend(f"- `{path}` ({kind})" for path, kind in files)
            parts.append("\n".join(file_lines))
        parts.append("（摘要里没有的细节，可用 memory.recall(query=\"关键词\") 从归档检索原文）")
        notes_message = {
            "role": "user",
            "content": "\n\n".join(parts),
            "_is_compacted_notes": True,
        }
        if cut is None:
            cut = self._safe_cut_index(memory.messages, keep_n or self.keep_recent_messages)

        current_task = None
        for i in range(cut - 1, -1, -1):
            msg = memory.messages[i]
            if _is_user_task_message(msg):
                current_task = msg
                break

        recent_raw = memory.messages[cut:]
        recent_messages = [self._truncate_giant_message(m) for m in recent_raw]
        budget = self.keep_recent_tokens if keep_tokens is None else int(keep_tokens or 0)
        recent_messages = self._trim_tool_results(recent_messages, budget, memory.messages)
        if self._archive:
            trimmed = [(raw, new) for raw, new in zip(recent_raw, recent_messages) if new is not raw]
            if trimmed:
                try:
                    self._archive.append_messages(
                        session_id=notes.session_id,
                        messages=[raw for raw, _ in trimmed],
                    )
                    for _, new in trimmed:
                        new["_origin_archived"] = True
                    logger.info("[SessionCompactor] 修剪前原文归档 | %d 条", len(trimmed))
                except Exception as e:
                    logger.warning("[SessionCompactor] 修剪原文归档失败: %s", e)
        head = [notes_message]
        if current_task is not None and current_task not in recent_messages:
            head.append(dict(current_task))
        memory.messages = head + recent_messages
        memory.tool_call_counter = len([
            m for m in memory.messages if m.get("role") == "assistant" and m.get("tool_calls")
        ])

    def _infer_category(self, note_type: str, content: str) -> str:
        content_lower = content.lower()
        if note_type == "error":
            return "troubleshooting"
        if note_type == "completed":
            if any(kw in content_lower for kw in ["修改", "编辑", "创建", "写入", "write", "edit", "create", "代码", "code"]):
                return "code"
            return "command"
        if note_type == "finding":
            if any(kw in content_lower for kw in ["项目", "project", "版本", "release", "bug", "issue"]):
                return "project"
            if any(kw in content_lower for kw in ["代码", "code", "实现", "implementation", "算法", "algorithm"]):
                return "code"
            if any(kw in content_lower for kw in ["搜索", "查询", "search", "find", "发现", "discover"]):
                return "command"
            return "project"
        if note_type in ("goal", "goal_history"):
            return "user_info"
        if note_type == "pending":
            return "general"
        return "general"

    def _persist_notes_to_long_term(self, notes: "SessionNotes", summary_data: dict):
        """将笔记内容按分类存入长期记忆"""
        try:
            from datetime import datetime
            day_cn = datetime.now().strftime("%m月%d日")

            goal = notes.get_goal()
            goal_history = notes.get_goal_history()
            completed = notes.get_completed()
            findings = notes.get_findings()
            errors = notes.get_errors()
            pending = notes.get_pending()

            if goal:
                self._repository.upsert_memory(
                    title=f"历史目标: {goal[:50]}",
                    content=f"[{day_cn}] {goal}",
                    category=self._infer_category("goal", goal),
                    note_type="goal_history",
                    schema_type="general",
                    memory_key=f"session_goal_hist:{notes.session_id}:{goal[:20]}",
                    metadata={"session_id": notes.session_id, "note_type": "goal_history", "source": "session_compact"},
                    allow_sensitive=True,
                    merge_strategy="merge",
                )

            if completed:
                completed_content = "\n".join([f"[{day_cn}] {a}" for a in completed])
                self._repository.upsert_memory(
                    title=f"已完成操作({len(completed)}项): {completed[-1][:30]}",
                    content=completed_content,
                    category=self._infer_category("completed", completed[-1]),
                    note_type="completed",
                    schema_type="general",
                    memory_key=f"session_completed:{notes.session_id}",
                    metadata={"session_id": notes.session_id, "note_type": "completed", "count": len(completed), "source": "session_compact"},
                    allow_sensitive=True,
                    merge_strategy="merge",
                )

            if findings:
                for i in range(0, len(findings), 2):
                    batch = findings[i:i+2]
                    batch_content = "\n".join([f"[{day_cn}] {f}" for f in batch])
                    batch_num = i // 2
                    self._repository.upsert_memory(
                        title=f"关键发现({batch_num+1}): {batch[-1][:30]}",
                        content=batch_content,
                        category=self._infer_category("finding", batch[-1]),
                        note_type="finding",
                        schema_type="general",
                        memory_key=f"session_finding:{notes.session_id}:{batch_num}",
                        metadata={"session_id": notes.session_id, "note_type": "finding", "batch": batch_num, "source": "session_compact"},
                        allow_sensitive=True,
                        merge_strategy="merge",
                    )

            if errors:
                for i in range(0, len(errors), 2):
                    batch = errors[i:i+2]
                    error_items = []
                    for err in batch:
                        error_msg = err.get("error", "") if isinstance(err, dict) else str(err)
                        error_res = err.get("resolution", "") if isinstance(err, dict) else ""
                        content = error_msg
                        if error_res:
                            content += f"\n解决方案: {error_res}"
                        error_items.append(f"[{day_cn}] {content}")
                    batch_content = "\n".join(error_items)
                    batch_num = i // 2
                    self._repository.upsert_memory(
                        title=f"错误({batch_num+1}): {batch[-1].get('error', '')[:30] if isinstance(batch[-1], dict) else str(batch[-1])[:30]}",
                        content=batch_content,
                        category=self._infer_category("error", batch[-1].get('error', '') if isinstance(batch[-1], dict) else str(batch[-1])),
                        note_type="error",
                        schema_type="general",
                        memory_key=f"session_error:{notes.session_id}:{batch_num}",
                        metadata={"session_id": notes.session_id, "note_type": "error", "batch": batch_num, "source": "session_compact"},
                        allow_sensitive=True,
                        merge_strategy="merge",
                    )

            if pending:
                for i in range(0, len(pending), 2):
                    batch = pending[i:i+2]
                    batch_content = "\n".join([f"[{day_cn}] {t}" for t in batch])
                    batch_num = i // 2
                    self._repository.upsert_memory(
                        title=f"待处理({batch_num+1}): {batch[-1][:30]}",
                        content=batch_content,
                        category=self._infer_category("pending", batch[-1]),
                        note_type="pending",
                        schema_type="general",
                        memory_key=f"session_pending:{notes.session_id}:{batch_num}",
                        metadata={"session_id": notes.session_id, "note_type": "pending", "batch": batch_num, "source": "session_compact"},
                        allow_sensitive=True,
                        merge_strategy="merge",
                    )

            logger.info(
                "[SessionCompactor] 笔记已存入长期记忆 | session=%s | goal=%d | hist=%d | actions=%d | findings=%d | errors=%d | pending=%d",
                notes.session_id, bool(goal), bool(goal_history), len(completed), len(findings), len(errors), len(pending)
            )
        except Exception as e:
            logger.warning("[SessionCompactor] 存入长期记忆失败: %s", e)
