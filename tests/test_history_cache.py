# -*- coding: utf-8 -*-
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.memory.archive_store import ArchiveStore
from app.server.routes import chat


@pytest.fixture(autouse=True)
def _clear_cache():
    chat._HISTORY_CACHE.clear()
    yield
    chat._HISTORY_CACHE.clear()


class _CountingArchive(ArchiveStore):
    def __init__(self, base_dir):
        super().__init__(base_dir)
        self.calls = 0

    def get_by_session(self, session_id, limit=20):
        self.calls += 1
        return super().get_by_session(session_id, limit=limit)


def test_load_archive_messages_order(tmp_path):
    archive = ArchiveStore(str(tmp_path))
    sid = "s_order"
    for text in ("m1", "m2", "m3"):
        archive.append_messages(session_id=sid, messages=[{"role": "user", "content": text}])

    msgs = chat._load_archive_messages(archive, sid)
    assert [m["content"] for m in msgs] == ["m1", "m2", "m3"]


def test_cache_hit_skips_rescan(tmp_path):
    archive = _CountingArchive(str(tmp_path))
    sid = "s_cache"
    archive.append_messages(session_id=sid, messages=[{"role": "user", "content": "m1"}])

    first = chat._load_archive_messages(archive, sid)
    second = chat._load_archive_messages(archive, sid)
    assert first == second
    assert archive.calls == 1


def test_signature_change_rescans(tmp_path):
    archive = ArchiveStore(str(tmp_path))
    sid = "s_append"
    archive.append_messages(session_id=sid, messages=[{"role": "user", "content": "m1"}])
    assert len(chat._load_archive_messages(archive, sid)) == 1

    archive.append_messages(session_id=sid, messages=[{"role": "user", "content": "m2"}])
    msgs = chat._load_archive_messages(archive, sid)
    assert [m["content"] for m in msgs] == ["m1", "m2"]


def test_history_route_returns_messages(monkeypatch):
    import asyncio

    payload = [
        {"id": "m1", "role": "user", "content": "hi"},
        {"id": "m2", "role": "assistant", "content": "yo"},
    ]
    monkeypatch.setattr(chat, "_load_raw_history", lambda sid, limit=None, offset=0: payload)
    monkeypatch.setattr(chat, "_history_total", lambda sid, fallback=0: len(payload))
    monkeypatch.setattr("app.agent.loop.rewind.tracked_user_ids", lambda sid: set())

    result = asyncio.run(chat.get_session_history("s1", limit=150, offset=0))
    assert result["count"] == 2
    assert result["total"] == 2


def test_sqlite_history_not_capped_by_record_count(tmp_path):
    """检查点让记录数暴涨后，历史仍按消息取窗口，不再被 500 条记录截断"""
    from app.agent.memory.archive_sqlite import SqliteArchiveStore

    store = SqliteArchiveStore(str(tmp_path / "archive.db"))
    sid = "s_many"
    for i in range(600):
        store.append_messages(sid, [
            {"role": "user", "content": f"u{i}"},
            {"role": "assistant", "content": f"a{i}"},
        ])

    recent = chat._load_archive_messages(store, sid, limit=200)
    assert [m["content"] for m in recent[-2:]] == ["u599", "a599"]
    older = chat._load_archive_messages(store, sid, limit=200, offset=200)
    assert older[-1]["content"] == "a499", "offset 应从最新往回数"
    assert store.count_messages(sid) == 1200
    store.close()


def test_duplicate_snapshot_deduped(tmp_path):
    archive = ArchiveStore(str(tmp_path))
    sid = "s_dup"
    archive.append_messages(session_id=sid, messages=[{"role": "assistant", "content": "same"}])
    archive.append_messages(session_id=sid, messages=[
        {"role": "user", "content": "summary", "_is_compacted_notes": True},
        {"role": "assistant", "content": "same"},
    ])

    msgs = chat._load_archive_messages(archive, sid)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "same"


def test_history_paging_next_offset_reaches_oldest(monkeypatch):
    import asyncio

    raw = [
        {"id": f"m{i}", "role": "user", "content": " " if i == 0 else f"msg{i}"}
        for i in range(400)
    ]
    def fake_load(sid, limit=None, offset=0):
        end = len(raw) - offset
        return raw[max(0, end - (limit or len(raw))):end]

    monkeypatch.setattr(chat, "_load_raw_history", fake_load)
    monkeypatch.setattr(chat, "_history_total", lambda sid, fallback=0: len(raw))
    monkeypatch.setattr("app.agent.loop.rewind.tracked_user_ids", lambda sid: set())

    collected = []
    offset = 0
    for _ in range(20):
        page = asyncio.run(chat.get_session_history("s_page", limit=150, offset=offset))
        collected = list(page["messages"]) + collected
        if not page["has_more"]:
            break
        offset = page["next_offset"]

    assert len(collected) == 400
    assert collected[0]["content"] == " "
    assert [m["content"] for m in collected] == [m["content"] for m in raw]
