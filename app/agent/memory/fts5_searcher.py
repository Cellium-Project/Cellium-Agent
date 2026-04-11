# -*- coding: utf-8 -*-
"""
FTS5 记忆搜索器 - 支持中文分词增强
"""

import os
import sqlite3
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from .chinese_tokenizer import get_tokenizer

logger = logging.getLogger(__name__)


class FTS5MemorySearcher:
    """基于 SQLite FTS5 的全文检索搜索器"""

    def __init__(self, memory_dir: str = "memory", db_name: str = "memory_index.db"):
        self.memory_dir = memory_dir
        self.db_path = os.path.join(memory_dir, db_name)
        self.conn: Optional[sqlite3.Connection] = None
        self.cursor: Optional[sqlite3.Cursor] = None
        self._initialized = False
        self.tokenizer = get_tokenizer()

    def _ensure_connected(self):
        """延迟连接：首次调用时才建立 SQLite 连接"""
        if self.conn is not None:
            return

        os.makedirs(self.memory_dir, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)

        self.conn.row_factory = sqlite3.Row
        self.conn.text_factory = str
        self.cursor = self.conn.cursor()
        self._init_db()
        self._migrate_if_needed()
        logger.debug("[FTS5] SQLite 连接已建立: %s", self.db_path)

    def _init_db(self):
        """初始化 FTS5 虚拟表"""
        self.cursor.execute(
            '''
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_index USING fts5(
                title,
                content,
                tokens,
                category UNINDEXED,
                tags,
                source_file UNINDEXED,
                created_at UNINDEXED,
                tokenize = "unicode61"
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS memory_stats (
                rowid INTEGER PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                last_used_at TEXT
            )
            '''
        )

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS memory_dedup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_hash TEXT UNIQUE NOT NULL,
                fts_rowid INTEGER,
                first_seen_at TEXT,
                title TEXT,
                category TEXT
            )
            '''
        )

        self.conn.commit()

    def _migrate_if_needed(self):
        """迁移旧数据：为已有记录生成分词字段"""
        try:
            self.cursor.execute("PRAGMA table_info(memory_index)")
            columns = [row[1] for row in self.cursor.fetchall()]
            if "tokens" not in columns:
                logger.info("[FTS5] 检测到旧表结构，开始迁移...")
                self._rebuild_with_tokens()
        except Exception as e:
            logger.warning("[FTS5] 迁移检查失败: %s", e)

    def _rebuild_with_tokens(self):
        """重建表以添加分词字段"""
        try:
            self.cursor.execute("SELECT rowid, title, content, category, tags, source_file, created_at FROM memory_index")
            old_data = self.cursor.fetchall()
            if not old_data:
                return

            logger.info("[FTS5] 迁移 %d 条记录", len(old_data))
            self.cursor.execute("DROP TABLE IF EXISTS memory_index")
            self._init_db()

            for row in old_data:
                rowid, title, content, category, tags, source_file, created_at = row
                tokens = self.tokenizer.tokenize_for_search(f"{title} {content}")
                self.cursor.execute(
                    '''
                    INSERT INTO memory_index (rowid, title, content, tokens, category, tags, source_file, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (rowid, title, content, tokens, category, tags, source_file, created_at),
                )

            self.conn.commit()
            logger.info("[FTS5] 迁移完成")
        except Exception as e:
            logger.error("[FTS5] 迁移失败: %s", e)

    # ============================================================
    # 写入/更新
    # ============================================================

    def insert_memory(
        self,
        title: str,
        content: str,
        category: str = "general",
        tags: str = "",
        source_file: str = "",
        return_existing: bool = True,
    ) -> Optional[int]:
        """插入记忆，返回 rowid。"""
        import hashlib

        self._ensure_connected()
        normalized_title = (title or "").strip()
        normalized_content = (content or "").strip()
        content_hash = hashlib.md5((normalized_title + "::" + normalized_content).encode("utf-8")).hexdigest()

        self.cursor.execute(
            "SELECT fts_rowid FROM memory_dedup WHERE content_hash = ?",
            (content_hash,),
        )
        existing = self.cursor.fetchone()
        if existing:
            logger.debug("[FTS5] 去重命中 | hash=%s...", content_hash[:12])
            return int(existing[0]) if return_existing and existing[0] is not None else None

        tokens = self.tokenizer.tokenize_for_search(f"{normalized_title} {normalized_content}")
        created_at = datetime.now().isoformat()
        self.cursor.execute(
            '''
            INSERT INTO memory_index (title, content, tokens, category, tags, source_file, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (normalized_title, normalized_content, tokens, category, tags, source_file, created_at),
        )
        fts_rowid = int(self.cursor.execute("SELECT last_insert_rowid()").fetchone()[0])

        self.cursor.execute(
            '''
            INSERT OR IGNORE INTO memory_stats (rowid, usage_count, last_used_at)
            VALUES (?, 0, NULL)
            ''',
            (fts_rowid,),
        )
        self.cursor.execute(
            '''
            INSERT INTO memory_dedup (content_hash, fts_rowid, first_seen_at, title, category)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (content_hash, fts_rowid, created_at, normalized_title, category),
        )
        self.conn.commit()
        logger.debug("[FTS5] 新增记忆 | rowid=%s | title=%s", fts_rowid, normalized_title[:30])
        return fts_rowid

    def add_memory(self, title: str, content: str, category: str = "general", tags: str = "", source_file: str = "") -> bool:
        """插入记忆（兼容旧接口）"""
        return self.insert_memory(title, content, category=category, tags=tags, source_file=source_file) is not None

    def update_memory(
        self,
        source_file: str,
        title: str,
        content: str,
        category: str = "general",
        tags: str = "",
        rowid: Optional[int] = None,
    ) -> bool:
        """更新记忆，支持按 rowid 或 source_file 定位。"""
        self._ensure_connected()
        target_rowid = rowid

        if target_rowid is None:
            self.cursor.execute(
                "SELECT rowid FROM memory_index WHERE source_file = ? ORDER BY rowid DESC LIMIT 1",
                (source_file,),
            )
            row = self.cursor.fetchone()
            target_rowid = int(row[0]) if row else None

        if target_rowid is None:
            return False

        tokens = self.tokenizer.tokenize_for_search(f"{title} {content}")
        self.cursor.execute(
            '''
            INSERT OR REPLACE INTO memory_index
            (rowid, title, content, tokens, category, tags, source_file, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM memory_index WHERE rowid = ?), ?))
            ''',
            (target_rowid, title, content, tokens, category, tags, source_file, target_rowid, datetime.now().isoformat()),
        )
        self.cursor.execute(
            '''
            INSERT OR IGNORE INTO memory_stats (rowid, usage_count, last_used_at)
            VALUES (?, 0, NULL)
            ''',
            (target_rowid,),
        )
        self.conn.commit()
        return True

    # ============================================================
    # 读取 / 搜索
    # ============================================================

    def get_memory(self, *, rowid: Optional[int] = None, source_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
        self._ensure_connected()
        if rowid is None and not source_file:
            return None

        sql = "SELECT rowid, title, content, category, tags, source_file, created_at FROM memory_index"
        params: List[Any] = []
        if rowid is not None:
            sql += " WHERE rowid = ?"
            params.append(rowid)
        else:
            sql += " WHERE source_file = ? ORDER BY rowid DESC LIMIT 1"
            params.append(source_file)

        self.cursor.execute(sql, params)
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "rowid": int(row["rowid"]),
            "title": row["title"],
            "content": row["content"],
            "category": row["category"],
            "tags": row["tags"],
            "source_file": row["source_file"],
            "created_at": row["created_at"],
        }

    def list_memories(self, limit: Optional[int] = 1000) -> List[Dict[str, Any]]:
        self._ensure_connected()
        sql = "SELECT rowid, title, content, category, tags, source_file, created_at FROM memory_index ORDER BY rowid ASC"
        params: List[Any] = []
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        return [
            {
                "rowid": int(row["rowid"]),
                "title": row["title"],
                "content": row["content"],
                "category": row["category"],
                "tags": row["tags"],
                "source_file": row["source_file"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def search(
        self,
        query: str,
        top_k: int = 3,
        category: str = None,
        use_usage_boost: bool = True,
        use_time_decay: bool = True,
    ) -> List[Dict]:
        """增强搜索：分词 + 多变体查询 + LIKE 兜底"""
        self._ensure_connected()
        query_variants = self._prepare_query_variants(query)
        all_results = []

        for variant in query_variants:
            results = self._fts5_search(variant, top_k, category, use_usage_boost, use_time_decay)
            all_results.extend(results)
            if len(all_results) >= top_k:
                break

        seen = set()
        unique_results = []
        for item in all_results:
            key = item.get("rowid") or item.get("source_file", "") or item.get("content", "")[:50]
            if key in seen:
                continue
            seen.add(key)
            unique_results.append(item)

        unique_results.sort(key=lambda item: item.get("score", 0), reverse=True)
        if len(unique_results) < top_k:
            like_results = self._fallback_search(query, top_k, category)
            for item in like_results:
                key = item.get("rowid") or item.get("source_file", "") or item.get("content", "")[:50]
                if key in seen:
                    continue
                seen.add(key)
                unique_results.append(item)

        return unique_results[:top_k]

    def _prepare_query_variants(self, query: str) -> List[str]:
        variants = [query]
        tokens = self.tokenizer.tokenize(query)
        if tokens and len(tokens) > 1:
            variants.append(" ".join(tokens))
            if len(tokens) == 2:
                variants.append(f"{tokens[0]} NEAR/5 {tokens[1]}")

        keywords = self.tokenizer.extract_keywords(query, top_k=2)
        for keyword in keywords:
            if len(keyword) >= 2 and keyword not in variants:
                variants.append(keyword)
        return variants[:5]

    def _fts5_search(self, query: str, top_k: int, category: str, use_usage_boost: bool, use_time_decay: bool) -> List[Dict]:
        safe_query = self._escape_fts5(query)
        sql = '''
            SELECT m.rowid, m.title, m.content, m.category, m.tags, m.source_file,
                   m.created_at,
                   bm25(memory_index, 10.0, 5.0, 3.0, 1.0, 1.0) as bm25_rank,
                   COALESCE(s.usage_count, 0) as usage_count,
                   s.last_used_at,
                   (-bm25(memory_index, 10.0, 5.0, 3.0, 1.0, 1.0))
        '''
        if use_usage_boost:
            sql += ' + (LOG10(COALESCE(s.usage_count, 0) + 1) * 2.0)'
        if use_time_decay:
            sql += '''
                + (CASE
                     WHEN s.last_used_at IS NOT NULL
                     THEN EXP(-(julianday('now') - julianday(s.last_used_at)) / 30.0)
                     ELSE EXP(-(julianday('now') - julianday(m.created_at)) / 30.0)
                   END * 3.0)
            '''
        sql += ''' as final_score
            FROM memory_index m
            LEFT JOIN memory_stats s ON m.rowid = s.rowid
            WHERE memory_index MATCH ?
        '''

        params: List[Any] = [safe_query]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY final_score DESC LIMIT ?"
        params.append(top_k)

        try:
            self.cursor.execute(sql, params)
            rows = self.cursor.fetchall()
            return [
                {
                    "rowid": int(row["rowid"]),
                    "title": row["title"],
                    "content": row["content"],
                    "category": row["category"],
                    "tags": row["tags"],
                    "source_file": row["source_file"],
                    "created_at": row["created_at"],
                    "score": row["final_score"],
                    "bm25_score": -row["bm25_rank"] if isinstance(row["bm25_rank"], (int, float)) else 0,
                    "usage_count": row["usage_count"] if isinstance(row["usage_count"], (int, float)) else 0,
                    "last_used_at": row["last_used_at"],
                }
                for row in rows
            ]
        except sqlite3.OperationalError as e:
            logger.debug("[FTS5] MATCH 失败: %s", e)
            return []
        except Exception as e:
            logger.warning("[FTS5] 搜索异常: %s", e)
            return []

    def _fallback_search(self, query: str, top_k: int = 3, category: str = None) -> List[Dict]:
        sql = '''
            SELECT rowid, title, content, category, tags, source_file, created_at,
                   1.0 as final_score, 0 as bm25_score, 0 as usage_count, NULL as last_used_at
            FROM memory_index
            WHERE title LIKE ? OR content LIKE ? OR tokens LIKE ?
        '''
        like_pattern = f"%{query}%"
        params: List[Any] = [like_pattern, like_pattern, like_pattern]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " LIMIT ?"
        params.append(top_k)

        try:
            self.cursor.execute(sql, params)
            rows = self.cursor.fetchall()
            return [
                {
                    "rowid": int(row["rowid"]),
                    "title": row["title"],
                    "content": row["content"],
                    "category": row["category"],
                    "tags": row["tags"],
                    "source_file": row["source_file"],
                    "created_at": row["created_at"],
                    "score": 1.0,
                    "bm25_score": 0,
                    "usage_count": 0,
                    "last_used_at": None,
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning("[FTS5] LIKE 搜索失败: %s", e)
            return []

    # ============================================================
    # 统计
    # ============================================================

    def increment_usage(self, source_file: Optional[str] = None, rowid: Optional[int] = None):
        self._ensure_connected()
        now = datetime.now().isoformat()
        if rowid is not None:
            self.cursor.execute(
                '''
                UPDATE memory_stats
                SET usage_count = usage_count + 1,
                    last_used_at = ?
                WHERE rowid = ?
                ''',
                (now, rowid),
            )
        elif source_file:
            self.cursor.execute(
                '''
                UPDATE memory_stats
                SET usage_count = usage_count + 1,
                    last_used_at = ?
                WHERE rowid IN (
                    SELECT rowid FROM memory_index WHERE source_file = ?
                )
                ''',
                (now, source_file),
            )
        self.conn.commit()

    def get_usage_stats(self) -> Dict:
        self._ensure_connected()
        self.cursor.execute(
            '''
            SELECT COUNT(*) as total,
                   SUM(usage_count) as total_usage,
                   AVG(usage_count) as avg_usage
            FROM memory_stats
            '''
        )
        row = self.cursor.fetchone()
        return {
            "total_memories": row["total"],
            "total_usage": row["total_usage"] or 0,
            "avg_usage": row["avg_usage"] or 0,
        }

    def get_stats(self) -> Dict:
        self._ensure_connected()
        self.cursor.execute("SELECT COUNT(*) as count FROM memory_index")
        count = self.cursor.fetchone()["count"]
        self.cursor.execute("SELECT category, COUNT(*) as count FROM memory_index GROUP BY category")
        categories = {row["category"]: row["count"] for row in self.cursor.fetchall()}
        return {
            "total_documents": count,
            "categories": categories,
        }

    @staticmethod
    def _escape_fts5(query: str) -> str:
        escaped = ""
        for char in query:
            if char in '"*-.+,:()':
                escaped += f'"{char}"'
            else:
                escaped += char
        return escaped or query

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
            self.cursor = None
