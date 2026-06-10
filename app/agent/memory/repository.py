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
import sqlite3
import struct
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
        self.vector_db_path = os.path.join(memory_dir, "memory_vectors_api.db")
        self.tfidf_db_path = os.path.join(memory_dir, "memory_vectors_tfidf.db")
        os.makedirs(memory_dir, exist_ok=True)
        self._catalog: Dict[str, Any] = {"version": 1, "records": {}}
        self._load_catalog()
        self._init_vector_db()
        self._query_embedding_cache: Dict[str, List[float]] = {}
        self._query_cache_max_size = 100
        self._embedding_config = self._load_embedding_config()
        self._embedding_dimensions = self._embedding_config.get("dimensions", self.EMBEDDING_DIMENSIONS)
        self._api_embedding_cache: Dict[str, List[float]] = {}
        self._api_cache_max_size = 1000
        self._register_config_callback()
        self._backfill_from_index()

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

    def _load_embedding_config(self) -> Dict[str, Any]:
        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()
            embedding_cfg = cfg.get("memory.long_term.embedding", {})
            if embedding_cfg.get("enabled", False):
                if not embedding_cfg.get("api_key"):
                    embedding_cfg["api_key"] = cfg.get("llm.openai.api_key", "")
                if not embedding_cfg.get("base_url"):
                    embedding_cfg["base_url"] = cfg.get("llm.openai.base_url", "https://api.openai.com/v1")
                if not embedding_cfg.get("model"):
                    embedding_cfg["model"] = "text-embedding-3-small"
                embedding_cfg.setdefault("dimensions", None)
                embedding_cfg.setdefault("fallback_to_tfidf", True)
                return embedding_cfg
        except Exception:
            pass
        return {"enabled": False, "dimensions": self.EMBEDDING_DIMENSIONS}

    def _register_config_callback(self):
        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()

            def on_memory_config_change(section_name, old_value, new_value):
                logger.info("[MemoryRepository] 检测到 memory 配置变更，重新加载 embedding 配置")
                old_embedding = self._embedding_config
                self._embedding_config = self._load_embedding_config()
                new_model = self._embedding_config.get("model", "")
                old_model = old_embedding.get("model", "")
                new_enabled = self._embedding_config.get("enabled", False)
                old_enabled = old_embedding.get("enabled", False)
                
                if new_model and new_model != old_model:
                    logger.info("[MemoryRepository] 模型切换: %s -> %s，清空缓存", old_model, new_model)
                    self._api_embedding_cache.clear()
                    self._query_embedding_cache.clear()
                    self._embedding_dimensions = self._embedding_config.get("dimensions", self.EMBEDDING_DIMENSIONS)
                
                if new_enabled and not old_enabled:
                    logger.info("[MemoryRepository] 向量 API 已启用，启动后台向量迁移")
                    self._start_background_embedding_migration()

            cfg.on_change("memory", on_memory_config_change)
        except Exception as e:
            logger.debug("[MemoryRepository] 注册配置回调失败: %s", e)

    # ============================================================
    # Vector Storage (SQLite BLOB)
    # ============================================================

    def _init_vector_db(self):
        conn = sqlite3.connect(self.vector_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_vectors (
                record_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        conn = sqlite3.connect(self.tfidf_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_vectors (
                record_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _vector_to_blob(self, vector: List[float]) -> bytes:
        return struct.pack(f"{len(vector)}f", *vector)

    def _blob_to_vector(self, blob: bytes, dimensions: int) -> List[float]:
        return list(struct.unpack(f"{dimensions}f", blob))

    def _save_vector(self, record_id: str, vector: List[float]):
        if not vector:
            return
        is_tfidf = len(vector) == self.EMBEDDING_DIMENSIONS
        db_path = self.tfidf_db_path if is_tfidf else self.vector_db_path
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        blob = self._vector_to_blob(vector)
        cursor.execute(
            "INSERT OR REPLACE INTO memory_vectors (record_id, embedding, dimensions, updated_at) VALUES (?, ?, ?, ?)",
            (record_id, blob, len(vector), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    def _load_vector(self, record_id: str, prefer_api: bool = True) -> Optional[List[float]]:
        for db_path in ([self.vector_db_path, self.tfidf_db_path] if prefer_api else [self.tfidf_db_path, self.vector_db_path]):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT embedding, dimensions FROM memory_vectors WHERE record_id = ?", (record_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return self._blob_to_vector(row[0], row[1])
        return None

    def _load_vectors_batch(self, record_ids: List[str], prefer_api: bool = True) -> Dict[str, List[float]]:
        if not record_ids:
            return {}
        db_path = self.vector_db_path if prefer_api else self.tfidf_db_path
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        placeholders = ",".join(["?"] * len(record_ids))
        cursor.execute(f"SELECT record_id, embedding, dimensions FROM memory_vectors WHERE record_id IN ({placeholders})", record_ids)
        results = {}
        for record_id, blob, dimensions in cursor.fetchall():
            results[record_id] = self._blob_to_vector(blob, dimensions)
        conn.close()
        return results

    def _get_record_ids_with_vectors(self, dimensions: int) -> List[str]:
        conn = sqlite3.connect(self.vector_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT record_id FROM memory_vectors WHERE dimensions = ?", (dimensions,))
        record_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        return record_ids

    def _delete_vector(self, record_id: str):
        for db_path in [self.vector_db_path, self.tfidf_db_path]:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memory_vectors WHERE record_id = ?", (record_id,))
            conn.commit()
            conn.close()

    def _migrate_embeddings_to_db(self):
        records = self._catalog.get("records", {})
        migrated = 0
        for record_id, record in records.items():
            if "embedding" in record:
                vector = record["embedding"]
                if vector:
                    self._save_vector(record_id, vector)
                    migrated += 1
                del record["embedding"]
        if migrated > 0:
            self._save_catalog()
            logger.info("[MemoryRepository] 迁移 %d 个向量到 SQLite", migrated)

    def _start_background_embedding_migration(self):
        """后台异步迁移旧记忆的向量到 API 向量"""
        import threading
        
        if getattr(self, '_migration_running', False):
            logger.info("[MemoryRepository] 迁移任务已在运行中")
            return
        
        def migration_task():
            try:
                self._migration_running = True
                self._migration_status = {
                    "running": True,
                    "total": 0,
                    "migrated": 0,
                    "failed": 0,
                }
                
                logger.info("[MemoryRepository] 开始后台向量迁移...")
                records = self._catalog.get("records", {})
                total = len([r for r in records.values() if r.get("status") == "active"])
                migrated = 0
                failed = 0
                
                self._migration_status["total"] = total
                
                for record_id, record in records.items():
                    if record.get("status") != "active":
                        continue
                    
                    existing_vector = self._load_vector(record_id, prefer_api=True)
                    if existing_vector and len(existing_vector) == self._embedding_dimensions:
                        continue
                    
                    content = record.get("content", "")
                    title = record.get("title", "")
                    text = f"{title}\n{content}".strip()
                    
                    if not text:
                        continue
                    
                    try:
                        vector = self._call_embedding_api(text)
                        if vector:
                            self._save_vector(record_id, vector)
                            migrated += 1
                            self._migration_status["migrated"] = migrated
                            if migrated % 10 == 0:
                                logger.info("[MemoryRepository] 向量迁移进度: %d/%d", migrated, total)
                        else:
                            failed += 1
                            self._migration_status["failed"] = failed
                    except Exception as e:
                        failed += 1
                        self._migration_status["failed"] = failed
                        logger.debug("[MemoryRepository] 向量迁移失败 | record_id=%s | error=%s", record_id, e)
                    
                    import time
                    time.sleep(0.1)
                
                self._migration_status["running"] = False
                logger.info("[MemoryRepository] 向量迁移完成 | 成功=%d | 失败=%d | 总计=%d", migrated, failed, total)
                
                try:
                    from app.server.routes.ws_event_manager import ws_publish_event
                    ws_publish_event("embedding_migration_complete", {
                        "migrated": migrated,
                        "failed": failed,
                        "total": total,
                    })
                except Exception:
                    pass
                    
            except Exception as e:
                self._migration_running = False
                self._migration_status["running"] = False
                logger.error("[MemoryRepository] 后台向量迁移失败: %s", e)
            finally:
                self._migration_running = False
        
        thread = threading.Thread(target=migration_task, daemon=True)
        thread.start()
        logger.info("[MemoryRepository] 后台向量迁移线程已启动")

    def _save_catalog(self):
        catalog_copy = {
            "version": self._catalog.get("version", 1),
            "_meta": self._catalog.get("_meta", {}),
            "records": {}
        }
        for record_id, record in self._catalog.get("records", {}).items():
            clean_record = {k: v for k, v in record.items() if k != "embedding"}
            catalog_copy["records"][record_id] = clean_record
        with open(self.catalog_path, "w", encoding="utf-8") as f:
            json.dump(catalog_copy, f, ensure_ascii=False, indent=2)

    def _backfill_from_index(self):
        changed = False
        records = self._catalog.setdefault("records", {})

        last_indexed_at = self._catalog.get("_meta", {}).get("last_indexed_at")
        if last_indexed_at:
            items = self.searcher.list_memories(limit=100000, since=last_indexed_at)
        else:
            items = self.searcher.list_memories(limit=100000)

        for item in items:
            record_id = str(item["rowid"])
            if record_id in records:
                existing = records[record_id]
                existing_updated = item.get("updated_at") or item.get("created_at", "")
                if existing.get("updated_at") and existing_updated and existing_updated > existing.get("updated_at"):
                    existing["updated_at"] = existing_updated
                    changed = True
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
                updated_at=item.get("updated_at") or item.get("created_at") or datetime.now().isoformat(),
                revisions=1,
            )
            changed = True

        if items or changed:
            max_updated = self.searcher.get_max_updated_at()
            if max_updated:
                self._catalog.setdefault("_meta", {})["last_indexed_at"] = max_updated

        if changed:
            self._save_catalog()

        self._check_embedding_migration()
        self._migrate_embeddings_to_db()

    def _check_embedding_migration(self):
        if not self._embedding_config.get("enabled", False):
            return

        current_model = self._embedding_config.get("model", "")
        stored_model = self._catalog.get("_meta", {}).get("embedding_model", "")
        stored_dim = self._catalog.get("_meta", {}).get("embedding_dimensions", 0)

        if current_model and current_model != stored_model:
            logger.info(
                "[MemoryRepository] 检测到 embedding 模型切换: %s -> %s，将在下次调用时重新检测维度",
                stored_model or "未配置", current_model
            )
            self._embedding_config["dimensions"] = None
            self._embedding_dimensions = self.EMBEDDING_DIMENSIONS
            self._api_embedding_cache.clear()
            self._query_embedding_cache.clear()
            return

        config_dim = self._embedding_config.get("dimensions")
        if config_dim and config_dim != stored_dim:
            logger.info(
                "[MemoryRepository] 检测到 embedding 维度变化: %d -> %d，将在检索时自动迁移",
                stored_dim, config_dim
            )
            self._catalog.setdefault("_meta", {})["embedding_dimensions"] = config_dim
            self._catalog.setdefault("_meta", {})["embedding_model"] = current_model
            self._save_catalog()
            self._embedding_dimensions = config_dim
        elif stored_dim and stored_dim > 0:
            self._embedding_dimensions = stored_dim

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
                }
            )
            new_embedding = self._embed_text(merged_title + "\n" + merged_content)
            if new_embedding:
                self._save_vector(record_id, new_embedding)
            
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
        existing_entry = self._catalog.setdefault("records", {}).get(record_id)
        revisions = (existing_entry.get("revisions", 0) + 1) if existing_entry else 1
        self._catalog["records"][record_id] = self._build_catalog_entry(
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
            created_at=(existing_entry or {}).get("created_at") or datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            revisions=revisions,
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

    RRF_K = 60

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        category: Optional[str] = None,
        note_type: Optional[str] = None,
        schema_type: Optional[str] = None,
        include_sensitive: bool = False,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []

        query = self._expand_date_query(query)

        self._backfill_from_index()
        fetch_top_k = max(top_k * 4, 8)
        fts_results = self.searcher.search(
            query,
            top_k=fetch_top_k,
            category=category,
            note_type=note_type,
            date_from=date_from,
            date_to=date_to,
            tags=tags,
            source=source,
        )
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

        if date_from or date_to or tags or source:
            embedding_results = self._apply_structured_filters(
                embedding_results, date_from=date_from, date_to=date_to, tags=tags, source=source
            )

        return self._rrf_fuse(filtered_fts, embedding_results, query, top_k)

    def _apply_structured_filters(
        self,
        results: List[Dict],
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> List[Dict]:
        import logging
        logger = logging.getLogger(__name__)
        filtered = []
        for item in results:
            record = self._get_catalog_record(item.get("rowid"))
            metadata = (record or {}).get("metadata", {}) or {}
            created_at = (
                item.get("created_at")
                or (record or {}).get("created_at")
                or metadata.get("created_at", "")
            )
            item_tags = (
                item.get("tags")
                or (record or {}).get("tags")
                or metadata.get("tags", "")
            )
            item_source = (
                item.get("source_file")
                or (record or {}).get("source_file")
                or metadata.get("source", "")
            )
            logger.debug(f"[StructuredFilter] rowid={item.get('rowid')} created_at={created_at!r} date_from={date_from!r} source={item_source!r}")
            if date_from and created_at and created_at < date_from:
                logger.debug(f"[StructuredFilter]   - filtered by date_from")
                continue
            if date_to and created_at and created_at > date_to:
                continue
            if tags:
                tag_list = [t.strip() for t in str(item_tags).split(",") if t.strip()]
                if not any(t in tag_list for t in tags):
                    continue
            if source:
                if str(source).lower() not in str(item_source or "").lower():
                    continue
            item["created_at"] = created_at
            item["tags"] = item_tags
            item["source_file"] = item_source
            filtered.append(item)
        return filtered

    def _rrf_fuse(
        self,
        fts_results: List[Dict],
        embedding_results: List[Dict],
        query: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        k = self.RRF_K
        merged: Dict[str, Dict[str, Any]] = {}

        max_bm25 = 0.0
        for r in fts_results:
            s = r.get("bm25_score", 0.0) or 0.0
            if s > max_bm25:
                max_bm25 = s

        for rank, result in enumerate(fts_results, 1):
            record_id = str(result["rowid"])
            entry = merged.setdefault(record_id, self._public_record(record_id))
            if not entry:
                continue
            rrf_score = 1.0 / (k + rank)
            bm25_norm = (result.get("bm25_score", 0.0) or 0.0) / max_bm25 if max_bm25 > 0 else 0.0
            entry["score"] += rrf_score + 0.3 * bm25_norm
            entry.setdefault("search_signals", {})["fts_rank"] = rank
            entry["fts_score"] = result.get("score", 0.0)

        for rank, result in enumerate(embedding_results, 1):
            record_id = str(result["rowid"])
            entry = merged.setdefault(record_id, self._public_record(record_id))
            if not entry:
                continue
            rrf_score = 1.0 / (k + rank)
            emb_score = result.get("embedding_score", 0.0)
            entry["score"] += 0.9 * rrf_score + 0.2 * emb_score
            entry.setdefault("search_signals", {})["embedding_rank"] = rank
            entry["embedding_score"] = emb_score

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
            }
        )
        new_embedding = self._embed_text(safe_title + "\n" + safe_content)
        if new_embedding:
            self._save_vector(record_id, new_embedding)
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
        self._delete_vector(record_id)
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
                }
            )
            new_embedding = self._embed_text(canonical.get("title", "") + "\n" + merged_content)
            if new_embedding:
                self._save_vector(canonical_id, new_embedding)
            merged_groups += 1

        if merged_groups:
            self._save_catalog()
        return {"success": True, "merged_groups": merged_groups, "merged_records": merged_records}

    def increment_usage(self, identifier: str):
        record_id = str(identifier)
        record = None
        
        if record_id.isdigit():
            self.searcher.increment_usage(rowid=int(record_id))
            record = self._get_catalog_record(record_id)
        else:
            record = self._find_record_by_memory_key(record_id)
        
        if record:
            metadata = record.get("metadata", {})
            metadata["usage_count"] = metadata.get("usage_count", 0) + 1
            record["metadata"] = metadata
            self._save_catalog()

    def _find_record_by_memory_key(self, memory_key: str) -> Optional[Dict[str, Any]]:
        """通过 memory_key 查找记录"""
        for rec in self._catalog.get("records", {}).values():
            if rec.get("memory_key") == memory_key:
                return rec
        return None

    def get_record(self, identifier: str) -> Optional[Dict[str, Any]]:
        return self._public_record(str(identifier))

    def get_by_memory_key(self, memory_key: str, schema_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """通过 memory_key 精确查找记录（公共方法）

        Args:
            memory_key: 记忆键名
            schema_type: 可选的 schema_type 过滤

        Returns:
            找到的记录字典，如果不存在返回 None
        """
        found = self._find_by_memory_key(memory_key, schema_type=schema_type)
        if found:
            return self._public_record(found[0])
        return None

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
        query_vector = self._embed_text(query)
        if not query_vector:
            return []

        query_dim = len(query_vector)
        use_api = query_dim != self.EMBEDDING_DIMENSIONS

        valid_record_ids = []
        for record_id, record in self._catalog.get("records", {}).items():
            if self._is_record_searchable(record, category=category, schema_type=schema_type, include_sensitive=include_sensitive):
                valid_record_ids.append(record_id)

        existing_vectors = self._load_vectors_batch(valid_record_ids, prefer_api=use_api)

        def search_vectors(vectors: Dict[str, List[float]], dim: int, query_vec: List[float]):
            vecs = []
            ids = []
            for rid in valid_record_ids:
                v = vectors.get(rid)
                if v and len(v) == dim:
                    vecs.append(v)
                    ids.append(rid)
            if not vecs:
                return []
            if HAS_NUMPY:
                emb = np.array(vecs, dtype=np.float32)
                qv = np.array(query_vec, dtype=np.float32)
                scores = emb @ qv
                top_i = np.argpartition(-scores, min(top_k, len(scores)) - 1)[:top_k]
                top_i = top_i[np.argsort(-scores[top_i])]
                res = []
                for idx in top_i:
                    sc = float(scores[idx])
                    if sc <= 0.08:
                        continue
                    res.append({"rowid": int(ids[idx]), "embedding_score": sc})
                return res
            else:
                scored = []
                for rid, vec in zip(ids, vecs):
                    score = self._cosine_similarity(query_vec, vec)
                    if score <= 0.08:
                        continue
                    scored.append({"rowid": int(rid), "embedding_score": score})
                scored.sort(key=lambda x: x["embedding_score"], reverse=True)
                return scored[:top_k]

        results = search_vectors(existing_vectors, query_dim, query_vector)

        if use_api:
            tfidf_vector = self._embed_text_tfidf(query)
            if tfidf_vector:
                tfidf_vectors = self._load_vectors_batch(valid_record_ids, prefer_api=False)
                tfidf_results = search_vectors(tfidf_vectors, self.EMBEDDING_DIMENSIONS, tfidf_vector)
                
                api_ids = {r["rowid"] for r in results}
                for r in tfidf_results:
                    if r["rowid"] not in api_ids:
                        r["embedding_score"] *= 0.7
                        results.append(r)
                
                results.sort(key=lambda x: x["embedding_score"], reverse=True)
                results = results[:top_k]

        return results if results else []

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
        embedding = self._embed_text(title + "\n" + content)
        if embedding:
            self._save_vector(record_id, embedding)
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

        if self._embedding_config.get("enabled", False):
            return self._call_embedding_api(text)

        return self._embed_text_tfidf(text)

    def _call_embedding_api(self, text: str) -> List[float]:
        cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()
        if cache_key in self._api_embedding_cache:
            return self._api_embedding_cache[cache_key]

        try:
            import httpx
            base_url = self._embedding_config.get("base_url", "https://api.openai.com/v1")
            api_key = self._embedding_config.get("api_key", "")
            model = self._embedding_config.get("model", "text-embedding-3-small")

            base_url = base_url.rstrip("/")
            resp = httpx.post(
                f"{base_url}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"input": text[:8000], "model": model},
                timeout=10.0
            )
            resp.raise_for_status()
            embedding = resp.json()["data"][0]["embedding"]

            detected_dim = len(embedding)
            if self._embedding_dimensions != detected_dim:
                self._embedding_dimensions = detected_dim
                self._embedding_config["dimensions"] = detected_dim
                self._catalog.setdefault("_meta", {})["embedding_dimensions"] = detected_dim
                self._catalog.setdefault("_meta", {})["embedding_model"] = model
                self._save_catalog()
                logger.info("[MemoryRepository] 自动检测到 embedding 维度: %d (模型: %s)", detected_dim, model)

            if len(self._api_embedding_cache) < self._api_cache_max_size:
                self._api_embedding_cache[cache_key] = embedding
            return embedding
        except Exception as e:
            logger.debug("[MemoryRepository] Embedding API 调用失败: %s，回退到 TF-IDF", e)
            if self._embedding_config.get("fallback_to_tfidf", True):
                return self._embed_text_tfidf(text)
            return []

    def _embed_text_tfidf(self, text: str) -> List[float]:
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
