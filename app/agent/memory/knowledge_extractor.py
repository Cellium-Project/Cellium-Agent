import hashlib
import logging
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from .chinese_tokenizer import get_tokenizer

logger = logging.getLogger(__name__)


class KnowledgeExtractor:
    """规则知识提取器 - 带中文分词增强"""

    def __init__(self, searcher=None):
        self.searcher = searcher
        self.tokenizer = get_tokenizer()

    def extract(self, user_input: str, response: str) -> List[Dict]:
        """从对话中提取知识项（带结构化 schema 元数据）"""
        text = (user_input or "") + "\n" + (response or "")
        results: List[Dict] = []

        for command in self._extract_commands(text):
            results.append(
                {
                    "title": "命令使用",
                    "content": command,
                    "tags": self._auto_tags(command, base_tag="command"),
                    "category": "command",
                    "schema_type": "general",
                }
            )

        results += self._extract_error_solutions(user_input, response)

        for code in self._extract_code_blocks(text):
            results.append(
                {
                    "title": "代码片段",
                    "content": code,
                    "tags": self._auto_tags(code, base_tag="code"),
                    "category": "code",
                    "schema_type": "general",
                }
            )

        filtered = []
        seen_hashes = set()
        for item in results:
            if self.is_noise(item["content"]):
                continue
            current_hash = self._content_hash(item["title"], item["content"])
            if current_hash in seen_hashes:
                continue
            seen_hashes.add(current_hash)
            filtered.append(item)
        return filtered

    def extract_from_messages(self, user_input: str, response: str, messages: Optional[List[Dict]] = None) -> List[Dict]:
        """基于完整消息链提取知识。当前以 user/assistant 为主，并补充工具错误事实。"""
        items = self.extract(user_input, response)
        recent_tool_errors = []
        for message in messages or []:
            if message.get("role") != "tool":
                continue
            content = (message.get("content") or "")[:300]
            if "error" in content.lower() or "错误" in content or "失败" in content:
                recent_tool_errors.append(content)

        if recent_tool_errors:
            issue_text = "\n".join(recent_tool_errors[:2])
            items.append(
                {
                    "title": "工具错误上下文",
                    "content": issue_text,
                    "tags": "troubleshooting,tool-error",
                    "category": "troubleshooting",
                    "schema_type": "issue",
                    "memory_key": hashlib.md5(issue_text.encode("utf-8")).hexdigest()[:16],
                    "metadata": {"problem": issue_text, "source": "tool_result"},
                }
            )
        return items

    def _auto_tags(self, content: str, base_tag: str = "") -> str:
        tags = [base_tag] if base_tag else []
        keywords = self.tokenizer.extract_keywords(content, top_k=3)
        tags.extend(keywords)
        return ",".join(dict.fromkeys(tag for tag in tags if tag))

    @staticmethod
    def is_noise(text: str) -> bool:
        text_stripped = (text or "").strip()
        if len(text_stripped) < 15:
            return True

        noise_words = [
            "谢谢", "好的", "明白", "可以", "收到", "了解",
            "你好", "您好", "hi", "hello", "ok", "好的，有什么可以帮您",
            "我是一个", "我是你的助手", "我可以帮你",
        ]
        text_lower = text_stripped.lower()
        if any(text_lower == word or (text_lower.startswith(word) and len(text_lower) <= len(word) + 4) for word in noise_words):
            return True


        generic_phrases = [
            "让我帮你查看", "我来帮你执行", "请告诉我",
            "有什么需要帮助的", "很高兴为您服务",
        ]
        if any(phrase in text_lower for phrase in generic_phrases):
            has_specific_info = any(keyword in text_lower for keyword in ["error", "错误", "路径", "文件", "命令", "config"])
            if not has_specific_info:
                return True

        action_patterns = [
            "然后", "接着", "下一步", "我来帮你",
            "我来执行", "我将", "我需要", "让我来",
            "首先", "其次", "最后", "步骤",
            "验证", "测试", "检查", "执行",
        ]
        action_count = sum(1 for p in action_patterns if p in text_lower)
        if action_count >= 2:
            tech_keywords = [
                "error", "错误", "路径", "文件", "config", "http", "api",
                "函数", "变量", "参数", "配置", "代码", "模块",
                "数据库", "服务器", "端口", "ip", "url",
            ]
            has_tech_info = any(kw in text_lower for kw in tech_keywords)
            if not has_tech_info:
                return True

        return False

    def is_duplicate(self, new_text: str, threshold: float = 0.85) -> bool:
        if not self.searcher:
            return False

        try:
            results = self.searcher.search(new_text, top_k=5)
            for item in results:
                if self._similarity(new_text, item.get("content", "")) > threshold:
                    return True
        except Exception as e:
            logger.debug("[KnowledgeExtractor] 搜索去重失败: %s", e)
        return False

    @staticmethod
    def _content_hash(title: str, content: str) -> str:
        raw = (title.strip() + "::" + content.strip()).encode("utf-8")
        return hashlib.md5(raw).hexdigest()

    def _extract_commands(self, text: str) -> List[str]:
        pattern = r"((?:Get|Set|New|Remove)-\w+[^\n]*|\b(?:rm|cp|mv|ls|cd)\s+[^\n]+)"

        return re.findall(pattern, text)


    def _extract_error_solutions(self, user: str, assistant: str) -> List[Dict]:
        user = user or ""
        assistant = assistant or ""
        if "错误" not in user and "error" not in user.lower() and "失败" not in user:
            return []

        problem = user[:120]
        resolution = assistant[:200]
        issue_key = hashlib.md5(problem.encode("utf-8")).hexdigest()[:16]
        return [
            {
                "title": "错误解决",
                "content": f"问题: {problem} -> 解决: {resolution}",
                "tags": "troubleshooting",
                "category": "troubleshooting",
                "schema_type": "issue",
                "memory_key": issue_key,
                "metadata": {
                    "problem": problem,
                    "resolution": resolution,
                },
            }
        ]

    def _extract_code_blocks(self, text: str) -> List[str]:
        return re.findall(r"```(?:\w+)?\n(.*?)```", text or "", re.DOTALL)

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio()
