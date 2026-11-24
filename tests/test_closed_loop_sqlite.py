# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.agent.loop.memory import MemoryManager
from app.agent.loop.session_manager import SessionManager
from app.agent.memory.archive_sqlite import SqliteArchiveStore


def _manager(archive):
    manager = SessionManager.__new__(SessionManager)
    manager.three_layer_memory = SimpleNamespace(archive=archive)
    manager._restore_token_budget = lambda: 800_000
    return manager


def _build_session(archive, session="s-loop"):
    for i in range(3):
        archive.append_messages(session, [
            {"role": "user", "content": f"Q{i}"},
            {"role": "assistant", "content": f"A{i}"},
        ])

    archive.append_messages(session, [
        {"role": "user", "content": "[摘要] 之前聊了 Q0-Q2", "_is_compacted_notes": True},
        {"role": "assistant", "content": "已了解前情"},
    ])

    for i in range(2):
        archive.append_messages(session, [
            {"role": "user", "content": f"P{i}"},
            {"role": "assistant", "content": f"R{i}"},
        ])


def test_sqlite_restore_starts_from_last_snapshot(tmp_path):
    archive = SqliteArchiveStore(str(tmp_path / "loop.db"))
    _build_session(archive)

    memory = MemoryManager()
    count = SessionManager._restore_from_archive(_manager(archive), "s-loop", memory)

    contents = [m.get("content") for m in memory.messages]
    assert count >= 6
    assert "Q0" not in contents
    assert "Q1" not in contents
    assert "Q2" not in contents
    assert contents[0] == "[摘要] 之前聊了 Q0-Q2"
    assert "P0" in contents and "R1" in contents


def test_sqlite_restore_is_repeatable(tmp_path):
    archive = SqliteArchiveStore(str(tmp_path / "loop.db"))
    _build_session(archive)

    manager = _manager(archive)
    first = MemoryManager()
    SessionManager._restore_from_archive(manager, "s-loop", first)

    second = MemoryManager()
    SessionManager._restore_from_archive(manager, "s-loop", second)

    assert [m.get("content") for m in first.messages] == [m.get("content") for m in second.messages]
    assert len(first.messages) == len(second.messages)


def test_sqlite_restore_without_snapshot_uses_budget(tmp_path):
    archive = SqliteArchiveStore(str(tmp_path / "plain.db"))
    session = "s-plain"
    for i in range(40):
        archive.append_messages(session, [
            {"role": "user", "content": f"U{i}"},
            {"role": "assistant", "content": f"A{i}"},
        ])

    manager = _manager(archive)
    manager._restore_token_budget = lambda: 300

    tail = archive.get_tail_records(session, limit=100)
    assert all(isinstance(r.get("seq"), int) for r in tail)

    seen = []
    before = None
    for _ in range(10):
        page = archive.get_tail_records(session, limit=10, before_seq=before)
        if not page:
            break
        seen.extend(r["id"] for r in page)
        before = page[0]["seq"]
    assert len(seen) == 40
    assert len(set(seen)) == 40

    memory = MemoryManager()
    count = SessionManager._restore_from_archive(manager, session, memory)
    assert 0 < count < 80
    contents = [m.get("content") for m in memory.messages]
    assert "U39" in contents


def test_sqlite_snapshot_restore_not_capped_by_budget(tmp_path):
    archive = SqliteArchiveStore(str(tmp_path / "snap.db"))
    session = "s-snap"
    archive.append_messages(session, [
        {"role": "user", "content": "[摘要] 很久以前", "_is_compacted_notes": True},
        {"role": "assistant", "content": "收到"},
    ])
    for i in range(20):
        archive.append_messages(session, [
            {"role": "user", "content": f"N{i}"},
            {"role": "assistant", "content": f"M{i}"},
        ])

    manager = _manager(archive)
    manager._restore_token_budget = lambda: 1_000

    memory = MemoryManager()
    SessionManager._restore_from_archive(manager, session, memory)

    contents = [m.get("content") for m in memory.messages]
    assert contents[0] == "[摘要] 很久以前"
    assert "N19" in contents
    assert len(contents) >= 40
