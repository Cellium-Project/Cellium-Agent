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
        max_tool_results: int = 10,
        max_tool_result_length: int = 500,
        auto_compact_threshold: int = 10000,
    ):
        self.messages: List[Dict] = []
        self.max_history = max_history
        self.max_tool_results = max_tool_results
        self.max_tool_result_length = max_tool_result_length
        self.auto_compact_threshold = auto_compact_threshold
        self.tool_call_counter = 0

    def update_config(
        self,
        max_history: int = None,
        max_tool_results: int = None,
        max_tool_result_length: int = None,
        auto_compact_threshold: int = None,
    ):
        """
        动态更新配置参数（热重载支持）

        Args:
            max_history: 最大消息数
            max_tool_results: 保留工具结果数
            max_tool_result_length: 结果截断长度
            auto_compact_threshold: 自动压缩阈值
        """
        if max_history is not None:
            self.max_history = max_history
        if max_tool_results is not None:
            self.max_tool_results = max_tool_results
        if max_tool_result_length is not None:
            self.max_tool_result_length = max_tool_result_length
        if auto_compact_threshold is not None:
            self.auto_compact_threshold = auto_compact_threshold

        logger.info(
            "[MemoryManager] 配置已更新 | max_history=%d | max_tool_results=%d",
            self.max_history, self.max_tool_results
        )

    def add_user_message(self, content: str):
        self.messages.append({
            "role": "user",
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_assistant_message(self, content: str):
        self.messages.append({
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

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

        self.messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
            "timestamp": datetime.now().isoformat()
        })

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
        messages = self._smart_truncate(self.messages, self.max_history)
        messages = self._truncate_long_tool_contents(messages)
        return self._fix_message_sequence(messages)

    def _truncate_long_tool_contents(self, messages: List[Dict]) -> List[Dict]:
        """截断过长的工具结果内容"""
        for msg in messages:
            if msg.get("role") == "tool" and not msg.get("_compacted"):
                content = msg.get("content", "")
                if len(content) > self.max_tool_result_length:
                    msg["content"] = content[:self.max_tool_result_length] + "\n...[已截断]"
                    msg["_truncated"] = True
        return messages

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

        return fixed

    def clear(self):
        """清空对话历史"""
        self.messages = []
        self.tool_call_counter = 0

    # ============================================================
    #  工具结果压缩
    # ============================================================

    def compact_tool_results(self) -> int:
        """
        清理过期的工具返回内容

        - 保留最近 max_tool_results 次完整结果
        - 较旧的结果截断为摘要
        - 返回清理的字节数
        """
        # 1. 找出所有 tool 角色的消息
        tool_messages = [
            (i, msg) for i, msg in enumerate(self.messages)
            if msg.get("role") == "tool"
        ]

        if len(tool_messages) <= self.max_tool_results:
            return 0  # 无需清理

        # 2. 确定需要截断的消息（较旧的）
        to_compact = tool_messages[:-self.max_tool_results]
        saved_bytes = 0

        # 3. 截断内容
        for idx, msg in to_compact:
            original = msg.get("content", "")
            if len(original) > self.max_tool_result_length:
                compacted = original[:self.max_tool_result_length] + "\n...[已压缩]"
                saved_bytes += len(original) - len(compacted)
                self.messages[idx]["content"] = compacted
                self.messages[idx]["_compacted"] = True

        if saved_bytes > 0:
            logger.info(
                "[MemoryManager] 压缩工具结果 | 共 %d 条 | 节省 %d 字节",
                len(to_compact), saved_bytes
            )

        return saved_bytes

    def should_compact(self) -> bool:
        """检查是否需要压缩"""
        total_length = sum(
            len(msg.get("content", ""))
            for msg in self.messages
            if msg.get("role") == "tool" and not msg.get("_compacted")
        )
        return total_length > self.auto_compact_threshold

    def get_tool_result_stats(self) -> Dict:
        """获取工具结果统计信息"""
        tool_messages = [
            msg for msg in self.messages
            if msg.get("role") == "tool"
        ]
        total_length = sum(len(msg.get("content", "")) for msg in tool_messages)
        compacted_count = sum(1 for msg in tool_messages if msg.get("_compacted"))

        return {
            "total_tool_results": len(tool_messages),
            "compacted_count": compacted_count,
            "total_bytes": total_length,
            "should_compact": self.should_compact(),
        }

    # ============================================================
    #  会话压缩支持
    # ============================================================

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
