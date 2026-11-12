# -*- coding: utf-8 -*-
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.util import logger as lg


class _State:
    def __init__(self, session_id, iteration=0):
        self.session_id = session_id
        self.iteration = iteration
        self.max_iterations = 10
        self.tokens_used = iteration * 100
        self.last_prompt_tokens = 10
        self.total_completion_tokens = 20
        self.elapsed_ms = iteration * 1000
        self.tool_traces = [{"tool": "ls", "success": True}]
        self.last_error = None
        self.recent_llm_outputs = []
        self.decision_trace = []
        self.features = None
        self.hybrid_phase = None

    def get_last_decision(self):
        return None


@pytest.fixture(autouse=True)
def _reset():
    lg._runtime_by_session.clear()
    lg._history_by_session.clear()
    lg._last_status_session = None
    lg._status_seq = 0
    yield
    lg._runtime_by_session.clear()
    lg._history_by_session.clear()
    lg._last_status_session = None
    lg._status_seq = 0


def test_two_sessions_isolated():
    lg.set_runtime_status(_State("A", 1))
    lg.set_runtime_status(_State("B", 5))

    assert lg.get_runtime_status("A").iteration == 1
    assert lg.get_runtime_status("B").iteration == 5
    assert lg.get_runtime_status().iteration == 5


def test_subagent_session_does_not_touch_parent():
    lg.set_runtime_status(_State("parent", 3))
    lg.set_runtime_status(_State("parent", 4))
    lg.set_runtime_status(_State("subagent:researcher:abc", 7))

    assert lg.get_runtime_status("parent").iteration == 4
    history = lg.get_status_history("parent")
    assert [item["iteration"] for item in history] == [3]
    assert lg.get_runtime_status("subagent:researcher:abc").iteration == 7


def test_history_per_session_and_dedup():
    lg.set_runtime_status(_State("A", 1))
    lg.set_runtime_status(_State("A", 1))
    assert len(lg.get_status_history("A")) == 1

    lg.set_runtime_status(_State("A", 2))
    lg.set_runtime_status(_State("A", 3))
    lg.set_runtime_status(_State("B", 1))
    lg.set_runtime_status(_State("B", 2))
    assert [item["iteration"] for item in lg.get_status_history("A")] == [1, 2]
    assert [item["iteration"] for item in lg.get_status_history("B")] == [1]

    lg.clear_status_history("A")
    assert lg.get_status_history("A") == []
    assert len(lg.get_status_history("B")) == 1


def test_clear_runtime_status_per_session():
    lg.set_runtime_status(_State("A", 1))
    lg.set_runtime_status(_State("B", 2))

    lg.clear_runtime_status("A")
    assert lg.get_runtime_status("A") is None
    assert lg.get_runtime_status("B").iteration == 2
    assert lg.get_runtime_status().iteration == 2


def test_session_lru_cap():
    for i in range(lg.MAX_STATUS_SESSIONS + 5):
        lg.set_runtime_status(_State(f"s{i}", 1))
    assert len(lg._runtime_by_session) <= lg.MAX_STATUS_SESSIONS
    assert lg.get_runtime_status(f"s{lg.MAX_STATUS_SESSIONS + 4}") is not None
    assert lg.get_runtime_status("s0") is None


def test_subagent_loop_skips_runtime_status():
    from app.agent.loop.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop._status_enabled = False
    loop._loop_state = _State("subagent:x:1", 3)
    loop._publish_runtime_state()
    assert lg.get_runtime_status("subagent:x:1") is None

    loop._status_enabled = True
    loop._publish_runtime_state()
    assert lg.get_runtime_status("subagent:x:1").iteration == 3


def test_concurrent_sessions_do_not_mix():
    async def worker(sid, base):
        for i in range(1, 6):
            lg.set_runtime_status(_State(sid, base + i))
            await asyncio.sleep(0)

    async def main():
        await asyncio.gather(
            worker("A", 0),
            worker("B", 100),
            worker("subagent:x:1", 200),
        )

    asyncio.run(main())
    assert lg.get_runtime_status("A").iteration == 5
    assert lg.get_runtime_status("B").iteration == 105
    assert lg.get_runtime_status("subagent:x:1").iteration == 205
    for sid in ("A", "B", "subagent:x:1"):
        assert lg.get_status_history(sid), f"{sid} 历史快照丢失"


def test_logs_route_accepts_session_id():
    from app.server.routes import logs

    lg.set_runtime_status(_State("A", 1))
    lg.set_runtime_status(_State("A", 2))
    lg.set_runtime_status(_State("B", 9))

    a = asyncio.run(logs.runtime_status(session_id="A"))
    b = asyncio.run(logs.runtime_status(session_id="B"))

    assert [s["session_id"] for s in a["sessions"]] == ["A"]
    assert a["sessions"][0]["detail"]["iteration"] == 2
    assert [s["session_id"] for s in b["sessions"]] == ["B"]
    assert asyncio.run(logs.runtime_status_history(session_id="A"))["history"][0]["iteration"] == 1


def test_logs_route_lists_all_sessions(monkeypatch):
    from app.server.routes import logs

    lg.set_runtime_status(_State("A", 1))
    lg.set_runtime_status(_State("B", 2))
    monkeypatch.setattr(logs, "_session_titles", lambda: {"A": "会话A", "B": "会话B"})

    data = asyncio.run(logs.runtime_status(session_id=""))

    assert data["available"] is True
    assert [s["session_id"] for s in data["sessions"]] == ["B", "A"]
    assert data["sessions"][0]["title"] == "会话B"
    assert data["sessions"][1]["title"] == "会话A"
    assert data["sessions"][0]["detail"]["iteration"] == 2
    assert data["detail"]["iteration"] == 2


def test_logs_route_empty():
    from app.server.routes import logs

    data = asyncio.run(logs.runtime_status(session_id=""))
    assert data["available"] is False
    assert data["sessions"] == []
