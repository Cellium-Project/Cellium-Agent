# -*- coding: utf-8 -*-

import json
import logging
import os
import sqlite3
import threading
import time

from .archive_store import ArchiveStore

logger = logging.getLogger(__name__)

_BATCH_RECORDS = 200
_SEQ_FILE_STEP = 10_000_000
_IMPORT_THREADS = {}
_IMPORT_LOCK = threading.Lock()


def import_jsonl(store, jsonl_dir, progress=None) -> dict:
    stats = {
        "imported_records": 0,
        "imported_messages": 0,
        "skipped_files": 0,
        "failed_files": 0,
    }
    if not jsonl_dir or not os.path.isdir(jsonl_dir):
        return stats

    try:
        names = sorted(f for f in os.listdir(jsonl_dir) if f.endswith(".jsonl"))
    except OSError as e:
        logger.warning("[ArchiveMigrate] 读取目录失败: %s (%s)", jsonl_dir, e)
        return stats

    order = {name: idx for idx, name in enumerate(names)}
    for name in reversed(names):
        path = os.path.join(jsonl_dir, name)
        try:
            stat = os.stat(path)
            if _is_done(store, name, stat):
                stats["skipped_files"] += 1
                continue
            records = _read_records(path)
            base_seq = order[name] * _SEQ_FILE_STEP
            items = [(record, base_seq + idx + 1) for idx, record in enumerate(records)]
            added_records, added_messages = _import_records(store, items)
            stats["imported_records"] += added_records
            stats["imported_messages"] += added_messages
            _mark_done(store, name, stat)
            if added_records:
                store.bump_revision()
            if progress is not None:
                try:
                    progress(name, stats)
                except Exception as e:
                    logger.warning("[ArchiveMigrate] 进度回调失败: %s", e)
        except Exception as e:
            logger.warning("[ArchiveMigrate] 文件导入失败 | file=%s | error=%s", name, e)
            stats["failed_files"] += 1

    return stats


def start_background_import(store, jsonl_dir):
    if not jsonl_dir or not os.path.isdir(jsonl_dir):
        return None
    try:
        has_jsonl = any(f.endswith(".jsonl") for f in os.listdir(jsonl_dir))
    except OSError:
        return None
    if not has_jsonl:
        return None

    key = getattr(store, "db_path", None) or id(store)
    with _IMPORT_LOCK:
        thread = _IMPORT_THREADS.get(key)
        if thread is not None:
            return thread
        thread = threading.Thread(
            target=_run_import,
            args=(store, jsonl_dir),
            daemon=True,
            name="archive-import",
        )
        _IMPORT_THREADS[key] = thread
        thread.start()
        return thread


def _run_import(store, jsonl_dir) -> None:
    try:
        def report(name, stats):
            logger.info(
                "[ArchiveMigrate] 导入进度 | file=%s | records=%d | messages=%d | skipped=%d | failed=%d",
                name,
                stats["imported_records"],
                stats["imported_messages"],
                stats["skipped_files"],
                stats["failed_files"],
            )

        import_jsonl(store, jsonl_dir, progress=report)
    except Exception as e:
        logger.warning("[ArchiveMigrate] 后台导入异常: %s", e)


def _is_done(store, name: str, stat) -> bool:
    with store._lock:
        row = store._conn.execute(
            "SELECT size, mtime_ns, done FROM import_state WHERE file = ?", (name,)
        ).fetchone()
    return bool(
        row
        and row["done"]
        and row["size"] == stat.st_size
        and row["mtime_ns"] == stat.st_mtime_ns
    )


def _mark_done(store, name: str, stat) -> None:
    with store._lock:
        store._conn.execute(
            "INSERT OR REPLACE INTO import_state(file, size, mtime_ns, done) VALUES(?, ?, ?, 1)",
            (name, stat.st_size, stat.st_mtime_ns),
        )


def _read_records(path: str) -> list:
    records = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _import_records(store, items: list) -> tuple:
    imported_records = 0
    imported_messages = 0

    for start in range(0, len(items), _BATCH_RECORDS):
        chunk = items[start:start + _BATCH_RECORDS]
        with store._lock:
            conn = store._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                touched_sessions = set()
                deleted = {
                    row["id"] for row in conn.execute("SELECT id FROM deleted_records").fetchall()
                }
                truncated = {
                    row["record_id"]: row["stop_message_id"]
                    for row in conn.execute(
                        "SELECT record_id, stop_message_id FROM truncated_records"
                    ).fetchall()
                }
                for record, seq in chunk:
                    record_id = record.get("id")
                    if not record_id or record_id in deleted:
                        continue
                    if conn.execute(
                        "SELECT 1 FROM records WHERE id = ?", (record_id,)
                    ).fetchone():
                        continue

                    session_id = record.get("session_id") or "default"
                    messages = record.get("messages") or []
                    stop_id = truncated.get(record_id)
                    if stop_id:
                        cut = next(
                            (i for i, m in enumerate(messages)
                             if isinstance(m, dict) and m.get("id") == stop_id),
                            None,
                        )
                        if cut is not None:
                            messages = messages[:cut]
                    if not messages and stop_id:
                        continue
                    conn.execute(
                        "INSERT INTO records(id, session_id, time, snapshot_hash, seq, raw) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (
                            record_id,
                            session_id,
                            record.get("time"),
                            record.get("snapshot_hash") or ArchiveStore._hash_messages(messages),
                            seq,
                            store._raw_record(record),
                        ),
                    )

                    rows = []
                    hash_rows = []
                    for ordinal, msg in enumerate(messages):
                        msg_hash = store._msg_hash(msg)
                        rows.append((
                            record_id,
                            ordinal,
                            msg_hash,
                            msg.get("id") if isinstance(msg, dict) else None,
                            json.dumps(msg, ensure_ascii=False),
                        ))
                        hash_rows.append((session_id, msg_hash))
                    if rows:
                        conn.executemany(
                            "INSERT OR IGNORE INTO record_messages"
                            "(record_id, ordinal, msg_hash, message_id, payload) VALUES(?, ?, ?, ?, ?)",
                            rows,
                        )
                        conn.executemany(
                            "INSERT OR IGNORE INTO message_hashes(session_id, msg_hash) VALUES(?, ?)",
                            hash_rows,
                        )

                    imported_records += 1
                    imported_messages += len(rows)
                    touched_sessions.add(session_id)

                if touched_sessions:
                    conn.executemany(
                        "INSERT INTO session_revision(session_id, revision) VALUES(?, 1) "
                        "ON CONFLICT(session_id) DO UPDATE SET revision = revision + 1",
                        [(sid,) for sid in touched_sessions],
                    )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    return imported_records, imported_messages


def tombstone_after(store, jsonl_dir, session_id: str, message_id: str) -> int:
    if not jsonl_dir or not os.path.isdir(jsonl_dir):
        return 0
    try:
        names = sorted(f for f in os.listdir(jsonl_dir) if f.endswith(".jsonl"))
    except OSError:
        return 0

    removed = 0
    found = False
    deleted_ids = []
    truncations = []
    for name in names:
        try:
            records = _read_records(os.path.join(jsonl_dir, name))
        except Exception:
            continue
        for record in records:
            if (record.get("session_id") or "default") != session_id:
                continue
            record_id = record.get("id")
            messages = record.get("messages") or []
            if not found:
                index = next(
                    (i for i, m in enumerate(messages)
                     if isinstance(m, dict) and m.get("id") == message_id),
                    None,
                )
                if index is None:
                    continue
                found = True
                removed += len(messages) - index
                if index > 0:
                    truncations.append((record_id, message_id))
                elif record_id:
                    deleted_ids.append(record_id)
                continue
            removed += len(messages)
            if record_id:
                deleted_ids.append(record_id)

    if not found and not deleted_ids:
        return 0

    with store._lock:
        conn = store._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            conn.executemany(
                "INSERT OR IGNORE INTO deleted_records(id, deleted_at) VALUES(?, ?)",
                [(rid, now) for rid in deleted_ids],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO truncated_records(record_id, stop_message_id) VALUES(?, ?)",
                truncations,
            )
            store._bump_revision(conn, session_id)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    return removed
