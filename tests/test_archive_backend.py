# -*- coding: utf-8 -*-
import os

import pytest

from app.agent.loop.session_store import SessionStore
from app.agent.memory.archive_sqlite import SqliteArchiveStore
from app.agent.memory.archive_store import ArchiveStore
from app.agent.memory.three_layer import ThreeLayerMemory
from app.server.routes import chat


@pytest.fixture
def mem(tmp_path):
    instance = ThreeLayerMemory(
        str(tmp_path / "mem"),
        archive_backend="sqlite",
        archive_dual_write=False,
    )
    yield instance
    if isinstance(instance.archive, SqliteArchiveStore):
        instance.archive.close()
    instance.close()


def test_sqlite_backend_roundtrip(mem):
    assert isinstance(mem.archive, SqliteArchiveStore)

    record_id = mem.archive.append_messages("s1", [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ])
    record = mem.archive.get_by_id(record_id)
    assert record["session_id"] == "s1"
    assert [m["content"] for m in record["messages"]] == ["u1", "a1"]


def test_jsonl_backend_selectable(tmp_path):
    instance = ThreeLayerMemory(str(tmp_path / "mem"), archive_backend="jsonl")
    try:
        assert type(instance.archive) is ArchiveStore
        assert not os.path.exists(tmp_path / "mem" / "archive.db")
    finally:
        instance.close()


def test_dual_write_creates_jsonl(tmp_path):
    instance = ThreeLayerMemory(
        str(tmp_path / "mem"),
        archive_backend="sqlite",
        archive_dual_write=True,
    )
    try:
        instance.archive.append_messages("s1", [
            {"role": "user", "content": "dual"},
            {"role": "assistant", "content": "write"},
        ])

        archive_dir = tmp_path / "mem" / "archive"
        files = [f for f in os.listdir(archive_dir) if f.endswith(".jsonl")]
        assert files

        records = ArchiveStore(str(archive_dir)).get_by_session("s1", limit=10)
        assert [m["content"] for m in records[0]["messages"]] == ["dual", "write"]
    finally:
        if isinstance(instance.archive, SqliteArchiveStore):
            instance.archive.close()
        instance.close()


def test_history_cache_invalidated_by_revision(tmp_path):
    chat._HISTORY_CACHE.clear()
    store = SqliteArchiveStore(str(tmp_path / "archive.db"))
    try:
        assert chat._load_archive_messages(store, "s1") == []

        store.append_messages("s1", [{"role": "user", "content": "new"}])
        assert [m["content"] for m in chat._load_archive_messages(store, "s1")] == ["new"]
    finally:
        store.close()
        chat._HISTORY_CACHE.clear()


def test_sqlite_init_failure_falls_back_to_jsonl(tmp_path, monkeypatch):
    import app.agent.memory.archive_sqlite as sqlite_mod

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise OSError("read-only filesystem")

    monkeypatch.setattr(sqlite_mod, "SqliteArchiveStore", _Boom)
    instance = ThreeLayerMemory(str(tmp_path / "mem"), archive_backend="sqlite")
    try:
        assert type(instance.archive) is ArchiveStore
    finally:
        instance.close()


def test_unknown_backend_falls_back_to_sqlite(tmp_path):
    instance = ThreeLayerMemory(str(tmp_path / "mem"), archive_backend="lite")
    try:
        assert isinstance(instance.archive, SqliteArchiveStore)
    finally:
        instance.archive.close()
        instance.close()


def test_deleted_session_not_resurrected_from_db(tmp_path):
    mem_dir = tmp_path / "mem"
    archive_dir = mem_dir / "archive"
    archive_dir.mkdir(parents=True)

    store = SqliteArchiveStore(str(mem_dir / "archive.db"))
    try:
        store.append_messages("gone", [{"role": "user", "content": "x"}])
    finally:
        store.close()

    path = tmp_path / "sessions.json"
    first = SessionStore(store_path=str(path), archive_dir=str(archive_dir))
    assert any(s.session_id == "gone" for s in first.list_sessions())
    assert first.delete_session("gone")

    second = SessionStore(store_path=str(path), archive_dir=str(archive_dir))
    assert not any(s.session_id == "gone" for s in second.list_sessions())


def test_session_store_imports_from_db(tmp_path):
    mem_dir = tmp_path / "mem"
    archive_dir = mem_dir / "archive"
    archive_dir.mkdir(parents=True)

    store = SqliteArchiveStore(str(mem_dir / "archive.db"))
    try:
        store.append_messages("sess_db", [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ])
    finally:
        store.close()

    session_store = SessionStore(
        store_path=str(tmp_path / "sessions.json"),
        archive_dir=str(archive_dir),
    )
    sessions = {s.session_id: s for s in session_store.list_sessions()}
    assert "sess_db" in sessions
    assert sessions["sess_db"].message_count == 1
