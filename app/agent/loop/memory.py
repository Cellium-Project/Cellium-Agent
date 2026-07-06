import json
import logging
from typing import List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class MemoryManager:
    """记忆管理器 - 维护对话上下文"""

    def __init__(
        self,
        max_history: int = 50,
    ):
        self.messages: List[Dict] = []
        self._ephemeral_messages: List[Dict] = []
        self.max_history = max_history
        self.tool_call_counter = 0

    def update_config(
        self,
        max_history: int = None,
    ):
        """
        动态更新配置参数（热重载支持）

        Args:
            max_history: 最大消息数
        """
        if max_history is not None:
            self.max_history = max_history

        logger.info(
            "[MemoryManager] 配置已更新 | max_history=%d",
            self.max_history,
        )

    def add_user_message(self, content: str):
        self.messages.append({
            "role": "user",
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_assistant_message(self, content: str, reasoning_content: str = None):
        msg = {
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self.messages.append(msg)

    def add_system_message(self, content: str):
        """添加系统消息"""
        self.messages.append({
            "role": "system",
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_tool_call(self, tool_name: str, arguments: dict, tool_call_id: str = None) -> str:
        if not tool_call_id:
            self.tool_call_counter += 1
            tool_call_id = f"call_{self.tool_call_counter}"

        self.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False)
                }
            }]
        })

        return tool_call_id

    def add_tool_calls_batch(
        self,
        tool_calls_data: List[Dict],
        content: str = None,
        reasoning_content: str = None,
    ) -> List[str]:
        tool_calls = []
        tool_call_ids = []

        for data in tool_calls_data:
            tool_name = data.get("tool_name", "")
            arguments = data.get("arguments", {})
            tool_call_id = data.get("tool_call_id")

            if not tool_call_id:
                self.tool_call_counter += 1
                tool_call_id = f"call_{self.tool_call_counter}"

            tool_call_ids.append(tool_call_id)

            tool_calls.append({
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False)
                }
            })

        msg = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
            "timestamp": datetime.now().isoformat()
        }
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self.messages.append(msg)

        logger.debug(
            "[MemoryManager] 批量添加 tool_calls | count=%d | content_len=%d",
            len(tool_calls),
            len(content) if content else 0,
        )

        return tool_call_ids

    def add_tool_result(self, tool_call_id: str, result: dict):
        """插入工具执行结果到提示词"""
        content = json.dumps(result, ensure_ascii=False)
        if result.get("status") == "error" and result.get("traceback"):
            content = json.dumps({
                "error": result.get("error", ""),
                "error_type": result.get("error_type", ""),
                "traceback": result.get("traceback", ""),
                "_source": result.get("_source", ""),
                "status": "error",
            }, ensure_ascii=False)
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content
        })

    def get_messages(self) -> List[Dict]:
        """获取消息列表"""
        messages = self._smart_truncate(self.messages, self.max_history)
        messages = self._fix_message_sequence(messages)
        
        if self._ephemeral_messages:
            messages = messages + self._ephemeral_messages
        return messages

    def add_ephemeral_message(self, role: str, content: str):
        """添加临时消息（不存储到记忆，仅用于当前 LLM 调用）"""
        self._ephemeral_messages.append({
            "role": role,
            "content": content,
        })

    def clear_ephemeral_messages(self):
        """清理临时消息"""
        self._ephemeral_messages = []

    def remove_system_messages_by_content(self, content_substring: str) -> int:
        """移除包含指定内容的系统消息

        Args:
            content_substring: 内容子字符串，用于匹配要移除的系统消息

        Returns:
            移除的消息数量
        """
        original_count = len(self.messages)
        self.messages = [
            msg for msg in self.messages
            if not (
                msg.get("role") == "system"
                and content_substring in msg.get("content", "")
            )
        ]
        removed_count = original_count - len(self.messages)
        if removed_count > 0:
            logger.debug(f"[MemoryManager] 移除了 {removed_count} 条系统消息")
        return removed_count

    def remove_gene_system_messages(self) -> int:
        """移除 Gene 创建评估的提示消息"""
        gene_prompt_prefixes = [
            "[系统提示 - Gene 创建评估]",
            "[HARD CONSTRAINTS]",
            "[任务约束",
        ]

        original_count = len(self.messages)
        new_messages = []

        for msg in self.messages:
            content = msg.get("content", "")
            if not content:
                new_messages.append(msg)
                continue

            is_gene_prompt = False
            for prefix in gene_prompt_prefixes:
                if content.startswith(prefix):
                    is_gene_prompt = True
                    break

            if not is_gene_prompt:
                new_messages.append(msg)

        self.messages = new_messages
        removed_count = original_count - len(self.messages)
        if removed_count > 0:
            logger.debug(f"[MemoryManager] 移除了 {removed_count} 条 Gene 评估提示")
        return removed_count

    def _smart_truncate(self, messages: List[Dict], max_count: int) -> List[Dict]:
        if len(messages) <= max_count:
            return messages
        
        start_idx = len(messages) - max_count
        
        while start_idx > 0 and messages[start_idx].get("role") == "tool":
            tool_call_id = messages[start_idx].get("tool_call_id", "")
            
            found = False
            for j in range(start_idx - 1, -1, -1):
                msg = messages[j]
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        if tc.get("id") == tool_call_id:
                            start_idx = j
                            found = True
                            break
                if found:
                    break
            
            if not found:
                break
        
        while start_idx < len(messages) and messages[start_idx].get("role") not in ("user", "system"):
            start_idx += 1
        
        return messages[start_idx:] if start_idx < len(messages) else messages[-max_count:]

    def _fix_message_sequence(self, messages: List[Dict]) -> List[Dict]:
        if not messages:
            return messages

        has_reasoning_content = any(
            msg.get("role") == "assistant" and msg.get("reasoning_content")
            for msg in messages
        )

        fixed = []
        i = 0
        n = len(messages)

        while i < n:
            msg = messages[i]

            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_call_ids = {tc.get("id") for tc in msg.get("tool_calls", []) if tc.get("id")}
                if tool_call_ids:
                    fixed.append(msg)
                    i += 1
                    seen_tool_ids = set()
                    while i < n and messages[i].get("role") == "tool":
                        tid = messages[i].get("tool_call_id", "")
                        if tid in tool_call_ids:
                            seen_tool_ids.add(tid)
                            fixed.append(messages[i])
                        i += 1
                    if seen_tool_ids != tool_call_ids:
                        missing = tool_call_ids - seen_tool_ids
                        msg_copy = dict(msg)
                        msg_copy["tool_calls"] = [tc for tc in msg_copy.get("tool_calls", []) if tc.get("id") not in missing]
                        if not msg_copy["tool_calls"]:
                            msg_copy.pop("tool_calls", None)
                            if not msg_copy.get("content"):
                                msg_copy["content"] = ""
                        fixed[-1] = msg_copy
                else:
                    fixed.append(msg)
                    i += 1

            elif msg.get("role") == "tool":
                i += 1

            else:
                fixed.append(msg)
                i += 1

        # DeepSeek API 要求：如果对话中有 reasoning_content，所有 assistant 消息都必须有这个字段
        if has_reasoning_content:
            for msg in fixed:
                if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                    msg["reasoning_content"] = ""

        return fixed

    def clear(self):
        """清空对话历史"""
        self.messages = []
        self.tool_call_counter = 0

    def replace_with_notes(self, notes_message: Dict, keep_recent: int = 10):
        """
        用笔记消息替代旧消息

        Args:
            notes_message: 笔记消息（包含压缩摘要）
            keep_recent: 保留最近 N 条消息
        """
        if len(self.messages) <= keep_recent:
            return

        recent_messages = self.messages[-keep_recent:]
        self.messages = [notes_message] + recent_messages

        logger.info(
            "[MemoryManager] 笔记替代完成 | 保留 %d 条原文",
            keep_recent
        )

    def get_total_tokens_estimate(self) -> int:
        """估算总 Token 数量"""
        total_chars = 0
        for msg in self.messages:
            content = msg.get("content", "")
            if content:
                total_chars += len(content)
        return total_chars // 2

    def get_message_count_by_role(self) -> Dict[str, int]:
        """按角色统计消息数量"""
        counts = {}
        for msg in self.messages:
            role = msg.get("role", "unknown")
            counts[role] = counts.get(role, 0) + 1
        return counts
