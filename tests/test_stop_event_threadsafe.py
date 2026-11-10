# -*- coding: utf-8 -*-
import asyncio
import threading

from app.agent.loop.agent_loop import AgentLoop
from app.agent.loop.loop_controller import LoopController


def _make_agent():
    agent = AgentLoop.__new__(AgentLoop)
    agent._loop_controller = LoopController()
    agent._stop_event = None
    agent._stop_event_loop = None
    return agent


def test_stop_wakes_waiter_from_other_thread():
    agent = _make_agent()

    async def _run():
        agent._stop_event = asyncio.Event()
        agent._stop_event_loop = asyncio.get_running_loop()
        waiter = asyncio.create_task(agent._stop_event.wait())
        threading.Thread(target=agent.stop, daemon=True).start()
        done, pending = await asyncio.wait({waiter}, timeout=2)
        for task in pending:
            task.cancel()
        return waiter in done

    assert asyncio.run(_run(), debug=True) is True
    assert agent._loop_controller.is_stop_requested is True


def test_stop_without_loop_sets_event():
    agent = _make_agent()
    agent._stop_event = asyncio.Event()
    agent.stop()
    assert agent._stop_event.is_set() is True


def test_stop_is_idempotent():
    agent = _make_agent()
    agent._stop_event = asyncio.Event()
    agent._stop_event.set()
    agent.stop()
    agent.stop()
    assert agent._stop_event.is_set() is True
