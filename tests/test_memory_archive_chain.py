# -*- coding: utf-8 -*-
import asyncio
import json
import os
import time

import pytest

from app.agent.memory.three_layer import ThreeLayerMemory
from app.agent.tools.memory_tool import MemoryTool


@pytest.fixture
def mem(tmp_path):
    instance = ThreeLayerMemory(
        str(tmp_path / "mem"),
        archive_backend="sqlite",
        archive_dual_write=False,
    )
    yield instance
    instance.close()


def test_search_then_raw_archive(mem):
    source_id = mem.persist_session(
        "reset adapter XZQ",
        "run netsh reset XZQ",
        session_id="s-chain",
    )
    assert source_id

    results = mem.retrieve_context("XZQ", include_raw=True, top_k=3)
    assert results
    raw = results[0]["raw"]
    assert raw is not None
    assert raw["id"] == source_id
    contents = [m.get("content") for m in raw["messages"]]
    assert "reset adapter XZQ" in contents
    assert "run netsh reset XZQ" in contents


def test_memory_route_archive_entry(mem, monkeypatch):
    from app.server.routes import memory as memory_route

    source_id = mem.persist_session("question ABC", "answer ABC", session_id="s-route")
    monkeypatch.setattr(memory_route, "_get_memory_service", lambda: mem)

    record = asyncio.run(memory_route.get_archive_entry(source_id))
    assert record["id"] == source_id
    assert [m.get("content") for m in record["messages"]][-1] == "answer ABC"


def test_memory_tool_read_archive(mem):
    source_id = mem.persist_session("question DEF", "answer DEF", session_id="s-tool")
    tool = MemoryTool(mem)

    result = tool._cmd_read_archive(source_id, page=1, page_size=500)
    assert result["success"] is True
    assert "answer DEF" in result["answer"]


def test_snapshot_dedupe_single_record(mem):
    messages = [
        {"role": "user", "content": "same Q"},
        {"role": "assistant", "content": "same A"},
    ]
    first = mem.persist_session("same Q", "same A", session_id="s-dup", messages=messages)
    second = mem.persist_session("same Q", "same A", session_id="s-dup", messages=messages)

    assert first == second
    assert len(mem.archive.get_by_session("s-dup", limit=10)) == 1


def test_rewind_does_not_break_raw_lookup(mem):
    source_id = mem.persist_session(
        "question GHI",
        "answer GHI",
        session_id="s-rw",
        messages=[
            {"id": "u-ghi", "role": "user", "content": "question GHI"},
            {"id": "a-ghi", "role": "assistant", "content": "answer GHI"},
        ],
    )
    assert mem.archive.get_by_id(source_id) is not None
    assert mem.archive.delete_after("s-rw", "u-ghi") == 2
    assert mem.archive.get_by_id(source_id) is None

    results = mem.retrieve_context("GHI", include_raw=True, top_k=3)
    assert all(item["raw"] is None for item in results)


def test_migrated_archive_ids_resolve(tmp_path):
    mem_dir = tmp_path / "legacy_mem"
    archive_dir = mem_dir / "archive"
    archive_dir.mkdir(parents=True)
    record = {
        "id": "legacy-1",
        "time": "2026-05-01T10:00:00",
        "session_id": "s-legacy",
        "snapshot_hash": "h-legacy",
        "messages": [
            {"role": "user", "content": "legacy question JKL"},
            {"role": "assistant", "content": "legacy answer JKL"},
        ],
    }
    with open(archive_dir / "2026-05-01.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    mem = ThreeLayerMemory(str(mem_dir), archive_backend="sqlite", archive_dual_write=False)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not mem.archive.migration_complete():
            time.sleep(0.05)

        mem.repository.store_user_question(
            question="legacy question JKL",
            answer_summary="legacy answer JKL",
            archive_entry_id="legacy-1",
            session_id="s-legacy",
        )
        results = mem.retrieve_context("JKL", include_raw=True, top_k=3)
        assert results
        raw = results[0]["raw"]
        assert raw is not None
        assert raw["id"] == "legacy-1"
        assert raw["messages"][-1]["content"] == "legacy answer JKL"
    finally:
        mem.close()
