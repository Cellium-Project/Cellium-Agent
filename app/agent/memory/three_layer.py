# -*- coding: utf-8 -*-
"""
三层记忆管理器

设计原则：
  - 短期记忆 = MemoryManager 的消息列表（per-session，自动维护，天然有界）
  - 长期记忆 = 统一仓库（FTS5 + hybrid recall + 治理能力）
  - 人格记忆 = personality.md 静态文件
"""

import json
import os
from typing import Any, Dict, List, Optional

from .archive_store import ArchiveStore
from .chinese_tokenizer import get_tokenizer
from .fts5_searcher import FTS5MemorySearcher
from .knowledge_extractor import KnowledgeExtractor
from .repository import MemoryRepository


class ThreeLayerMemory:
    """三层记忆：人格 + 会话上下文(外部注入) + 统一长期记忆仓库"""

    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir = memory_dir
        self.searcher = FTS5MemorySearcher(memory_dir)
        self.archive = ArchiveStore(os.path.join(memory_dir, "archive"))
        self.extractor = KnowledgeExtractor(searcher=self.searcher)
        self.repository = MemoryRepository(memory_dir, self.searcher)
        self.tokenizer = get_tokenizer()
        os.makedirs(os.path.join(memory_dir, "archive"), exist_ok=True)

    # ============================================================
    # 核心接口
    # ============================================================

    def build_prompt(self, user_input: str, session_messages: List[Dict] = None) -> str:
        """构建增强提示词（兼容旧接口，但内部走统一检索入口）"""
        personality = self._load_personality()
        context_section = self._format_session_context(session_messages)
        long_term_results = self.retrieve_context(user_input, top_k=3, exclude_schema_types=["control_gene"])
        long_term_section = self.format_retrieved_context(long_term_results)
        return f"""{personality}

---

## 当前对话上下文
{context_section}

---

## 相关历史记忆（统一检索）
{long_term_section}

---

## 用户新问题
{user_input}

请基于以上上下文和历史记忆回答。保持对话连贯性。"""

    def persist_session(
        self,
        user_input: str,
        response: str,
        *,
        session_id: str = "default",
        messages: Optional[List[Dict]] = None,
    ) -> str:
        """统一持久化入口：归档 + 知识提取 + 结构化长期记忆写入。"""
        normalized_messages = self._normalize_messages(user_input, response, messages)
        snapshot_hash = self._snapshot_hash(normalized_messages)
        latest = self.archive.get_latest_by_session(session_id)

        if latest and latest.get("snapshot_hash") == snapshot_hash:
            return latest.get("id", "")
        
        source_id = self.archive.append_messages(
            session_id=session_id,
            messages=normalized_messages,
            snapshot_hash=snapshot_hash,
        )

        knowledge_items = self.extractor.extract_from_messages(user_input, response, normalized_messages)
        for index, item in enumerate(knowledge_items, 1):
            if self.extractor.is_noise(item.get("content", "")):
                continue
            if self.extractor.is_duplicate(item.get("content", "")):
                continue

            metadata = dict(item.get("metadata", {}))
            metadata.update({
                "archive_id": source_id,
                "session_id": session_id,
            })
            self.repository.upsert_memory(
                title=item.get("title", "记忆"),
                content=item.get("content", ""),
                category=item.get("category", self._detect_category(item.get("tags", ""))),
                tags=item.get("tags", ""),
                source_file=f"archive:{source_id}:{index}",
                schema_type=item.get("schema_type", "general"),
                memory_key=item.get("memory_key", ""),
                metadata=metadata,
                allow_sensitive=False,
                merge_strategy="merge",
            )

        return source_id

    def save_conversation(
        self,
        user_input: str,
        response: str,
        session_id: str = "default",
        messages: list = None,
    ) -> str:
        """兼容旧接口，内部统一转发到 persist_session。"""
        return self.persist_session(
            user_input,
            response,
            session_id=session_id,
            messages=messages,
        )

    def retrieve_context(
        self,
        query: str,
        *,
        top_k: int = 3,
        include_raw: bool = False,
        category: Optional[str] = None,
        schema_type: Optional[str] = None,
        include_sensitive: bool = False,
        exclude_schema_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """统一长期记忆检索入口。"""
        clean_query = self._preprocess_query(query)
        if len(clean_query) < 2:
            return []

        results = self.repository.search(
            clean_query,
            top_k=top_k,
            category=category,
            schema_type=schema_type,
            include_sensitive=include_sensitive,
        )

        if exclude_schema_types:
            results = [r for r in results if r.get("schema_type") not in exclude_schema_types]

        enriched = []
        for item in results:
            archive_id = item.get("metadata", {}).get("archive_id")
            raw_conversation = self.archive.get_by_id(archive_id) if include_raw and archive_id else None
            if item.get("id"):
                self.repository.increment_usage(item["id"])
            enriched.append({
                **item,
                "raw": raw_conversation,
            })
        return enriched

    def retrieve_with_context(self, query: str, top_k: int = 3) -> List[Dict]:
        """兼容旧接口：两阶段检索（摘要 + 原始对话回溯）。"""
        results = self.retrieve_context(query, top_k=top_k, include_raw=True)
        return [
            {
                "summary": item.get("content", ""),
                "raw": item.get("raw"),
                "score": item.get("score", 0.0),
                "tags": item.get("tags", ""),
                "schema_type": item.get("schema_type", "general"),
            }
            for item in results
        ]

    # ============================================================
    # 仓库治理封装
    # ============================================================

    def search_memories(
        self,
        query: str,
        *,
        top_k: int = 5,
        category: Optional[str] = None,
        schema_type: Optional[str] = None,
        include_sensitive: bool = False,
    ) -> List[Dict[str, Any]]:
        return self.retrieve_context(
            query,
            top_k=top_k,
            category=category,
            schema_type=schema_type,
            include_sensitive=include_sensitive,
        )

    def list_memories(
        self,
        *,
        category: Optional[str] = None,
        schema_type: Optional[str] = None,
        include_deleted: bool = False,
        include_sensitive: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """列表查询记忆，返回 {"items": [...], "total": N}"""
        return self.repository.list_memories(
            category=category,
            schema_type=schema_type,
            include_deleted=include_deleted,
            include_sensitive=include_sensitive,
            limit=limit,
            offset=offset,
        )

    def upsert_memory(self, **kwargs) -> Dict[str, Any]:
        return self.repository.upsert_memory(**kwargs)

    def update_memory(self, **kwargs) -> Dict[str, Any]:
        return self.repository.update_memory(**kwargs)

    def delete_memory(self, **kwargs) -> Dict[str, Any]:
        return self.repository.delete_memory(**kwargs)

    def forget_memories(self, **kwargs) -> Dict[str, Any]:
        return self.repository.forget_memories(**kwargs)

    def merge_conflicts(self, **kwargs) -> Dict[str, Any]:
        return self.repository.merge_conflicts(**kwargs)

    def get_memory(self, identifier: str) -> Optional[Dict[str, Any]]:
        return self.repository.get_record(identifier)

    def summarize_memories(self) -> Dict[str, Any]:
        return self.repository.summarize()

    def format_retrieved_context(self, results: List[Dict[str, Any]]) -> str:

        if not results:
            return "（未检索到相关历史记忆）"

        formatted = []
        for index, item in enumerate(results, 1):
            formatted.append(
                f"{index}. [{item.get('schema_type', 'general')}] {item.get('title', '无标题')}\n"
                f"   分类: {item.get('category', 'general')} | 标签: {item.get('tags', '') or '无'}\n"
                f"   {self._truncate(item.get('content', ''), 280)}"
            )
        return "\n\n".join(formatted)

    # ============================================================
    # 内部方法
    # ============================================================

    def _normalize_messages(self, user_input: str, response: str, messages: Optional[List[Dict]]) -> List[Dict]:
        if messages:
            return messages
        return [
            {"role": "user", "content": user_input or ""},
            {"role": "assistant", "content": response or ""},
        ]

    @staticmethod
    def _snapshot_hash(messages: List[Dict]) -> str:
        payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        import hashlib
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _format_session_context(self, messages: List[Dict]) -> str:
        if not messages:
            return "（新会话，无历史上下文）"

        lines = []
        recent = messages[-20:]
        for message in recent:
            role = message.get("role", "")
            content = message.get("content", "")
            if role == "user":
                lines.append(f"> **用户**: {self._truncate(content, 300)}")
            elif role == "assistant":
                lines.append(f"> **助手**: {self._truncate(content, 400)}")
            elif role == "tool":
                lines.append(f"> *[工具]*: {self._truncate(content, 200)}")
        total = len(messages)
        shown = len(recent)
        return f"最近 {shown} 轮 / 共 {total} 条消息\n" + "\n".join(lines)

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        text = text or ""
        return text if len(text) <= max_len else text[:max_len] + "..."

    def _preprocess_query(self, query: str) -> str:
        stop_words = ["帮我", "请", "查询", "一下", "请问", "能不能", "如何", "能不能"]
        clean_query = query or ""
        for word in stop_words:
            clean_query = clean_query.replace(word, "")
        clean_query = " ".join(clean_query.split())
        return clean_query or (query or "")

    def _detect_category(self, tags: str) -> str:
        if "command" in tags:
            return "command"
        if "code" in tags:
            return "code"
        if "troubleshooting" in tags:
            return "troubleshooting"
        return "general"

    def _load_personality(self) -> str:
        path = os.path.join(self.memory_dir, "personality.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "# Cellium Agent\n\n你是一个专业的桌面助手。"

    def close(self):
        self.searcher.close()
