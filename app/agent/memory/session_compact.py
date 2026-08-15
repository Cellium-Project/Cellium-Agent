# -*- coding: utf-8 -*-
"""
SessionCompactor — 会话记忆压缩器

功能：
  - 检查压缩触发条件（Token 阈值）
  - 同步执行压缩（避免与主循环 LLM 调用冲突）
  - 使用 LLM 生成结构化摘要
  - 用笔记替代旧消息，保留最近 N 条原文
"""

import asyncio
import logging
from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.llm.engine import BaseLLMEngine
    from app.agent.loop.memory import MemoryManager
    from app.agent.memory.repository import MemoryRepository
    from app.agent.memory.session_notes import SessionNotes

logger = logging.getLogger(__name__)

LLM_SUMMARIZE_PROMPT = """你是一个会话记忆压缩助手。请根据以下对话历史，生成一个简洁的结构化摘要。

## 对话历史
{messages}

## 输出格式
请生成以下格式的摘要（JSON）：

{{
    "goal": "用户的主要目标或问题（一句话概括）",
    "actions": ["已完成的关键操作列表（返回本次压缩周期内新增的操作）"],
    "findings": ["发现的关键信息、路径、配置等"],
    "errors": ["遇到的错误（如有）"],
    "summary": "200字以内的整体摘要"
}}

只输出JSON，不要有其他内容。"""


class SessionCompactor:
    """
    会话记忆压缩器

    触发条件：
      - Token 数量超过阈值
      - 工具调用次数超过阈值（可选）

    执行方式：
      - 同步执行（在下一次迭代开始前完成）
      - 使用 LLM 生成结构化摘要
      - 避免与主循环 LLM 调用冲突
    """

    def __init__(
        self,
        llm_engine: "BaseLLMEngine" = None,
        token_threshold: int = 100000,  # 默认 100k tokens 触发压缩
        tool_call_threshold: int = 21, 
        keep_recent_messages: int = 10,
        max_notes_length: int = 2000,
        repository: "MemoryRepository" = None,
        archive=None,
        retry_backoff_base: float = 2.0,  # 失败重试指数退避基数（秒）
    ):
        self.llm = llm_engine
        self.token_threshold = token_threshold
        self.tool_call_threshold = tool_call_threshold
        self.keep_recent_messages = keep_recent_messages
        self.max_notes_length = max_notes_length
        self.retry_backoff_base = retry_backoff_base
        self._pending_compact = False  # 标记是否有待执行的压缩
        self._tool_call_count = 0  # 累计工具调用次数
        self._last_compact_tokens = 0  # 上次压缩后的 token 数量
        self._compact_cooldown_ratio = 0.5  # 冷却比例：token 增长 50% 后才再次触发
        self._repository = repository  # 长期记忆仓库引用
        self._archive = archive  # 归档存储引用（压缩前补写原始消息）

    def track_tool_call(self):
        """追踪工具调用次数"""
        self._tool_call_count += 1

    def should_compact(self, memory: "MemoryManager") -> bool:
        """
        检查是否需要压缩

        Args:
            memory: MemoryManager 实例

        Returns:
            是否应该压缩
        """
        # 条件2：工具调用次数超过阈值（不受 LLM 是否存在的限制，也受冷却限制）
        # 冷却检查
        cooldown_blocked = False
        if self._last_compact_tokens > 0:
            token_count = self._estimate_tokens(memory)
            growth = token_count - self._last_compact_tokens
            growth_ratio = growth / max(self._last_compact_tokens, 1)
            if growth_ratio < self._compact_cooldown_ratio:
                cooldown_blocked = True
                logger.debug(
                    "[SessionCompactor] 工具调用阈值冷却中 | tool_calls=%d | 增长=%.1f%% (需>%.0f%%)",
                    self._tool_call_count, growth_ratio * 100, self._compact_cooldown_ratio * 100
                )

        tool_call_exceeded = not cooldown_blocked and self._tool_call_count >= self.tool_call_threshold

        if tool_call_exceeded:
            logger.info(
                "[SessionCompactor] 工具调用触发压缩 | tool_calls=%d (阈值=%d)",
                self._tool_call_count, self.tool_call_threshold
            )
            return True

        # 条件3：Token 数量超过阈值（需要 LLM 生成摘要）
        if self.llm is None:
            return False

        token_count = self._estimate_tokens(memory)

        # 冷却检查（只对 Token 阈值生效）
        cooldown_blocked = False
        if self._last_compact_tokens > 0:
            growth = token_count - self._last_compact_tokens
            growth_ratio = growth / max(self._last_compact_tokens, 1)
            if growth_ratio < self._compact_cooldown_ratio:
                cooldown_blocked = True
                logger.debug(
                    "[SessionCompactor] Token 阈值冷却中 | tokens=%d | 上次=%d | 增长=%.1f%% (需>%.0f%%)",
                    token_count, self._last_compact_tokens, growth_ratio * 100, self._compact_cooldown_ratio * 100
                )

        token_exceeded = not cooldown_blocked and token_count >= self.token_threshold

        if token_exceeded:
            logger.info(
                "[SessionCompactor] Token 阈值触发压缩 | tokens=%d (阈值=%d)",
                token_count, self.token_threshold
            )

        return token_exceeded

    def _estimate_tokens(self, memory: "MemoryManager") -> int:
        """估算 Token 数量"""
        from app.agent.llm.engine import _estimate_messages_tokens
        return _estimate_messages_tokens(memory.messages)

    def request_compact(self):
        """请求在下一次迭代开始时执行压缩"""
        self._pending_compact = True
        logger.info("[SessionCompactor] 已标记待执行压缩")

    def has_pending_compact(self) -> bool:
        """检查是否有待执行的压缩"""
        return self._pending_compact

    async def compact_now(self, memory: "MemoryManager", notes: "SessionNotes"):
        """
        同步执行压缩

        策略（Map-Reduce + 递归兜底）：
          1. 将旧消息分块（块间重叠 10-15%，防块边界丢信息）
          2. Map 阶段：每块独立调 LLM 生成结构化摘要
          3. 合并各块摘要到 notes（append 去重合并）
          4. Reduce 兜底：若合并后的摘要仍超窗口，对摘要再摘要（递归）

        Args:
            memory: MemoryManager 实例
            notes: SessionNotes 实例
        """
        self._pending_compact = False  # 清除标记
        self._tool_call_count = 0  # 重置工具调用计数器

        if self.llm is None:
            logger.error("[SessionCompactor] 无 LLM 引擎，无法执行压缩")
            raise ValueError("SessionCompactor 需要 LLM 引擎才能执行压缩")

        if len(memory.messages) <= self.keep_recent_messages:
            logger.debug("[SessionCompactor] 消息数不足，无需压缩")
            return

        notes.load()

        has_prior_notes = notes.exists() and (notes.get_goal() or notes.get_completed() or notes.get_findings() or notes.get_errors())
        if has_prior_notes:
            compact_idx = next(
                (i for i, m in enumerate(memory.messages) if m.get("_is_compacted_notes")), -1
            )
            if compact_idx >= 0:
                old_messages = memory.messages[compact_idx + 1:-self.keep_recent_messages]
            else:
                old_messages = memory.messages[:-self.keep_recent_messages]
        else:
            old_messages = memory.messages[:-self.keep_recent_messages]

        blocks = self._chunk_messages(old_messages)
        logger.info(
            "[SessionCompactor] 分块压缩 | 消息=%d → %d 块 | 增量模式=%s",
            len(old_messages), len(blocks), has_prior_notes,
        )

        summaries = []
        # 每块摘要：不设超时（相信 API），失败内部重试，最终降级规则摘要，压缩必然完成
        for idx, block in enumerate(blocks):
            formatted = self._format_messages(block)
            if not formatted:
                continue
            summary_data = await self._generate_summary_with_llm(formatted)
            summaries.append(summary_data)
            logger.debug(
                "[SessionCompactor] 块 %d/%d 摘要完成 | 消息=%d",
                idx + 1, len(blocks), len(block),
            )

        if not summaries:
            logger.warning("[SessionCompactor] 所有分块摘要为空，跳过压缩")
            return

        # 压缩落盘前，先把将被替换的原始消息补写进归档，保证归档 = 完整原始日志
        if self._archive and old_messages:
            try:
                self._archive.append_messages(
                    session_id=notes.session_id,
                    messages=old_messages,
                )
                logger.info(
                    "[SessionCompactor] 压缩前补写归档 | session=%s | %d 条原始消息",
                    notes.session_id, len(old_messages),
                )
            except Exception as e:
                logger.warning("[SessionCompactor] 压缩前补写归档失败: %s", e)

        summaries = await self._recursive_reduce(summaries)

        for summary_data in summaries:
            if summary_data.get("goal"):
                notes.update_goal_from_summary(summary_data["goal"])
            for action in summary_data.get("actions", []):
                notes.add_completed(action)
            for finding in summary_data.get("findings", []):
                notes.add_finding(finding)
            for error in summary_data.get("errors", []):
                notes.add_error(error, resolution=None)

        notes.save()

        merged_summary = self._merge_summaries(summaries)

        if self._repository:
            self._persist_notes_to_long_term(notes, merged_summary)

        self._replace_old_messages(memory, notes, merged_summary.get("summary", ""))

        self._last_compact_tokens = self._estimate_tokens(memory)

        logger.info(
            "[SessionCompactor] LLM 压缩完成 | 压缩 %d 条消息 (%d 块) | 保留 %d 条原文 | 当前 tokens=%d",
            len(old_messages), len(blocks), self.keep_recent_messages, self._last_compact_tokens
        )

    def _chunk_messages(self, messages: List[Dict], max_chars: int = 6000, overlap_ratio: float = 0.15) -> List[List[Dict]]:
        if not messages:
            return []

        total = sum(len(str(m.get("content") or "")) for m in messages)
        if total <= max_chars:
            return [messages]

        blocks = []
        current: List[Dict] = []
        current_chars = 0
        overlap_chars = int(max_chars * overlap_ratio)

        for msg in messages:
            m_chars = len(str(msg.get("content") or ""))
            if current and current_chars + m_chars > max_chars:
                blocks.append(current)
                overlap_take = []
                used = 0
                for m in reversed(current):
                    mc = len(str(m.get("content") or ""))
                    if used + mc > overlap_chars:
                        break
                    overlap_take.insert(0, m)
                    used += mc
                current = list(overlap_take)
                current_chars = used
            current.append(msg)
            current_chars += m_chars

        if current:
            blocks.append(current)
        return blocks

    async def _recursive_reduce(self, summaries: List[Dict]) -> List[Dict]:
        MAX_SUMMARY_CHARS = 8000
        MAX_REDUCE_ROUNDS = 10  # 收敛保护：超轮数直接返回，防止死循环
        current = list(summaries)

        for _ in range(MAX_REDUCE_ROUNDS):
            text = self._summaries_to_text(current)
            if len(text) <= MAX_SUMMARY_CHARS or len(current) <= 1:
                return current

            logger.info(
                "[SessionCompactor] 摘要超预算，递归归约 | 块=%d | 文本=%d 字符",
                len(current), len(text),
            )
            reduced = []
            for chunk in self._chunk_summaries(current, MAX_SUMMARY_CHARS):
                formatted = self._summaries_to_text(chunk)
                summary_data = await self._generate_summary_with_llm(formatted)
                # 合并 chunk 原始 goal/errors（小而关键，防降级目标丢失），
                # 不合并 findings/summary 以避免 reduce 永不收敛（死循环）
                if summary_data:
                    merged = self._merge_summaries([summary_data, *chunk])
                    # 收敛保护：只保留 LLM 新摘要的 findings/summary，避免拼接无限膨胀
                    merged["findings"] = summary_data.get("findings", []) or []
                    merged["summary"] = summary_data.get("summary", "") or merged["summary"]
                    reduced.append(merged)
                else:
                    reduced.append(self._merge_summaries(chunk))
            current = reduced

        return current

    def _summaries_to_text(self, summaries: List[Dict]) -> str:
        """将结构化摘要列表序列化为可读文本"""
        parts = []
        for s in summaries:
            lines = []
            if s.get("goal"):
                lines.append(f"目标: {s['goal']}")
            for a in s.get("actions", []):
                lines.append(f"- 操作: {a}")
            for f in s.get("findings", []):
                lines.append(f"- 发现: {f}")
            for e in s.get("errors", []):
                lines.append(f"- 错误: {e}")
            if s.get("summary"):
                lines.append(f"总结: {s['summary']}")
            if lines:
                parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def _chunk_summaries(self, summaries: List[Dict], max_chars: int) -> List[List[Dict]]:
        blocks, current, cc = [], [], 0
        for s in summaries:
            sc = len(self._summaries_to_text([s]))
            if current and cc + sc > max_chars:
                blocks.append(current)
                current, cc = [], 0
            current.append(s)
            cc += sc
        if current:
            blocks.append(current)
        return blocks

    def _merge_summaries(self, summaries: List[Dict]) -> Dict:
        merged = {"goal": "", "actions": [], "findings": [], "errors": [], "summary": ""}
        for s in summaries:
            if s.get("goal") and not merged["goal"]:
                merged["goal"] = s["goal"]
            for a in s.get("actions", []):
                if a not in merged["actions"]:
                    merged["actions"].append(a)
            for f in s.get("findings", []):
                if f not in merged["findings"]:
                    merged["findings"].append(f)
            for e in s.get("errors", []):
                if e not in merged["errors"]:
                    merged["errors"].append(e)
            if s.get("summary"):
                merged["summary"] += s["summary"] + "\n"
        merged["summary"] = merged["summary"].strip()
        return merged

    def _infer_category(self, note_type: str, content: str) -> str:
        content_lower = content.lower()

        if note_type == "error":
            return "troubleshooting"

        if note_type == "completed":
            if any(kw in content_lower for kw in ["搜索", "查询", "find", "search", "获取", "fetch"]):
                return "command"
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
        """将笔记内容按分类存入长期记忆，每类型1条记录，与笔记文件一致"""
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

    def _format_messages(self, messages: List[Dict]) -> str:
        """将消息格式化为可读文本"""
        lines = []
        call_id_to_name = {}
        for msg in messages:
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and "id" in tc:
                    fn = tc.get("function", {})
                    call_id_to_name[tc["id"]] = (fn.get("name", "") if isinstance(fn, dict) else "") or tc["id"]

        for msg in messages:
            if msg.get("_is_compacted_notes"):
                continue

            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "user" and content:
                lines.append(f"[用户]: {content[:500]}")
            elif role == "assistant" and content:
                lines.append(f"[助手]: {content[:500]}")
            elif role == "tool":
                call_id = msg.get("tool_call_id", "")
                tool_name = call_id_to_name.get(call_id, call_id) or "unknown"
                lines.append(f"[工具结果-{tool_name}]: {content[:300] if content else '(无内容)'}")

        return "\n".join(lines)[:8000]

    async def _generate_summary_with_llm(self, messages_text: str) -> Dict:
        prompt = LLM_SUMMARIZE_PROMPT.format(messages=messages_text)

        max_retries = 3
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = await self.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=4000,
                )
                if getattr(response, "finish_reason", None) == "length":
                    logger.warning(
                        "[SessionCompactor] 摘要输出被截断(finish_reason=length)，降级规则摘要 | attempt=%d/%d",
                        attempt, max_retries,
                    )
                    last_error = "输出截断"
                    break

                import json
                content = response.content or "{}"
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(content[start:end])
                else:
                    logger.warning(
                        "[SessionCompactor] LLM 返回格式异常，重试 | attempt=%d/%d | content=%s",
                        attempt, max_retries, content[:200],
                    )
                    last_error = "格式异常"
                    continue
            except Exception as e:
                last_error = e
                logger.warning(
                    "[SessionCompactor] LLM 摘要失败，重试 | attempt=%d/%d | error=%s",
                    attempt, max_retries, e,
                )
                await asyncio.sleep(self.retry_backoff_base * (2 ** (attempt - 1)))

        logger.error(
            "[SessionCompactor] LLM 摘要重试 %d 次均失败，降级为规则摘要 | last_error=%s",
            max_retries, last_error,
        )
        return self._fallback_summary(messages_text)

    def _fallback_summary(self, messages_text: str) -> Dict:
        """规则降级摘要：LLM 不可用时从文本提取保底摘要，结构兼容，保证压缩不中断"""
        lines = [l.strip() for l in (messages_text or "").splitlines() if l.strip()]
        if not lines:
            return {"goal": "", "actions": [], "findings": [], "errors": [], "summary": "（LLM 摘要不可用，已降级）"}

        goal = ""
        for line in lines:
            if line.startswith("[用户]"):
                goal = line[len("[用户]"):].strip()
                goal = goal.split(":", 1)[-1].strip()[:100] if ":" in goal else goal[:100]
                break

        findings = []
        for line in lines:
            if line.startswith("[用户]") or line.startswith("[助手]"):
                item = line.split("]:", 1)[-1].strip()[:80]
                if item and item not in findings:
                    findings.append(item)
            if len(findings) >= 10:
                break

        errors = [line.split("]:", 1)[-1].strip()[:80] for line in lines]
        errors = [e for e in errors if "error" in e.lower() or "失败" in e or "异常" in e][:5]

        summary = f"（降级摘要）共 {len(lines)} 条消息。"
        if goal:
            summary += f"目标：{goal}。"

        return {
            "goal": goal,
            "actions": [],
            "findings": findings,
            "errors": errors,
            "summary": summary,
        }

    def _replace_old_messages(self, memory: "MemoryManager", notes: "SessionNotes", summary: str = ""):
        """用笔记替代旧消息"""
        notes_content = notes.render_for_prompt(max_length=self.max_notes_length)
        if summary:
            notes_content = f"**整体摘要**: {summary}\n\n{notes_content}"

        notes_message = {
            "role": "user",
            "content": f"[系统压缩] 之前的对话已压缩为以下摘要：\n\n{notes_content}",
            "_is_compacted_notes": True,
        }

        recent_messages = memory.messages[-self.keep_recent_messages:]
        memory.messages = [notes_message] + recent_messages

        memory.tool_call_counter = len([
            m for m in memory.messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ])
