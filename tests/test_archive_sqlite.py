# -*- coding: utf-8 -*-

import json
import os
from datetime import datetime

import pytest

from app.agent.memory.archive_store import ArchiveStore
from app.agent.memory.archive_sqlite import SqliteArchiveStore
from app.agent.memory.archive_migrate import import_jsonl, start_background_import


@pytest.fixture
def store(tmp_path):
    instance = SqliteArchiveStore(str(tmp_path / "archive.db"), jsonl_dir=str(tmp_path / "jsonl"))
    yield instance
    instance.close()


def _flatten(records):
    return [msg for record in records for msg in record["messages"]]


def _contents(records):
    return [[msg["content"] for msg in record["messages"]] for record in records]


def _db_counts(store):
    with store._lock:
        records = store._conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        messages = store._conn.execute("SELECT COUNT(*) FROM record_messages").fetchone()[0]
    return records, messages


def _write_jsonl(path, records):
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_append_messages_order_and_date(store):
    store.append_messages("s1", [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ])
    store.append_messages("s1", [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ])

    records = store.get_by_session("s1")
    assert _contents(records) == [["u1", "a1"], ["u2", "a2"]]

    by_date = store.get_by_date(datetime.now().strftime("%Y-%m-%d"))
    assert _contents(by_date) == [["u1", "a1"], ["u2", "a2"]]


def test_matches_jsonl_store(tmp_path, store):
    jsonl_store = ArchiveStore(str(tmp_path / "jsonl_ref"))
    batches = [
        [{"role": "user", "content": "u1", "timestamp": "t1"},
         {"role": "assistant", "content": "a1", "timestamp": "t1"}],
        [{"role": "user", "content": "u2", "timestamp": "t2"},
         {"role": "assistant", "content": "", "timestamp": "t2",
          "tool_calls": [{"id": "c1", "type": "function",
                          "function": {"name": "read", "arguments": "{}"}}]},
         {"role": "tool", "content": "ok", "tool_call_id": "c1", "timestamp": "t2"}],
        [{"role": "user", "content": "你好", "timestamp": "t3"},
         {"role": "assistant", "content": "你好", "timestamp": "t3"}],
    ]
    for batch in batches:
        jsonl_store.append_messages("s1", [dict(m) for m in batch])
        store.append_messages("s1", [dict(m) for m in batch])

    expected = _flatten(jsonl_store.get_by_session("s1", limit=100))
    actual = _flatten(store.get_by_session("s1", limit=100))
    assert actual == expected


def test_snapshot_records_match_jsonl_raw(tmp_path, store):
    jsonl_store = ArchiveStore(str(tmp_path / "jsonl_ref"))
    snapshot = {"role": "user", "content": "[压缩] 摘要", "_is_compacted_notes": True}
    batches = [
        [{"role": "user", "content": "u1", "timestamp": "t1"},
         {"role": "assistant", "content": "a1", "timestamp": "t1"}],
        [dict(snapshot, timestamp="t2"),
         {"role": "assistant", "content": "a3", "timestamp": "t2"}],
        [dict(snapshot, timestamp="t2"),
         {"role": "assistant", "content": "a3", "timestamp": "t2"}],
    ]
    for batch in batches:
        jsonl_store.append_messages("s1", [dict(m) for m in batch])
        store.append_messages("s1", [dict(m) for m in batch])

    assert _contents(store.get_by_session("s1", limit=100)) == \
        _contents(jsonl_store.get_by_session("s1", limit=100))
    assert any(msg.get("_is_compacted_notes")
               for rec in store.get_by_session("s1", limit=100)
               for msg in rec["messages"])


def test_latest_and_get_by_id_structure(store):
    record_id = store.append_messages("s1", [
        {"id": "m1", "role": "user", "content": "u1"},
        {"id": "m2", "role": "assistant", "content": "a1"},
    ])

    latest = store.get_latest_by_session("s1")
    assert latest["id"] == record_id
    assert set(latest.keys()) == {"id", "time", "session_id", "snapshot_hash", "messages"}
    assert latest["session_id"] == "s1"
    assert [m["content"] for m in latest["messages"]] == ["u1", "a1"]

    assert store.get_by_id(record_id) == latest
    assert store.get_by_id("missing") is None
    assert store.get_latest_by_session("missing") is None


def test_delete_after_and_rewrite(store):
    messages = [
        {"id": "m1", "role": "user", "content": "u1"},
        {"id": "a1", "role": "assistant", "content": "a1"},
    ]
    store.append_messages("s1", messages)
    store.append_messages("s1", [
        {"id": "m2", "role": "user", "content": "u2"},
        {"id": "a2", "role": "assistant", "content": "a2"},
    ])

    assert store.delete_after("s1", "missing") == 0
    assert store.delete_after("s1", "a1") == 3
    assert _contents(store.get_by_session("s1", limit=100)) == [["u1"]]

    store.append_messages("s1", [
        {"id": "m2", "role": "user", "content": "u2"},
        {"id": "a2", "role": "assistant", "content": "a2"},
    ])
    assert _contents(store.get_by_session("s1", limit=100)) == [["u1"], ["u2", "a2"]]


def test_session_revision_scoped_per_session(store):
    assert store.revision("s1") == 0
    assert store.revision("s2") == 0

    store.append_messages("s1", [{"role": "user", "content": "u1"}])
    assert store.revision("s1") == 1
    assert store.revision("s2") == 0
    assert store.revision() == 1

    store.append_messages("s2", [{"role": "user", "content": "u2"}])
    assert store.revision("s2") == 1
    assert store.revision("s1") == 1
    assert store.revision() == 2


def test_indexes_present(store):
    names = {
        row[0] for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    assert "idx_record_messages_message" in names
    assert "idx_records_time" in names


def test_record_time_is_offset_aware(store):
    store.append_messages("s1", [{"role": "user", "content": "u1"}])
    time_str = store.get_by_session("s1")[-1]["time"]
    assert "+" in time_str or time_str.endswith("Z")


def test_legacy_fallback_for_single_record_reads(tmp_path):
    jsonl_dir = tmp_path / "legacy"
    jsonl_dir.mkdir()
    _write_jsonl(jsonl_dir / "2026-01-01.jsonl", [
        {"id": "r1", "time": "2026-01-01T10:00:00", "session_id": "s1",
         "messages": [{"id": "u1", "role": "user", "content": "hi"},
                      {"role": "assistant", "content": "yo"}],
         "snapshot_hash": "h1"},
    ])
    store = SqliteArchiveStore(str(tmp_path / "archive.db"), jsonl_dir=str(jsonl_dir))
    try:
        assert store.migration_complete() is False
        assert store.get_record_meta("r1")["session_id"] == "s1"
        assert store.get_last_assistant("r1") == "yo"
        assert store.get_by_id("r1")["messages"][1]["content"] == "yo"

        store.append_messages("s1", [
            {"id": "u2", "role": "user", "content": "new"},
            {"id": "a2", "role": "assistant", "content": "new-a"},
        ])
        records = store.get_by_session("s1", limit=10)
        assert [r["id"] for r in records if r["id"] == "r1"] == ["r1"]
        assert len(records) == 2
    finally:
        store.close()


def test_close_waits_for_active_readers(tmp_path):
    import sqlite3
    import threading

    store = SqliteArchiveStore(str(tmp_path / "archive.db"))
    for i in range(30):
        store.append_messages("s1", [{"role": "user", "content": f"m{i}"}])

    stop = threading.Event()
    errors = []

    def reader():
        while not stop.is_set():
            try:
                store.get_by_session("s1", limit=20)
                store.revision("s1")
            except sqlite3.ProgrammingError:
                return
            except Exception as e:
                errors.append(repr(e))
                return

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        import time as _time
        _time.sleep(0.05)
        store.close()
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)

    assert errors == []
    assert store._read_conns == set()
    import pytest
    with pytest.raises(sqlite3.ProgrammingError):
        store.get_by_session("s1")
    with pytest.raises(sqlite3.ProgrammingError):
        store.append_messages("s1", [{"role": "user", "content": "x"}])
    with pytest.raises(sqlite3.ProgrammingError):
        store.delete_after("s1", "m0")
    with pytest.raises(sqlite3.ProgrammingError):
        store.bump_revision("s1")


def test_read_connections_bounded_by_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = SqliteArchiveStore(str(tmp_path / "archive.db"))
    store.append_messages("s1", [{"role": "user", "content": "seed"}])
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for _ in range(100):
                pool.submit(store.get_by_session, "s1", 10).result(timeout=10)
        assert len(store._read_conns) <= 8
    finally:
        store.close()
    assert store._read_conns == set()


def test_revision_increments_on_writes(store):
    assert store.revision() == 0
    store.append("u1", "a1", "s1")
    assert store.revision() == 1
    store.append_messages("s1", [{"role": "user", "content": "u2"}])
    assert store.revision() == 2
    assert store.delete_after("s1", "missing") == 0
    assert store.revision() == 2

    store.append_messages("s1", [{"id": "m3", "role": "user", "content": "u3"}])
    assert store.revision() == 3
    assert store.delete_after("s1", "m3") == 1
    assert store.revision() == 4


def test_dual_write_jsonl_readable(tmp_path):
    jsonl_dir = tmp_path / "dual"
    store = SqliteArchiveStore(
        str(tmp_path / "archive.db"), jsonl_dir=str(jsonl_dir), dual_write=True
    )
    try:
        record_id = store.append_messages("s1", [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ])
        assert jsonl_dir.is_dir()
        records = ArchiveStore(str(jsonl_dir)).get_by_session("s1", limit=10)
        assert len(records) == 1
        assert records[0]["id"] == record_id
        assert [m["content"] for m in records[0]["messages"]] == ["u1", "a1"]
    finally:
        store.close()


def test_import_jsonl_idempotent(tmp_path, store):
    source = tmp_path / "legacy"
    source.mkdir()
    path = source / "2026-01-01.jsonl"
    snapshot = {"role": "user", "content": "[压缩] 摘要", "_is_compacted_notes": True}
    _write_jsonl(path, [
        {"id": "r1", "time": "2026-01-01T10:00:00", "session_id": "s1",
         "messages": [snapshot, {"role": "assistant", "content": "a1"}], "snapshot_hash": "h1"},
        {"id": "r2", "time": "2026-01-01T10:01:00", "session_id": "s1",
         "messages": [snapshot, {"role": "assistant", "content": "a2"}], "snapshot_hash": "h2"},
    ])

    stats = import_jsonl(store, str(source))
    assert stats == {
        "imported_records": 2,
        "imported_messages": 4,
        "skipped_files": 0,
        "failed_files": 0,
    }
    assert _db_counts(store) == (2, 4)
    assert store.revision() > 0
    assert len(store.get_by_id("r1")["messages"]) == 2

    again = import_jsonl(store, str(source))
    assert again["imported_records"] == 0
    assert again["skipped_files"] == 1
    assert _db_counts(store) == (2, 4)

    _write_jsonl(path, [
        {"id": "r3", "time": "2026-01-01T10:02:00", "session_id": "s1",
         "messages": [{"role": "user", "content": "u3"},
                      {"role": "assistant", "content": "a3"}], "snapshot_hash": "h3"},
    ])
    third = import_jsonl(store, str(source))
    assert third["imported_records"] == 1
    assert third["imported_messages"] == 2
    assert _db_counts(store) == (3, 6)


def test_background_import_runs_once(tmp_path, store):
    assert start_background_import(store, str(tmp_path / "missing")) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert start_background_import(store, str(empty)) is None

    source = tmp_path / "legacy"
    source.mkdir()
    _write_jsonl(source / "2026-01-02.jsonl", [
        {"id": "r1", "time": "2026-01-02T10:00:00", "session_id": "s1",
         "messages": [{"role": "user", "content": "u1"},
                      {"role": "assistant", "content": "a1"}], "snapshot_hash": "h1"},
    ])

    thread = start_background_import(store, str(source))
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert _db_counts(store) == (1, 2)
    assert start_background_import(store, str(source)) is thread


def test_import_newest_first_keeps_chronological_order(tmp_path, store):
    source = tmp_path / "legacy"
    source.mkdir()
    for day, tag in (("2026-01-01", "old"), ("2026-01-02", "new")):
        _write_jsonl(source / f"{day}.jsonl", [
            {"id": f"r-{tag}", "time": f"{day}T10:00:00", "session_id": "s1",
             "messages": [{"role": "user", "content": tag}], "snapshot_hash": "h"},
        ])

    import_jsonl(store, str(source))
    assert [r["messages"][0]["content"] for r in store.get_by_session("s1", limit=10)] == ["old", "new"]

    store.append_messages("s1", [{"role": "user", "content": "live"}])
    assert [r["messages"][0]["content"] for r in store.get_by_session("s1", limit=10)] == ["old", "new", "live"]


def test_same_batch_duplicates_match_jsonl(tmp_path, store):
    jsonl = ArchiveStore(str(tmp_path / "jsonl_ref"))
    batch = [
        {"role": "user", "content": "dup"},
        {"role": "user", "content": "dup"},
        {"role": "assistant", "content": "ok"},
    ]
    jsonl.append_messages("s1", [dict(m) for m in batch])
    store.append_messages("s1", [dict(m) for m in batch])
    assert _flatten(store.get_by_session("s1", limit=10)) == \
        _flatten(jsonl.get_by_session("s1", limit=10))


def test_read_through_jsonl_before_import(tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    _write_jsonl(source / "2026-02-01.jsonl", [
        {"id": "r1", "time": "2026-02-01T10:00:00", "session_id": "s1",
         "messages": [{"role": "user", "content": "u1"}], "snapshot_hash": "h"},
    ])
    store = SqliteArchiveStore(str(tmp_path / "archive.db"), jsonl_dir=str(source))
    try:
        assert store.migration_complete() is False
        records = store.get_by_session("s1", limit=10)
        assert [m["content"] for r in records for m in r["messages"]] == ["u1"]

        import_jsonl(store, str(source))
        assert store.migration_complete() is True
        records = store.get_by_session("s1", limit=10)
        assert [m["content"] for r in records for m in r["messages"]] == ["u1"]
    finally:
        store.close()


def test_rewind_before_import_does_not_resurrect(tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    _write_jsonl(source / "2026-03-01.jsonl", [
        {"id": "r1", "time": "2026-03-01T10:00:00", "session_id": "s1",
         "messages": [{"id": "u1", "role": "user", "content": "u1"},
                      {"id": "a1", "role": "assistant", "content": "a1"}],
         "snapshot_hash": "h1"},
        {"id": "r2", "time": "2026-03-01T10:01:00", "session_id": "s1",
         "messages": [{"id": "u2", "role": "user", "content": "u2"},
                      {"id": "a2", "role": "assistant", "content": "a2"}],
         "snapshot_hash": "h2"},
    ])
    store = SqliteArchiveStore(str(tmp_path / "archive.db"), jsonl_dir=str(source))
    try:
        removed = store.delete_after("s1", "a1")
        assert removed == 3

        import_jsonl(store, str(source))
        contents = [m["content"] for r in store.get_by_session("s1", limit=100)
                    for m in r["messages"]]
        assert contents == ["u1"]
    finally:
        store.close()


def test_rewind_survives_reimport(tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    path = source / "2026-03-02.jsonl"
    _write_jsonl(path, [
        {"id": "r1", "time": "2026-03-02T10:00:00", "session_id": "s1",
         "messages": [{"id": "u1", "role": "user", "content": "u1"},
                      {"id": "a1", "role": "assistant", "content": "a1"}],
         "snapshot_hash": "h1"},
        {"id": "r2", "time": "2026-03-02T10:01:00", "session_id": "s1",
         "messages": [{"id": "u2", "role": "user", "content": "u2"},
                      {"id": "a2", "role": "assistant", "content": "a2"}],
         "snapshot_hash": "h2"},
    ])
    store = SqliteArchiveStore(str(tmp_path / "archive.db"), jsonl_dir=str(source))
    try:
        import_jsonl(store, str(source))
        assert store.delete_after("s1", "a1") == 3

        _write_jsonl(path, [
            {"id": "r3", "time": "2026-03-02T10:02:00", "session_id": "s1",
             "messages": [{"id": "u3", "role": "user", "content": "u3"},
                          {"id": "a3", "role": "assistant", "content": "a3"}],
             "snapshot_hash": "h3"},
        ])
        import_jsonl(store, str(source))

        contents = [m["content"] for r in store.get_by_session("s1", limit=100)
                    for m in r["messages"]]
        assert contents == ["u1", "u3", "a3"]
    finally:
        store.close()


def test_import_preserves_legacy_fields(tmp_path, store):
    source = tmp_path / "legacy"
    source.mkdir()
    _write_jsonl(source / "2026-04-01.jsonl", [
        {"id": "r1", "time": "2026-04-01T10:00:00", "session_id": "s1",
         "user": "hello", "assistant": "world"},
    ])
    import_jsonl(store, str(source))
    record = store.get_by_id("r1")
    assert record["user"] == "hello"
    assert record["assistant"] == "world"
    assert record["messages"] == []


def test_dual_write_rewind_removes_jsonl(tmp_path):
    jsonl_dir = tmp_path / "dual"
    store = SqliteArchiveStore(
        str(tmp_path / "archive.db"), jsonl_dir=str(jsonl_dir), dual_write=True
    )
    try:
        store.append_messages("s1", [
            {"id": "u1", "role": "user", "content": "u1"},
            {"id": "a1", "role": "assistant", "content": "a1"},
        ])
        store.append_messages("s1", [
            {"id": "u2", "role": "user", "content": "u2"},
            {"id": "a2", "role": "assistant", "content": "a2"},
        ])
        assert store.delete_after("s1", "a1") == 3

        legacy = ArchiveStore(str(jsonl_dir)).get_by_session("s1", limit=10)
        contents = [m["content"] for r in legacy for m in r["messages"]]
        assert contents == ["u1"]
    finally:
        store.close()


def test_concurrent_read_write_no_errors(store):
    import threading

    store.append_messages("s1", [{"id": "seed", "role": "user", "content": "seed"}])
    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                store.get_by_session("s1", limit=50)
                store._load_messages("seed")
            except Exception as e:
                errors.append(repr(e))
                return

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for t in readers:
        t.start()
    for i in range(80):
        store.append_messages("s1", [{"id": f"w{i}", "role": "user", "content": f"m{i}"}])
    stop.set()
    for t in readers:
        t.join(timeout=5)

    assert errors == []
    assert len(store.get_by_session("s1", limit=200)) >= 1


def test_list_session_ids(store):
    store.append_messages("s1", [{"role": "user", "content": "u1"}])
    store.append_messages("s1", [{"role": "user", "content": "u2"}])
    store.append_messages("s2", [{"role": "user", "content": "u3"}])
    assert sorted(store.list_session_ids()) == ["s1", "s2"]


def test_get_last_assistant_and_meta(store):
    rid = store.append_messages("s1", [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "{}"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u2"},
    ])
    assert store.get_last_assistant(rid) == "a2"

    meta = store.get_record_meta(rid)
    assert meta["id"] == rid
    assert meta["session_id"] == "s1"
    assert "messages" not in meta

    assert store.get_last_assistant("missing") is None
    assert store.get_record_meta("missing") is None
