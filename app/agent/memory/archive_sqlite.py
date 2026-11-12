# -*- coding: utf-8 -*-

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional

from .archive_store import ArchiveStore

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS records(
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time TEXT,
    snapshot_hash TEXT,
    seq INTEGER NOT NULL,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS deleted_records(
    id TEXT PRIMARY KEY,
    deleted_at REAL
);
CREATE TABLE IF NOT EXISTS truncated_records(
    record_id TEXT PRIMARY KEY,
    stop_message_id TEXT
);
CREATE TABLE IF NOT EXISTS record_messages(
    record_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    msg_hash TEXT NOT NULL,
    message_id TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY(record_id, ordinal)
);
CREATE TABLE IF NOT EXISTS message_hashes(
    session_id TEXT NOT NULL,
    msg_hash TEXT NOT NULL,
    PRIMARY KEY(session_id, msg_hash)
);
CREATE TABLE IF NOT EXISTS import_state(
    file TEXT PRIMARY KEY,
    size INTEGER,
    mtime_ns INTEGER,
    done INTEGER
);
CREATE TABLE IF NOT EXISTS session_revision(
    session_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_record_messages_message ON record_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_records_time ON records(time);
CREATE INDEX IF NOT EXISTS idx_records_session_seq ON records(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_record_messages_record ON record_messages(record_id, ordinal);
"""

_RECORD_COLUMNS = "id, session_id, time, snapshot_hash, raw"
_SEQ_LIVE_BASE = 1_000_000_000_000_000
_WRITE_RETRY_DELAYS = (0.05, 0.15, 0.4)
_SNAPSHOT_FLAG = '"_is_compacted_notes"'


class SqliteArchiveStore:
    def __init__(self, db_path: str, jsonl_dir: str = None, dual_write: bool = False):
        self.db_path = db_path
        self.base_dir = jsonl_dir
        self.dual_write = dual_write
        if db_path != ":memory:":
            directory = os.path.dirname(os.path.abspath(db_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._legacy_files = self._list_jsonl(jsonl_dir)
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._ensure_columns()
        self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('seq', '0')")
        self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('revision', '0')")
        self._read_local = threading.local()
        self._read_conns = set()
        self._read_conns_lock = threading.Lock()
        self._last_flush = 0.0
        self._state = threading.Condition()
        self._active_reads = 0
        self._closed = False

    @contextmanager
    def _reading(self):
        with self._state:
            if self._closed:
                raise sqlite3.ProgrammingError("Cannot operate on a closed database")
            self._active_reads += 1
        try:
            yield self._read_conn()
        finally:
            with self._state:
                self._active_reads -= 1
                if self._active_reads == 0:
                    self._state.notify_all()

    def _ensure_open(self) -> None:
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database")

    def _read_conn(self):
        if self.db_path == ":memory:":
            return self._conn
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            self._read_local.conn = conn
            with self._read_conns_lock:
                self._read_conns.add(conn)
        return conn

    def _ensure_columns(self) -> None:
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(records)").fetchall()}
        if "raw" not in cols:
            self._conn.execute("ALTER TABLE records ADD COLUMN raw TEXT")

    def flush(self, min_interval: float = 30.0) -> None:
        """WAL 合并回主库：PASSIVE 不等读者，并按时长节流，避免多会话互相卡住"""
        if self.db_path == ":memory:":
            return
        now = time.time()
        if now - self._last_flush < min_interval:
            return
        with self._lock:
            if self._closed:
                return
            try:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self._last_flush = now
            except sqlite3.Error as e:
                logger.debug("[SqliteArchiveStore] checkpoint 失败: %s", e)

    def close(self) -> None:
        with self._lock:
            with self._state:
                self._closed = True
                while self._active_reads > 0:
                    self._state.wait(timeout=1.0)
            with self._read_conns_lock:
                conns = list(self._read_conns)
                self._read_conns.clear()
                self._read_local.__dict__.clear()
            for conn in conns:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def append(self, user_input: str, response: str, session_id: str = "default") -> str:
        return self.append_messages(
            session_id=session_id,
            messages=[
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response},
            ],
        )

    def append_messages(
        self,
        session_id: str = "default",
        messages: list = None,
        snapshot_hash: Optional[str] = None,
    ) -> str:
        normalized = messages or []
        is_snapshot = any(
            isinstance(m, dict) and m.get("_is_compacted_notes") for m in normalized
        )
        with self._lock:
            self._ensure_open()
            fresh = []
            local_hashes = set()
            for m in normalized:
                if isinstance(m, dict) and not is_snapshot:
                    msg_hash = self._msg_hash(m)
                    if msg_hash in local_hashes or self._hash_exists(session_id, msg_hash):
                        continue
                    local_hashes.add(msg_hash)
                fresh.append(m)

            if not fresh:
                latest = self.get_latest_by_session(session_id)
                logger.debug(
                    "[SqliteArchiveStore] 消息全部命中去重，跳过写入 | session=%s | incoming=%d | latest=%s",
                    session_id, len(normalized), (latest or {}).get("id", "")[:12],
                )
                return latest.get("id", "") if latest else ""

            now = datetime.now().astimezone().isoformat()
            record_id = self._gen_id(fresh, timestamp=now)
            record = {
                "id": record_id,
                "time": now,
                "session_id": session_id,
                "messages": fresh,
                "snapshot_hash": ArchiveStore._hash_messages(fresh),
            }

            try:
                self._write_with_retry(
                    lambda: self._insert_record(record_id, session_id, fresh, record)
                )
            except Exception:
                raise

            self._append_jsonl(record)
            return record_id

    def _insert_record(self, record_id, session_id, fresh, record) -> None:
        """单条记录写入事务（被 _write_with_retry 重试调用）"""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            seq = self._next_seq(self._conn)
            self._conn.execute(
                "INSERT INTO records(id, session_id, time, snapshot_hash, seq, raw) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (
                    record_id, session_id, record["time"], record["snapshot_hash"], seq,
                    self._raw_record(record),
                ),
            )
            self._insert_messages(self._conn, record_id, session_id, fresh)
            self._bump_revision(self._conn, session_id)
            self._conn.execute("COMMIT")
        except Exception:
            self._safe_rollback(self._conn)
            raise

    def _write_with_retry(self, func):
        """写锁争用短退避重试：busy_timeout 之后只兜短暂的锁抖动"""
        attempts = len(_WRITE_RETRY_DELAYS) + 1
        for i in range(attempts):
            try:
                return func()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if i == attempts - 1 or ("locked" not in msg and "busy" not in msg):
                    raise
                logger.debug("[SqliteArchiveStore] 写锁争用，%.2fs 后重试: %s", _WRITE_RETRY_DELAYS[i], e)
                time.sleep(_WRITE_RETRY_DELAYS[i])

    def get_by_id(self, record_id: str) -> Optional[Dict]:
        with self._reading() as conn:
            row = conn.execute(
                f"SELECT {_RECORD_COLUMNS} FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is not None:
                return self._record_from_row(row)
        legacy = self._legacy_store()
        return legacy.get_by_id(record_id) if legacy else None

    def get_by_session(self, session_id: str, limit: int = 20) -> List[Dict]:
        with self._reading() as conn:
            rows = conn.execute(
                f"SELECT {_RECORD_COLUMNS} FROM records WHERE session_id = ? "
                "ORDER BY seq DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            records = self._records_from_rows(list(reversed(rows)))
        legacy = self._legacy_store()
        if legacy is not None:
            try:
                old = legacy.get_by_session(session_id, limit=limit)
            except Exception:
                old = []
            if old:
                seen = {r.get("id") for r in records}
                merged = [r for r in old if r.get("id") not in seen] + records
                merged.sort(key=lambda r: str(r.get("time") or ""))
                return merged[-limit:]
        return records

    def _legacy_store(self):
        if not self.base_dir:
            return None
        try:
            if self.migration_complete():
                return None
            return ArchiveStore(self.base_dir)
        except Exception:
            return None

    def migration_complete(self) -> bool:
        names = list(self._legacy_files)
        if not names:
            return True
        with self._reading() as conn:
            placeholders = ",".join("?" for _ in names)
            row = conn.execute(
                f"SELECT COUNT(*) FROM import_state WHERE done = 1 AND file IN ({placeholders})",
                names,
            ).fetchone()
        return row[0] >= len(names)

    @staticmethod
    def _list_jsonl(directory) -> List[str]:
        if not directory or not os.path.isdir(directory):
            return []
        try:
            return [f for f in os.listdir(directory) if f.endswith(".jsonl")]
        except OSError:
            return []

    def get_latest_by_session(self, session_id: str) -> Optional[Dict]:
        records = self.get_by_session(session_id, limit=1)
        return records[-1] if records else None

    def get_by_date(self, date: str) -> List[Dict]:
        with self._reading() as conn:
            rows = conn.execute(
                f"SELECT {_RECORD_COLUMNS} FROM records WHERE time >= ? AND time < ? ORDER BY seq",
                (date, date + "\uffff"),
            ).fetchall()
            return self._records_from_rows(rows)

    def delete_after(self, session_id: str, message_id: str) -> int:
        with self._lock:
            self._ensure_open()
            target = self._conn.execute(
                "SELECT rm.record_id, rm.ordinal, r.seq FROM record_messages rm "
                "JOIN records r ON r.id = rm.record_id "
                "WHERE r.session_id = ? AND rm.message_id = ? "
                "ORDER BY r.seq ASC, rm.ordinal ASC LIMIT 1",
                (session_id, message_id),
            ).fetchone()
            if target is None:
                return self.tombstone_after(session_id, message_id)

            target_id = target["record_id"]
            target_ordinal = target["ordinal"]
            target_seq = target["seq"]

            tail_rows = self._conn.execute(
                "SELECT msg_hash FROM record_messages WHERE record_id = ? AND ordinal >= ?",
                (target_id, target_ordinal),
            ).fetchall()
            later_rows = self._conn.execute(
                "SELECT rm.msg_hash FROM record_messages rm "
                "JOIN records r ON r.id = rm.record_id "
                "WHERE r.session_id = ? AND r.seq > ?",
                (session_id, target_seq),
            ).fetchall()
            removed_count = len(tail_rows) + len(later_rows)

            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "DELETE FROM record_messages WHERE record_id = ? AND ordinal >= ?",
                    (target_id, target_ordinal),
                )
                if target_ordinal == 0:
                    self._conn.execute("DELETE FROM records WHERE id = ?", (target_id,))
                later_ids = [
                    row["id"] for row in self._conn.execute(
                        "SELECT id FROM records WHERE session_id = ? AND seq > ?",
                        (session_id, target_seq),
                    ).fetchall()
                ]
                for rid in later_ids:
                    self._conn.execute("DELETE FROM record_messages WHERE record_id = ?", (rid,))
                    self._conn.execute("DELETE FROM records WHERE id = ?", (rid,))
                tombstoned = list(later_ids)
                if target_ordinal == 0:
                    tombstoned.append(target_id)
                self._conn.executemany(
                    "INSERT OR IGNORE INTO deleted_records(id, deleted_at) VALUES(?, ?)",
                    [(rid, time.time()) for rid in tombstoned],
                )
                self._conn.execute(
                    "DELETE FROM message_hashes WHERE session_id = ? AND NOT EXISTS ("
                    "SELECT 1 FROM record_messages rm JOIN records r ON r.id = rm.record_id "
                    "WHERE r.session_id = ? AND rm.msg_hash = message_hashes.msg_hash)",
                    (session_id, session_id),
                )
                self._bump_revision(self._conn, session_id)
                self._conn.execute("COMMIT")
            except Exception:
                self._safe_rollback(self._conn)
                raise
            if self.base_dir and not self.migration_complete():
                try:
                    self.tombstone_after(session_id, message_id)
                except Exception as e:
                    logger.warning("[SqliteArchiveStore] 撤回墓碑写入失败: %s", e)
            elif self.dual_write and self.base_dir:
                try:
                    ArchiveStore(self.base_dir).delete_after(session_id, message_id)
                except Exception as e:
                    logger.warning("[SqliteArchiveStore] 撤回同步 JSONL 失败: %s", e)
            return removed_count

    def tombstone_after(self, session_id: str, message_id: str) -> int:
        if not self.base_dir or self.migration_complete():
            return 0
        from .archive_migrate import tombstone_after
        return tombstone_after(self, self.base_dir, session_id, message_id)

    def list_session_ids(self) -> List[str]:
        with self._reading() as conn:
            rows = conn.execute(
                "SELECT DISTINCT session_id FROM records ORDER BY session_id"
            ).fetchall()
        return [row["session_id"] for row in rows]

    def revision(self, session_id: str = None) -> int:
        with self._reading() as conn:
            if session_id:
                row = conn.execute(
                    "SELECT revision FROM session_revision WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                return int(row["revision"]) if row else 0
            row = conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
            return int(row["value"]) if row else 0

    def bump_revision(self, session_id: str = None) -> int:
        with self._lock:
            self._ensure_open()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._bump_revision(self._conn, session_id)
                self._conn.execute("COMMIT")
            except Exception:
                self._safe_rollback(self._conn)
                raise
            return self.revision(session_id)

    def _record_from_row(self, row: sqlite3.Row) -> Dict:
        data = self._meta_from_row(row)
        data["messages"] = self._load_messages(row["id"])
        return data

    @staticmethod
    def _meta_from_row(row: sqlite3.Row) -> Dict:
        data = {}
        raw = row["raw"]
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    data = loaded
            except (json.JSONDecodeError, TypeError):
                data = {}
        data.update({
            "id": row["id"],
            "time": row["time"],
            "session_id": row["session_id"],
            "snapshot_hash": row["snapshot_hash"],
        })
        return data

    def get_record_meta(self, record_id: str) -> Optional[Dict]:
        with self._reading() as conn:
            row = conn.execute(
                f"SELECT {_RECORD_COLUMNS} FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is not None:
                return self._meta_from_row(row)
        legacy = self._legacy_store()
        return legacy.get_record_meta(record_id) if legacy else None

    def get_last_assistant(self, record_id: str) -> Optional[str]:
        with self._reading() as conn:
            exists = conn.execute(
                "SELECT 1 FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            if exists is None:
                found = False
            else:
                found = True
                cur = conn.execute(
                    "SELECT payload FROM record_messages WHERE record_id = ? "
                    "ORDER BY ordinal DESC",
                    (record_id,),
                )
                try:
                    for row in cur:
                        try:
                            msg = json.loads(row["payload"])
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if isinstance(msg, dict) and msg.get("role") == "assistant":
                            return msg.get("content") or ""
                finally:
                    cur.close()
        if found:
            return None
        legacy = self._legacy_store()
        return legacy.get_last_assistant(record_id) if legacy else None

    def _records_from_rows(self, rows) -> List[Dict]:
        """批量装配记录：一条 SQL 取全部消息，避免逐条查询"""
        metas = [self._meta_from_row(row) for row in rows]
        if not metas:
            return []
        bulk = self._load_messages_bulk([meta["id"] for meta in metas])
        for meta in metas:
            meta["messages"] = bulk.get(meta["id"], [])
        return metas

    def _load_messages_bulk(self, record_ids) -> Dict[str, List]:
        result = {rid: [] for rid in record_ids}
        ids = list(result)
        if not ids:
            return result
        with self._reading() as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT record_id, payload FROM record_messages "
                    f"WHERE record_id IN ({placeholders}) ORDER BY record_id, ordinal",
                    chunk,
                ).fetchall()
                for row in rows:
                    try:
                        result[row["record_id"]].append(json.loads(row["payload"]))
                    except (json.JSONDecodeError, TypeError):
                        continue
        return result

    def _load_messages(self, record_id: str) -> List:
        with self._reading() as conn:
            rows = conn.execute(
                "SELECT payload FROM record_messages WHERE record_id = ? ORDER BY ordinal",
                (record_id,),
            ).fetchall()
        messages = []
        for row in rows:
            try:
                messages.append(json.loads(row["payload"]))
            except (json.JSONDecodeError, TypeError):
                continue
        return messages

    def get_recent_messages(self, session_id: str, limit: int = 2000, offset: int = 0) -> List:
        """按消息取窗口：跳过最新 offset 条后取 limit 条，正序返回（压缩快照忽略）"""
        if limit <= 0:
            return []
        legacy = self._legacy_store()
        if legacy is not None:
            return self._legacy_messages_tail(legacy, session_id, limit, offset)
        with self._reading() as conn:
            rows = conn.execute(
                "SELECT rm.payload FROM record_messages rm JOIN records r ON r.id = rm.record_id "
                f"WHERE r.session_id = ? AND instr(rm.payload, '{_SNAPSHOT_FLAG}') = 0 "
                "ORDER BY r.seq DESC, rm.ordinal DESC LIMIT ? OFFSET ?",
                (session_id, limit, max(0, offset)),
            ).fetchall()
        return self._payloads_to_messages(rows)

    def count_messages(self, session_id: str) -> int:
        legacy = self._legacy_store()
        if legacy is not None:
            try:
                records = legacy.get_by_session(session_id, limit=100000)
            except Exception:
                return 0
            return sum(
                1 for rec in records for m in (rec.get("messages") or [])
                if not (m.get("role") == "user" and m.get("_is_compacted_notes"))
            )
        with self._reading() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM record_messages rm JOIN records r ON r.id = rm.record_id "
                f"WHERE r.session_id = ? AND instr(rm.payload, '{_SNAPSHOT_FLAG}') = 0",
                (session_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _payloads_to_messages(rows) -> List:
        messages = []
        for row in reversed(rows):
            try:
                messages.append(json.loads(row["payload"]))
            except (json.JSONDecodeError, TypeError):
                continue
        return messages

    @staticmethod
    def _legacy_messages_tail(legacy, session_id, limit, offset) -> List:
        """迁移未完成时从 JSONL 归档取尾部（消息级去重）"""
        messages = []
        seen = set()
        try:
            records = legacy.get_by_session(session_id, limit=500)
        except Exception:
            return []
        for rec in reversed(records):
            msgs = rec.get("messages")
            if not isinstance(msgs, list):
                continue
            for msg in reversed(msgs):
                if msg.get("role") == "user" and msg.get("_is_compacted_notes"):
                    continue
                key = json.dumps(msg, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                messages.append(msg)
        messages.reverse()
        end = len(messages) - max(0, offset)
        return messages[max(0, end - limit):end]

    def _insert_messages(self, conn, record_id: str, session_id: str, messages: list) -> None:
        rows = []
        hashes = []
        for ordinal, msg in enumerate(messages):
            msg_hash = self._msg_hash(msg)
            rows.append((
                record_id,
                ordinal,
                msg_hash,
                msg.get("id") if isinstance(msg, dict) else None,
                json.dumps(msg, ensure_ascii=False),
            ))
            hashes.append((session_id, msg_hash))
        if not rows:
            return
        conn.executemany(
            "INSERT OR IGNORE INTO record_messages(record_id, ordinal, msg_hash, message_id, payload) "
            "VALUES(?, ?, ?, ?, ?)",
            rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO message_hashes(session_id, msg_hash) VALUES(?, ?)",
            hashes,
        )

    def _hash_exists(self, session_id: str, msg_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM message_hashes WHERE session_id = ? AND msg_hash = ? LIMIT 1",
            (session_id, msg_hash),
        ).fetchone()
        return row is not None

    def _next_seq(self, conn=None) -> int:
        with self._lock:
            own = conn is None
            target = conn or self._conn
            if own:
                target.execute("BEGIN IMMEDIATE")
            try:
                row = target.execute("SELECT value FROM meta WHERE key = 'seq'").fetchone()
                current = int(row["value"]) if row else 0
                seq = max(current, _SEQ_LIVE_BASE) + 1
                target.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('seq', ?)", (str(seq),))
                if own:
                    target.execute("COMMIT")
            except Exception:
                if own:
                    self._safe_rollback(target)
                raise
            return seq

    def _bump_revision(self, conn, session_id: str = None) -> None:
        row = conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
        revision = (int(row["value"]) if row else 0) + 1
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('revision', ?)", (str(revision),))
        if session_id:
            conn.execute(
                "INSERT INTO session_revision(session_id, revision) VALUES(?, 1) "
                "ON CONFLICT(session_id) DO UPDATE SET revision = revision + 1",
                (session_id,),
            )

    def _append_jsonl(self, record: Dict) -> None:
        if not self.dual_write or not self.base_dir:
            return
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            path = os.path.join(self.base_dir, f"{date_str}.jsonl")
            with open(path, "a", encoding="utf-8-sig") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("[SqliteArchiveStore] 双写 JSONL 失败: %s", e)

    @staticmethod
    def _safe_rollback(conn) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @staticmethod
    def _raw_record(record: Dict) -> str:
        return json.dumps(
            {k: v for k, v in record.items() if k != "messages"},
            ensure_ascii=False,
        )

    @staticmethod
    def _gen_id(messages: Optional[list] = None, timestamp: Optional[str] = None) -> str:
        payload = json.dumps(messages or [], ensure_ascii=False, sort_keys=True)
        ts = (timestamp or datetime.now().isoformat()).encode("utf-8")
        return hashlib.md5(payload.encode("utf-8") + ts).hexdigest()

    @staticmethod
    def _msg_hash(msg) -> str:
        key = ArchiveStore._message_key(msg) if isinstance(msg, dict) else msg
        return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()
