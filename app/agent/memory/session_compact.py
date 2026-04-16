# -*- coding: utf-8 -*-
"""
SessionCompactor — 会话记忆压缩器

功能：
  - 检查压缩触发条件（Token 阈值）
  - 同步执行压缩（避免与主循环 LLM 调用冲突）
  - 使用 LLM 生成结构化摘要
  - 用笔记替代旧消息，保留最近 N 条原文
"""

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
        tool_call_threshold: int = 10,  # 默认 10 次工具调用触发压缩
        keep_recent_messages: int = 10,
        max_notes_length: int = 2000,
        repository: "MemoryRepository" = None,
    ):
        self.llm = llm_engine
        self.token_threshold = token_threshold
        self.tool_call_threshold = tool_call_threshold
        self.keep_recent_messages = keep_recent_messages
        self.max_notes_length = max_notes_length
        self._pending_compact = False  # 标记是否有待执行的压缩
        self._tool_call_count = 0  # 累计工具调用次数
        self._last_compact_tokens = 0  # 上次压缩后的 token 数量
        self._compact_cooldown_ratio = 0.3  # 冷却比例：token 增长 30% 后才再次触发
        self._repository = repository  # 长期记忆仓库引用

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

        # 条件1：Token 数量超过阈值（需要 LLM 生成摘要）
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
        同步执行压缩（在迭代开始前调用）

        Args:
            memory: MemoryManager 实例
            notes: SessionNotes 实例
        """
        self._pending_compact = False  # 清除标记
        self._tool_call_count = 0  # 重置工具调用计数器

        if self.llm is None:
            logger.debug("[SessionCompactor] 无 LLM 引擎，跳过压缩")
            return

        if len(memory.messages) <= self.keep_recent_messages:
            logger.debug("[SessionCompactor] 消息数不足，无需压缩")
            return

        old_messages = memory.messages[:-self.keep_recent_messages]

        formatted = self._format_messages(old_messages)
        summary_data = await self._generate_summary_with_llm(formatted)

        notes.load()
        if summary_data.get("goal"):
            notes.update_goal_from_summary(summary_data["goal"])
        notes.set_completed(summary_data.get("actions", []))
        for finding in summary_data.get("findings", []):
            notes.add_finding(finding)
        for error in summary_data.get("errors", []):
            notes.add_error(error, resolution=None)

        notes.save()

        if self._repository:
            self._persist_notes_to_long_term(notes, summary_data)

        self._replace_old_messages(memory, notes, summary_data.get("summary", ""))

        self._last_compact_tokens = self._estimate_tokens(memory)

        logger.info(
            "[SessionCompactor] LLM 压缩完成 | 压缩 %d 条消息 | 保留 %d 条原文 | 当前 tokens=%d",
            len(old_messages), self.keep_recent_messages, self._last_compact_tokens
        )

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
        for msg in messages:
            # 跳过已压缩的笔记消息，避免重复压缩
            if msg.get("_is_compacted_notes"):
                continue

            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "user" and content:
                lines.append(f"[用户]: {content[:500]}")
            elif role == "assistant" and content:
                lines.append(f"[助手]: {content[:500]}")
            elif role == "tool":
                tool_name = msg.get("tool_call_id", "unknown")
                lines.append(f"[工具结果-{tool_name}]: {content[:300] if content else '(无内容)'}")

        return "\n".join(lines)[:8000]

    async def _generate_summary_with_llm(self, messages_text: str) -> Dict:
        """使用 LLM 生成结构化摘要"""
        try:
            prompt = LLM_SUMMARIZE_PROMPT.format(messages=messages_text)
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
            )

            import json
            content = response.content or "{}"
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(content[start:end])
            else:
                logger.warning("[SessionCompactor] LLM 返回格式异常 | content=%s", content[:200])
                return {}
        except Exception as e:
            logger.error("[SessionCompactor] LLM 摘要生成失败 | error=%s", e)
            return {}

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
