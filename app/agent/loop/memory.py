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

    def add_tool_result(self, tool_call_id: str, result: dict):
        """插入工具执行结果到提示词"""
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result, ensure_ascii=False)
        })

    def get_messages(self) -> List[Dict]:
        messages = self._smart_truncate(self.messages, self.max_history)
        return self._fix_message_sequence(messages)

    def _smart_truncate(self, messages: List[Dict], max_count: int) -> List[Dict]:
        """
        智能截断消息，确保 tool 消息链的完整性
        
        规则：
        1. 如果消息数 <= max_count，直接返回
        2. 截断时，确保不截断 tool 消息对应的 assistant 消息
        3. 如果截断点在 tool 消息链中间，向前扩展到链的开始
        4. 确保第一条消息是 user 或 system 消息
        """
        if len(messages) <= max_count:
            return messages
        
        # 从截断点开始，向前查找确保消息链完整
        start_idx = len(messages) - max_count
        
        # 如果截断点第一条是 tool 消息，需要向前找对应的 assistant 消息
        while start_idx > 0 and messages[start_idx].get("role") == "tool":
            tool_call_id = messages[start_idx].get("tool_call_id", "")
            
            # 向前查找对应的 assistant 消息
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
                # 找不到对应的 assistant 消息，停止向前扩展
                break
        
        # 确保第一条消息是 user 或 system 消息
        while start_idx < len(messages) and messages[start_idx].get("role") not in ("user", "system"):
            start_idx += 1
        
        return messages[start_idx:] if start_idx < len(messages) else messages[-max_count:]

    def _fix_message_sequence(self, messages: List[Dict]) -> List[Dict]:
        """
        修复消息序列，确保 tool 消息前面有对应的 tool_calls assistant 消息
        这是为了兼容某些 LLM API（如 DeepSeek）的严格校验
        
        注意：不再移除孤立消息，而是保留它们，避免历史对话恢复时数据丢失
        """
        # 不再移除孤立消息，直接返回
        # 如果需要严格校验，可以在发送给 LLM 之前处理
        return messages

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
