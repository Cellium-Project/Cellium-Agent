# -*- coding: utf-8 -*-
import pytest

from app.agent.memory.archive_sqlite import SqliteArchiveStore


def _make_archive(tmp_path, count=250, session_id="s1"):
    archive = SqliteArchiveStore(str(tmp_path / "archive.db"))
    for i in range(count):
        archive.append_messages(
            session_id=session_id,
            messages=[{"role": "user", "content": f"msg-{i}"}],
        )
    return archive


def test_tail_records_carry_seq(tmp_path):
    archive = _make_archive(tmp_path, 5)
    page = archive.get_tail_records("s1", limit=3)
    assert len(page) == 3
    assert all(isinstance(r.get("seq"), int) for r in page)


def test_tail_records_seq_increases_across_pages(tmp_path):
    archive = _make_archive(tmp_path, 250)
    first = archive.get_tail_records("s1", limit=100)
    second = archive.get_tail_records("s1", limit=100, before_seq=first[0]["seq"])
    assert len(first) == 100
    assert len(second) == 100
    assert second[-1]["seq"] < first[0]["seq"]
    assert not ({r["id"] for r in first} & {r["id"] for r in second})


def test_tail_paging_does_not_repeat(tmp_path):
    archive = _make_archive(tmp_path, 250)
    seen = []
    before = None
    for _ in range(10):
        page = archive.get_tail_records("s1", limit=100, before_seq=before)
        if not page:
            break
        seen.extend(r["id"] for r in page)
        before = page[0]["seq"]
        assert before is not None
    assert len(seen) == 250
    assert len(set(seen)) == 250


def test_load_by_budget_collects_all_records(tmp_path):
    from types import SimpleNamespace

    from app.agent.loop.session_manager import SessionManager

    archive = _make_archive(tmp_path, 250)
    manager = SessionManager.__new__(SessionManager)
    manager.three_layer_memory = SimpleNamespace(archive=archive)

    kept = SessionManager._load_records_by_budget(manager, "s1", 10_000_000)
    assert len(kept) == 250
    assert len({r["id"] for r in kept}) == 250


def test_load_by_budget_stops_at_budget(tmp_path):
    from types import SimpleNamespace

    from app.agent.llm.engine import _estimate_messages_tokens
    from app.agent.loop.session_manager import SessionManager

    archive = _make_archive(tmp_path, 120)
    page = archive.get_tail_records("s1", limit=10)
    per_record = _estimate_messages_tokens(page[-1]["messages"])

    manager = SessionManager.__new__(SessionManager)
    manager.three_layer_memory = SimpleNamespace(archive=archive)

    kept = SessionManager._load_records_by_budget(manager, "s1", per_record * 30)
    assert 0 < len(kept) < 120
    assert len({r["id"] for r in kept}) == len(kept)


def _legacy_archive(tmp_path):
    from app.agent.memory.archive_store import ArchiveStore

    jsonl_dir = tmp_path / "jsonl"
    jsonl_dir.mkdir(exist_ok=True)
    legacy = ArchiveStore(str(jsonl_dir))
    legacy.append("你好", "你好呀", session_id="s1")
    legacy.append("第二问", "第二答", session_id="s1")
    return SqliteArchiveStore(str(tmp_path / "legacy.db"), jsonl_dir=str(jsonl_dir))


def test_recent_messages_fall_back_to_jsonl_before_migration(tmp_path):
    archive = _legacy_archive(tmp_path)
    messages = archive.get_recent_messages("s1", limit=100)
    assert [m.get("content") for m in messages] == ["你好", "你好呀", "第二问", "第二答"]


def test_restore_falls_back_to_legacy_jsonl(tmp_path):
    from types import SimpleNamespace

    from app.agent.loop.memory import MemoryManager
    from app.agent.loop.session_manager import SessionManager

    archive = _legacy_archive(tmp_path)
    manager = SessionManager.__new__(SessionManager)
    manager.three_layer_memory = SimpleNamespace(archive=archive)
    manager._restore_token_budget = lambda: 800_000

    memory = MemoryManager()
    count = SessionManager._restore_from_archive(manager, "s1", memory)

    assert count >= 4
    contents = [m.get("content") for m in memory.messages]
    assert "你好" in contents
    assert "第二答" in contents


def test_restore_prefers_sqlite_when_migration_done(tmp_path):
    from types import SimpleNamespace

    from app.agent.loop.memory import MemoryManager
    from app.agent.loop.session_manager import SessionManager

    archive = _legacy_archive(tmp_path)
    archive.append_messages("s1", [{"role": "user", "content": "已迁移"}])

    manager = SessionManager.__new__(SessionManager)
    manager.three_layer_memory = SimpleNamespace(archive=archive)
    manager._restore_token_budget = lambda: 800_000

    memory = MemoryManager()
    count = SessionManager._restore_from_archive(manager, "s1", memory)

    assert count >= 1
    contents = [m.get("content") for m in memory.messages]
    assert "已迁移" in contents

