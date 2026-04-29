# -*- coding: utf-8 -*-
"""
统一长期记忆仓库：
- 统一检索入口
- 统一写入/更新/删除/遗忘入口
- 结构化 schema + hybrid recall
- 敏感信息控制
"""

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from pypinyin import pinyin, Style
    HAS_PINYIN = True
except ImportError:
    HAS_PINYIN = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from .chinese_tokenizer import get_tokenizer

logger = logging.getLogger(__name__)


class MemoryRepository:
    """长期记忆统一仓库。"""

    CATALOG_FILE = "memory_catalog.json"
    EMBEDDING_DIMENSIONS = 96
    CATEGORY_SCHEMA_MAP = {
        "preference": "profile",
        "user_info": "profile",
        "project": "project",
        "troubleshooting": "issue",
    }
    VALID_SCHEMA_TYPES = {"general", "profile", "project", "issue", "control_gene"}
    ACTIVE_STATUSES = {"active"}
    STRONG_SECRET_PATTERNS = [
        ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)),
        ("aws_secret", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        ("generic_token", re.compile(r"\b(?:sk|ghp|github_pat|xoxb|xoxp|AIza)[A-Za-z0-9_\-]{10,}\b")),
    ]
    REDACT_PATTERNS = [
        (
            "credential_assignment",
            re.compile(
                r"(?i)\b(api[_-]?key|token|secret|password|passwd|access[_-]?key|refresh[_-]?token)\b\s*([:=])\s*([^\s,;]+)"
            ),
        ),
        (
            "bearer_token",
            re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;]+)"),
        ),
    ]

    def __init__(self, memory_dir: str, searcher):
        self.memory_dir = memory_dir
        self.searcher = searcher
        self.tokenizer = get_tokenizer()
        self.catalog_path = os.path.join(memory_dir, self.CATALOG_FILE)
        os.makedirs(memory_dir, exist_ok=True)
        self._catalog: Dict[str, Any] = {"version": 1, "records": {}}
        self._load_catalog()
        self._backfill_from_index()
        self._query_embedding_cache: Dict[str, List[float]] = {}
        self._query_cache_max_size = 100

    # ============================================================
    # Catalog
    # ============================================================

    def _load_catalog(self):
        if not os.path.exists(self.catalog_path):
            return

        try:
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("records"), dict):
                self._catalog = data
        except Exception as e:
            logger.warning("[MemoryRepository] 加载 catalog 失败，使用默认值: %s", e)

    def _save_catalog(self):
        with open(self.catalog_path, "w", encoding="utf-8") as f:
            json.dump(self._catalog, f, ensure_ascii=False, indent=2)

    def _backfill_from_index(self):
        changed = False
        records = self._catalog.setdefault("records", {})

        for item in self.searcher.list_memories(limit=100000):
            record_id = str(item["rowid"])
            if record_id in records:
                continue
            records[record_id] = self._build_catalog_entry(
                record_id=record_id,
                source_file=item.get("source_file", ""),
                title=item.get("title", ""),
                content=item.get("content", ""),
                category=item.get("category", "general"),
                tags=item.get("tags", ""),
                schema_type=self._normalize_schema_type(category=item.get("category", "general")),
                memory_key="",
                metadata={},
                sensitive=False,
                sensitivity_reason="",
                status="active",
                created_at=item.get("created_at") or datetime.now().isoformat(),
                updated_at=item.get("created_at") or datetime.now().isoformat(),
                revisions=1,
            )
            changed = True

        if changed:
            self._save_catalog()

    # ============================================================
    # Public API
    # ============================================================

    def upsert_memory(
        self,
        *,
        title: str,
        content: str,
        category: str = "general",
        note_type: str = "",
        tags: str = "",
        source_file: Optional[str] = None,
        schema_type: str = "general",
        memory_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        allow_sensitive: bool = False,
        merge_strategy: str = "merge",
    ) -> Dict[str, Any]:
        title = (title or "").strip()
        content = (content or "").strip()
        if not title:
            return {"success": False, "error": "标题不能为空"}
        if not content:
            return {"success": False, "error": "内容不能为空"}

        metadata = dict(metadata or {})
        schema_type = self._normalize_schema_type(schema_type=schema_type, category=category)
        sensitive_state = self._sanitize_sensitive_content(title, content, allow_sensitive=allow_sensitive)
        if sensitive_state["blocked"]:
            return {
                "success": False,
                "error": f"检测到高风险敏感信息，已拒绝写入: {sensitive_state['reason']}",
                "sensitive": True,
            }

        safe_title = sensitive_state["title"]
        safe_content = sensitive_state["content"]
        memory_key = memory_key or self._infer_memory_key(
            title=safe_title,
            content=safe_content,
            schema_type=schema_type,
            metadata=metadata,
        )

        existing = None
        if memory_key and merge_strategy != "create_new":
            existing = self._find_by_memory_key(memory_key, schema_type=schema_type)

        if existing:
            record_id, record = existing
            if merge_strategy == "replace":
                merged_title = safe_title
                merged_content = safe_content
            else:
                merged_title = safe_title or record.get("title", "")
                merged_content = self._merge_contents(record.get("content", ""), safe_content, schema_type=schema_type)
            
            existing_content = record.get("content", "")
            existing_title = record.get("title", "")
            content_unchanged = (merged_content == existing_content and merged_title == existing_title)
            
            merged_tags = self._merge_tags(record.get("tags", ""), tags)
            merged_metadata = self._merge_metadata(record.get("metadata", {}), metadata)
            merged_category = category or record.get("category", "general")
            merged_reason = self._merge_text(record.get("sensitivity_reason", ""), sensitive_state["reason"])

            self.searcher.update_memory(
                rowid=int(record_id),
                source_file=record.get("source_file", ""),
                title=merged_title,
                content=merged_content,
                category=merged_category,
                tags=merged_tags,
            )

            record.update(
                {
                    "title": merged_title,
                    "content": merged_content,
                    "category": merged_category,
                    "tags": merged_tags,
                    "schema_type": schema_type,
                    "memory_key": memory_key,
                    "metadata": merged_metadata,
                    "sensitive": bool(record.get("sensitive")) or sensitive_state["sensitive"],
                    "sensitivity_reason": merged_reason,
                    "embedding": self._embed_text(merged_title + "\n" + merged_content),
                }
            )
            
            if not content_unchanged:
                record["updated_at"] = datetime.now().isoformat()
                record["revisions"] = int(record.get("revisions", 1)) + 1
            record.setdefault("merged_sources", [])
            if source_file and source_file != record.get("source_file") and source_file not in record["merged_sources"]:
                record["merged_sources"].append(source_file)
            self._save_catalog()
            return {
                "success": True,
                "action": "merged",
                "id": record_id,
                "source": record.get("source_file", ""),
                "sensitive": record.get("sensitive", False),
            }

        source_file = source_file or self._gen_source_id(prefix=schema_type)
        rowid = self.searcher.insert_memory(
            title=safe_title,
            content=safe_content,
            category=category or "general",
            note_type=note_type,
            tags=tags or "",
            source_file=source_file,
            return_existing=True,
        )
        if rowid is None:
            return {"success": False, "error": "写入长期记忆失败"}

        record_id = str(rowid)
        self._catalog.setdefault("records", {})[record_id] = self._build_catalog_entry(
            record_id=record_id,
            source_file=source_file,
            title=safe_title,
            content=safe_content,
            category=category or "general",
            tags=tags or "",
            schema_type=schema_type,
            memory_key=memory_key,
            metadata=metadata,
            sensitive=sensitive_state["sensitive"],
            sensitivity_reason=sensitive_state["reason"],
            status="active",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            revisions=1,
        )
        self._save_catalog()
        return {
            "success": True,
            "action": "created",
            "id": record_id,
            "source": source_file,
            "sensitive": sensitive_state["sensitive"],
        }

    def store_user_question(
        self,
        question: str,
        answer_summary: str,
        archive_entry_id: str,
        session_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        存储用户问题及其关联的 archive entry

        Args:
            question: 用户问题
            answer_summary: 答案摘要（用于长期记忆搜索）
            archive_entry_id: archive entry ID，用于查看完整答案
            session_id: 会话 ID
            metadata: 额外元数据

        Returns:
            {"success": True, "id": ...}
        """
        if not question or not question.strip():
            return {"success": False, "error": "问题不能为空"}
        if not answer_summary or not answer_summary.strip():
            return {"success": False, "error": "答案摘要不能为空"}
        if not archive_entry_id:
            return {"success": False, "error": "archive_entry_id 不能为空"}

        meta = dict(metadata or {})
        meta["archive_entry_id"] = archive_entry_id
        meta["session_id"] = session_id
        meta["memory_type"] = "user_question"

        return self.upsert_memory(
            title=question[:200], 
            content=answer_summary,
            category="qa", 
            schema_type="general",
            memory_key=f"qa:{question[:100]}",
            metadata=meta,
            merge_strategy="create_new", 
        )

    def get_archive_entry_by_question(self, question_record_id: str) -> Optional[str]:
        """
        根据问题记录 ID 获取关联的 archive entry ID

        Args:
            question_record_id: 问题记录的 ID

        Returns:
            archive entry ID 或 None
        """
        record = self._get_catalog_record(question_record_id)
        if record and record.get("metadata", {}).get("memory_type") == "user_question":
            return record["metadata"].get("archive_entry_id")
        return None

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        category: Optional[str] = None,
        note_type: Optional[str] = None,
        schema_type: Optional[str] = None,
        include_sensitive: bool = False,
    ) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []

        query = self._expand_date_query(query)

        self._backfill_from_index()
        fetch_top_k = max(top_k * 4, 8)
        fts_results = self.searcher.search(query, top_k=fetch_top_k, category=category, note_type=note_type)
        filtered_fts = []
        for result in fts_results:
            record = self._get_catalog_record(result.get("rowid"))
            if not self._is_record_searchable(record, category=category, schema_type=schema_type, include_sensitive=include_sensitive):
                continue
            filtered_fts.append(result)

        embedding_results = self._embedding_search(
            query,
            top_k=fetch_top_k,
            category=category,
            schema_type=schema_type,
            include_sensitive=include_sensitive,
        )

        merged: Dict[str, Dict[str, Any]] = {}
        for rank, result in enumerate(filtered_fts, 1):
            record_id = str(result["rowid"])
            entry = merged.setdefault(record_id, self._public_record(record_id))
            if not entry:
                continue
            entry["score"] += 1.0 / (60 + rank)
            entry.setdefault("search_signals", {})["fts_rank"] = rank
            entry["fts_score"] = result.get("score", 0.0)

        for rank, result in enumerate(embedding_results, 1):
            record_id = str(result["rowid"])
            entry = merged.setdefault(record_id, self._public_record(record_id))
            if not entry:
                continue
            entry["score"] += 0.9 / (60 + rank)
            entry.setdefault("search_signals", {})["embedding_rank"] = rank
            entry["embedding_score"] = result.get("embedding_score", 0.0)

        normalized_query = query.lower()
        for entry in merged.values():
            text = f"{entry.get('title', '')}\n{entry.get('content', '')}".lower()
            if normalized_query and normalized_query in text:
                entry["score"] += 0.05

        results = [entry for entry in merged.values() if entry]
        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return results[:top_k]

    def search_memories(
        self,
        query: str,
        *,
        top_k: int = 3,
        category: Optional[str] = None,
        schema_type: Optional[str] = None,
        include_sensitive: bool = False,
    ) -> List[Dict[str, Any]]:
        """search_memories 别名方法 - 为了与 ThreeLayerMemory 接口保持兼容"""
        return self.search(
            query=query,
            top_k=top_k,
            category=category,
            schema_type=schema_type,
            include_sensitive=include_sensitive,
        )

    def _expand_date_query(self, query: str) -> str:
        """扩展日期查询：英文日期自动加上中文格式"""
        import re
        from datetime import datetime

        month_map = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12",
            "jan": "01", "feb": "02", "mar": "03", "apr": "04",
            "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        }

        expanded = query
        lower_q = query.lower()

        for eng_month, num_month in month_map.items():
            if eng_month in lower_q:
                cn_format = f"{num_month}月"
                if cn_format not in expanded:
                    expanded = f"{expanded} {cn_format}"

        today = datetime.now()
        for pattern in [
            r"yesterday", r"yesterday\s*(\d+)?",
            r"(\d+)\s*days?\s*ago", r"(\d+)\s*天前",
        ]:
            if re.search(pattern, lower_q):
                if "昨天" not in expanded:
                    expanded = f"{expanded} 昨天"
                break

        if re.search(r"today", lower_q):
            if "今天" not in expanded:
                expanded = f"{expanded} 今天"

        return expanded

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
        self._backfill_from_index()
        items = []
        for record_id, record in self._catalog.get("records", {}).items():
            if not include_deleted and record.get("status") != "active":
                continue
            if category and record.get("category") != category:
                continue
            if schema_type and record.get("schema_type") != schema_type:
                continue
            if not include_sensitive and record.get("sensitive"):
                continue
            items.append(self._public_record(record_id))

        items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        total = len(items)
        return {"items": items[offset : offset + limit], "total": total}

    def update_memory(
        self,
        *,
        identifier: Optional[str] = None,
        source: Optional[str] = None,
        memory_key: Optional[str] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[str] = None,
        schema_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        allow_sensitive: bool = False,
    ) -> Dict[str, Any]:
        found = self._resolve_record(identifier=identifier, source=source, memory_key=memory_key, schema_type=schema_type)
        if not found:
            return {"success": False, "error": "未找到可更新的记忆"}

        record_id, record = found
        new_title = (title if title is not None else record.get("title", "")).strip()
        new_content = (content if content is not None else record.get("content", "")).strip()
        new_category = category or record.get("category", "general")
        new_schema = self._normalize_schema_type(schema_type=schema_type or record.get("schema_type", "general"), category=new_category)
        new_tags = self._merge_tags(record.get("tags", ""), tags) if tags is not None else record.get("tags", "")
        new_metadata = self._merge_metadata(record.get("metadata", {}), metadata or {})

        if new_schema == "control_gene" or record.get("schema_type") == "control_gene":
            existing_version = new_metadata.get("version", 0)
            new_version = existing_version + 1
            new_metadata["version"] = new_version

            evolution_history = new_metadata.get("evolution_history", [])
            evolution_history.append({
                "version": new_version,
                "change": "agent_updated",
                "at": datetime.now().isoformat(),
            })
            new_metadata["evolution_history"] = evolution_history

            if new_title and not new_title.endswith(f"(v{new_version})"):
                import re
                new_title = re.sub(r'\s*\(v\d+\)\s*$', '', new_title)
                new_title = f"{new_title} (v{new_version})"

        sensitive_state = self._sanitize_sensitive_content(new_title, new_content, allow_sensitive=allow_sensitive)
        if sensitive_state["blocked"]:
            return {"success": False, "error": f"检测到高风险敏感信息，已拒绝更新: {sensitive_state['reason']}"}

        safe_title = sensitive_state["title"]
        safe_content = sensitive_state["content"]
        self.searcher.update_memory(
            rowid=int(record_id),
            source_file=record.get("source_file", ""),
            title=safe_title,
            content=safe_content,
            category=new_category,
            tags=new_tags,
        )

        new_memory_key = memory_key or record.get("memory_key") or self._infer_memory_key(
            title=safe_title,
            content=safe_content,
            schema_type=new_schema,
            metadata=new_metadata,
        )
        record.update(
            {
                "title": safe_title,
                "content": safe_content,
                "category": new_category,
                "tags": new_tags,
                "schema_type": new_schema,
                "memory_key": new_memory_key,
                "metadata": new_metadata,
                "updated_at": datetime.now().isoformat(),
                "revisions": int(record.get("revisions", 1)) + 1,
                "sensitive": bool(record.get("sensitive")) or sensitive_state["sensitive"],
                "sensitivity_reason": self._merge_text(record.get("sensitivity_reason", ""), sensitive_state["reason"]),
                "embedding": self._embed_text(safe_title + "\n" + safe_content),
            }
        )
        self._save_catalog()
        return {"success": True, "id": record_id, "source": record.get("source_file")}

    def delete_memory(
        self,
        *,
        identifier: Optional[str] = None,
        source: Optional[str] = None,
        memory_key: Optional[str] = None,
        reason: str = "deleted",
    ) -> Dict[str, Any]:
        found = self._resolve_record(identifier=identifier, source=source, memory_key=memory_key)
        if not found:
            return {"success": False, "error": "未找到可删除的记忆"}

        record_id, record = found
        record["status"] = "deleted"
        record["deleted_reason"] = reason
        record["updated_at"] = datetime.now().isoformat()
        self._save_catalog()
        return {"success": True, "id": record_id, "source": record.get("source_file")}

    def forget_memories(
        self,
        *,
        query: Optional[str] = None,
        source: Optional[str] = None,
        memory_key: Optional[str] = None,
        all_matches: bool = False,
    ) -> Dict[str, Any]:
        targets: List[Tuple[str, Dict[str, Any]]] = []
        if query:
            results = self.search(query, top_k=20, include_sensitive=True)
            if not results:
                return {"success": False, "error": f"未找到与「{query}」相关的记忆"}
            selected = results if all_matches else results[:1]
            for item in selected:
                found = self._resolve_record(identifier=item.get("id"))
                if found:
                    targets.append(found)
        else:
            found = self._resolve_record(source=source, memory_key=memory_key)
            if not found:
                return {"success": False, "error": "未找到可遗忘的记忆"}
            targets.append(found)

        changed = []
        for record_id, record in targets:
            record["status"] = "forgotten"
            record["updated_at"] = datetime.now().isoformat()
            changed.append(record_id)
        self._save_catalog()
        return {"success": True, "forgotten": changed}

    def merge_conflicts(
        self,
        *,
        memory_key: Optional[str] = None,
        schema_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        groups: Dict[Tuple[str, str], List[Tuple[str, Dict[str, Any]]]] = {}
        for record_id, record in self._catalog.get("records", {}).items():
            if record.get("status") != "active":
                continue
            current_key = record.get("memory_key")
            current_schema = record.get("schema_type", "general")
            if not current_key:
                continue
            if memory_key and current_key != memory_key:
                continue
            if schema_type and current_schema != schema_type:
                continue
            groups.setdefault((current_key, current_schema), []).append((record_id, record))

        merged_groups = 0
        merged_records = 0
        for (_group_key, current_schema), items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda item: item[1].get("updated_at", ""), reverse=True)
            canonical_id, canonical = items[0]
            merged_content = canonical.get("content", "")
            merged_tags = canonical.get("tags", "")
            merged_metadata = dict(canonical.get("metadata", {}))
            merged_sources = list(canonical.get("merged_sources", []))

            for duplicate_id, duplicate in items[1:]:
                merged_content = self._merge_contents(merged_content, duplicate.get("content", ""), schema_type=current_schema)
                merged_tags = self._merge_tags(merged_tags, duplicate.get("tags", ""))
                merged_metadata = self._merge_metadata(merged_metadata, duplicate.get("metadata", {}))
                duplicate_source = duplicate.get("source_file")
                if duplicate_source and duplicate_source not in merged_sources:
                    merged_sources.append(duplicate_source)
                duplicate["status"] = "merged"
                duplicate["merged_into"] = canonical_id
                duplicate["updated_at"] = datetime.now().isoformat()
                merged_records += 1

            self.searcher.update_memory(
                rowid=int(canonical_id),
                source_file=canonical.get("source_file", ""),
                title=canonical.get("title", ""),
                content=merged_content,
                category=canonical.get("category", "general"),
                tags=merged_tags,
            )
            canonical.update(
                {
                    "content": merged_content,
                    "tags": merged_tags,
                    "metadata": merged_metadata,
                    "merged_sources": merged_sources,
                    "updated_at": datetime.now().isoformat(),
                    "revisions": int(canonical.get("revisions", 1)) + merged_records,
                    "embedding": self._embed_text(canonical.get("title", "") + "\n" + merged_content),
                }
            )
            merged_groups += 1

        if merged_groups:
            self._save_catalog()
        return {"success": True, "merged_groups": merged_groups, "merged_records": merged_records}

    def increment_usage(self, identifier: str):
        record_id = str(identifier)
        if record_id.isdigit():
            self.searcher.increment_usage(rowid=int(record_id))

    def get_record(self, identifier: str) -> Optional[Dict[str, Any]]:
        return self._public_record(str(identifier))

    def summarize(self) -> Dict[str, Any]:
        self._backfill_from_index()
        records = list(self._catalog.get("records", {}).values())
        status_counts: Dict[str, int] = {}
        category_counts: Dict[str, int] = {}
        schema_counts: Dict[str, int] = {}
        sensitive_records = 0

        for record in records:
            status = record.get("status", "active") or "active"
            status_counts[status] = status_counts.get(status, 0) + 1
            if record.get("sensitive"):
                sensitive_records += 1
            if status == "active":
                category = record.get("category", "general") or "general"
                schema = record.get("schema_type", "general") or "general"
                category_counts[category] = category_counts.get(category, 0) + 1
                schema_counts[schema] = schema_counts.get(schema, 0) + 1

        return {
            "total_records": len(records),
            "active_records": status_counts.get("active", 0),
            "deleted_records": status_counts.get("deleted", 0),
            "forgotten_records": status_counts.get("forgotten", 0),
            "merged_records": status_counts.get("merged", 0),
            "sensitive_records": sensitive_records,
            "categories": [
                {"name": key, "count": value}
                for key, value in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            "schemas": [
                {"name": key, "count": value}
                for key, value in sorted(schema_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            "catalog_file": self.catalog_path,
            "memory_dir": self.memory_dir,
        }

    # ============================================================
    # Internal helpers
    # ============================================================


    def _resolve_record(
        self,
        *,
        identifier: Optional[str] = None,
        source: Optional[str] = None,
        memory_key: Optional[str] = None,
        schema_type: Optional[str] = None,
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        records = self._catalog.get("records", {})
        if identifier:
            record = records.get(str(identifier))
            if record and record.get("status") == "active":
                return str(identifier), record

        if source:
            for record_id, record in records.items():
                if record.get("status") == "active" and record.get("source_file") == source:
                    return record_id, record

        if memory_key:
            return self._find_by_memory_key(memory_key, schema_type=schema_type)

        return None

    def _find_by_memory_key(self, memory_key: str, schema_type: Optional[str] = None) -> Optional[Tuple[str, Dict[str, Any]]]:
        for record_id, record in self._catalog.get("records", {}).items():
            if record.get("status") != "active":
                continue
            if record.get("memory_key") != memory_key:
                continue
            if schema_type and record.get("schema_type") != schema_type:
                continue
            return record_id, record
        return None

    def _is_record_searchable(
        self,
        record: Optional[Dict[str, Any]],
        *,
        category: Optional[str],
        schema_type: Optional[str],
        include_sensitive: bool,
    ) -> bool:
        if not record:
            return False
        if record.get("status") != "active":
            return False
        if category and record.get("category") != category:
            return False
        if schema_type and record.get("schema_type") != schema_type:
            return False
        if not include_sensitive and record.get("sensitive"):
            return False
        return True

    def _embedding_search(
        self,
        query: str,
        *,
        top_k: int,
        category: Optional[str],
        schema_type: Optional[str],
        include_sensitive: bool,
    ) -> List[Dict[str, Any]]:
        """
        向量搜索 - 使用矩阵运算优化

        策略：
        - 动态构建 embedding 矩阵（N x 96）
        - 使用 numpy 批量点积计算相似度
        - 复杂度仍是 O(n)，但比 Python for-loop 快 10-50 倍
        """
        cache_key = query.lower().strip()
        if cache_key in self._query_embedding_cache:
            query_vector = self._query_embedding_cache[cache_key]
        else:
            query_vector = self._embed_text(query)
            if len(self._query_embedding_cache) < self._query_cache_max_size:
                self._query_embedding_cache[cache_key] = query_vector

        if not query_vector:
            return []

        # 收集符合条件的记录（过滤 + 确保有 embedding）
        active_records = []
        record_ids = []

        for record_id, record in self._catalog.get("records", {}).items():
            if not self._is_record_searchable(record, category=category, schema_type=schema_type, include_sensitive=include_sensitive):
                continue

            vector = record.get("embedding")
            if not vector:
                vector = self._embed_text(record.get("title", "") + "\n" + record.get("content", ""))
                record["embedding"] = vector

            active_records.append(vector)
            record_ids.append(record_id)

        if not active_records:
            return []

        # 使用 numpy 矩阵运算批量计算相似度
        if HAS_NUMPY:
            embeddings = np.array(active_records, dtype=np.float32)
            query_vec = np.array(query_vector, dtype=np.float32)

            # 批量点积: (N, 96) @ (96,) -> (N,)
            scores = embeddings @ query_vec

            # 取 top_k（使用 partition 避免全排序）
            k = min(top_k, len(scores))
            top_indices = np.argpartition(-scores, k - 1)[:k]
            top_indices = top_indices[np.argsort(-scores[top_indices])]

            results = []
            for idx in top_indices:
                score = float(scores[idx])
                if score <= 0.08:
                    continue
                results.append({
                    "rowid": int(record_ids[idx]),
                    "embedding_score": score,
                })
            return results

        # Fallback: Python 循环（无 numpy 时）
        scored = []
        for record_id, vector in zip(record_ids, active_records):
            score = self._cosine_similarity(query_vector, vector)
            if score <= 0.08:
                continue
            scored.append({
                "rowid": int(record_id),
                "embedding_score": score,
            })
        scored.sort(key=lambda item: item["embedding_score"], reverse=True)
        return scored[:top_k]

    def _public_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        record = self._get_catalog_record(record_id)
        if not record:
            return None
        return {
            "id": record_id,
            "title": record.get("title", ""),
            "content": record.get("content", ""),
            "category": record.get("category", "general"),
            "tags": record.get("tags", ""),
            "source_file": record.get("source_file", ""),
            "schema_type": record.get("schema_type", "general"),
            "memory_key": record.get("memory_key", ""),
            "metadata": dict(record.get("metadata", {})),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "sensitive": record.get("sensitive", False),
            "sensitivity_reason": record.get("sensitivity_reason", ""),
            "status": record.get("status", "active"),
            "revisions": int(record.get("revisions", 1)),
            "merged_sources": list(record.get("merged_sources", [])),
            "deleted_reason": record.get("deleted_reason", ""),
            "merged_into": record.get("merged_into", ""),
            "score": 0.0,
        }


    def _get_catalog_record(self, record_id: Any) -> Optional[Dict[str, Any]]:
        return self._catalog.get("records", {}).get(str(record_id))

    def _build_catalog_entry(
        self,
        *,
        record_id: str,
        source_file: str,
        title: str,
        content: str,
        category: str,
        tags: str,
        schema_type: str,
        memory_key: str,
        metadata: Dict[str, Any],
        sensitive: bool,
        sensitivity_reason: str,
        status: str,
        created_at: str,
        updated_at: str,
        revisions: int,
    ) -> Dict[str, Any]:
        return {
            "id": record_id,
            "source_file": source_file,
            "title": title,
            "content": content,
            "category": category,
            "tags": tags,
            "schema_type": schema_type,
            "memory_key": memory_key,
            "metadata": metadata,
            "sensitive": sensitive,
            "sensitivity_reason": sensitivity_reason,
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
            "revisions": revisions,
            "merged_sources": [],
            "embedding": self._embed_text(title + "\n" + content),
        }

    def _normalize_schema_type(self, schema_type: Optional[str] = None, category: str = "general") -> str:
        candidate = (schema_type or "").strip().lower()
        if candidate in self.VALID_SCHEMA_TYPES:
            return candidate
        return self.CATEGORY_SCHEMA_MAP.get((category or "general").strip().lower(), "general")

    def _infer_memory_key(self, *, title: str, content: str, schema_type: str, metadata: Dict[str, Any]) -> str:
        if schema_type == "profile":
            field = metadata.get("field") or title
            return f"profile:{self._normalize_key(field)}"
        if schema_type == "project":
            project_id = metadata.get("project_id") or metadata.get("path") or title
            return f"project:{self._normalize_key(project_id)}"
        if schema_type == "issue":
            problem = metadata.get("problem") or title or content[:80]
            return f"issue:{hashlib.md5(problem.encode('utf-8')).hexdigest()[:16]}"
        return metadata.get("memory_key", "")

    @staticmethod
    def _normalize_key(text: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", (text or "").strip().lower())
        normalized = normalized.strip("-")
        return normalized[:80] or "memory"

    def _sanitize_sensitive_content(self, title: str, content: str, *, allow_sensitive: bool) -> Dict[str, Any]:
        combined = f"{title}\n{content}"
        for reason, pattern in self.STRONG_SECRET_PATTERNS:
            if pattern.search(combined):
                return {
                    "blocked": not allow_sensitive,
                    "sensitive": True,
                    "title": title,
                    "content": content,
                    "reason": reason,
                }

        redacted_content = content
        reasons = []
        sensitive = False

        for reason, pattern in self.REDACT_PATTERNS:
            def _replacer(match):
                if reason == "credential_assignment":
                    return f"{match.group(1)}{match.group(2)} [REDACTED]"
                return f"{match.group(1)}[REDACTED]"

            updated, count = pattern.subn(_replacer, redacted_content)
            if count:
                sensitive = True
                reasons.append(reason)
                redacted_content = updated

        return {
            "blocked": False,
            "sensitive": sensitive,
            "title": title,
            "content": redacted_content,
            "reason": ",".join(reasons),
        }

    @staticmethod
    def _merge_tags(existing: str, new_value: Optional[str]) -> str:
        tokens = []
        seen = set()
        for raw in [existing, new_value or ""]:
            for item in str(raw).split(","):
                tag = item.strip()
                if tag and tag not in seen:
                    seen.add(tag)
                    tokens.append(tag)
        return ",".join(tokens)

    def _merge_metadata(self, existing: Dict[str, Any], new_value: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(existing or {})
        for key, value in (new_value or {}).items():
            if value in (None, "", [], {}):
                continue
            if key in merged and isinstance(merged[key], list) and isinstance(value, list):
                seen = []
                for item in merged[key] + value:
                    if item not in seen:
                        seen.append(item)
                merged[key] = seen
            elif key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self._merge_metadata(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _merge_contents(self, existing: str, new_value: str, *, schema_type: str) -> str:
        if not existing:
            return new_value
        if not new_value or new_value in existing:
            return existing
        if schema_type == "issue":
            return f"{existing}\n\n补充方案：\n{new_value}"
        return self._merge_text(existing, new_value)

    @staticmethod
    def _merge_text(existing: str, new_value: str) -> str:
        if not existing:
            return new_value
        if not new_value or new_value in existing:
            return existing
        existing_lines = [line.strip() for line in existing.splitlines() if line.strip()]
        for line in [line.strip() for line in new_value.splitlines() if line.strip()]:
            if line not in existing_lines:
                existing_lines.append(line)
        return "\n".join(existing_lines)

    def _embed_text(self, text: str) -> List[float]:
        text = (text or "").strip().lower()
        if not text:
            return []

        vector = [0.0] * self.EMBEDDING_DIMENSIONS
        tokens = []

        try:
            word_tokens = self.tokenizer.tokenize(text)
            tokens.extend(word_tokens)
            tokens.extend(self.tokenizer.extract_keywords(text, top_k=5))
        except Exception:
            tokens.extend([item for item in re.split(r"\W+", text) if item])

        is_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))

        if is_chinese:
            if len(word_tokens) >= 2:
                for i in range(len(word_tokens) - 1):
                    tokens.append(word_tokens[i] + word_tokens[i + 1])

            if HAS_PINYIN:
                try:
                    py_tokens = [p[0] for p in pinyin(text, style=Style.NORMAL) if p and p[0]]
                    tokens.extend(py_tokens)
                    for i in range(len(py_tokens) - 1):
                        tokens.append(py_tokens[i] + py_tokens[i + 1])
                except Exception:
                    pass
        else:
            compact = re.sub(r"\s+", "", text)
            if len(compact) >= 3:
                tokens.extend(compact[i:i + 3] for i in range(len(compact) - 2))
            elif compact:
                tokens.append(compact)

        for index, token in enumerate(tokens):
            if not token:
                continue
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
            bucket = int(digest[:8], 16) % self.EMBEDDING_DIMENSIONS
            sign = -1.0 if int(digest[8:10], 16) % 2 else 1.0
            weight = 1.0 + min(len(token), 8) / 8.0
            if index < 8:
                weight += 0.25
            vector[bucket] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return []
        return [round(value / norm, 8) for value in vector]

    @staticmethod
    def _cosine_similarity(left: List[float], right: List[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        return sum(a * b for a, b in zip(left, right))

    @staticmethod
    def _gen_source_id(prefix: str = "manual") -> str:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"{prefix}:{stamp}"
