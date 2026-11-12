# -*- coding: utf-8 -*-
"""历史恢复：一轮对话不能被内部消息/Gene 阶段拆成多条，分页也不能切断工具调用"""
from app.server.routes.chat import _messages_to_renderable, _page_bounds


def _assistant(tool_calls=None, content=None, reasoning=None):
    msg = {"role": "assistant"}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    if content is not None:
        msg["content"] = content
    if reasoning is not None:
        msg["content"] = content if content is not None else reasoning
    return msg


def _tc(call_id, name="shell"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _tool(call_id, out="{}"):
    return {"role": "tool", "tool_call_id": call_id, "content": out}


def test_system_message_does_not_split_turn():
    msgs = [
        {"role": "user", "content": "hi"},
        _assistant(tool_calls=[_tc("c1")]),
        _tool("c1"),
        {"role": "system", "content": "[HARD CONSTRAINTS] ..."},
        _assistant(tool_calls=[_tc("c2")]),
        _tool("c2"),
        _assistant(content="done"),
    ]
    out = _messages_to_renderable(msgs)
    assert [m["role"] for m in out] == ["user", "assistant"]
    tools = [s for s in out[1]["timeline"] if s["kind"] == "tool"]
    assert len(tools) == 2
    assert out[1]["content"] == "done"


def test_internal_user_message_hidden_and_not_split():
    msgs = [
        {"role": "user", "content": "hi"},
        _assistant(tool_calls=[_tc("c1")]),
        _tool("c1"),
        {"role": "user", "content": "[系统] 上一轮回复为空，请直接给出最终回答。"},
        _assistant(content="done"),
    ]
    out = _messages_to_renderable(msgs)
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert out[0]["content"] == "hi"
    assert len([s for s in out[1]["timeline"] if s["kind"] == "tool"]) == 1


def test_gene_phase_hidden():
    msgs = [
        {"role": "user", "content": "hi"},
        _assistant(tool_calls=[_tc("c1")]),
        _tool("c1"),
        _assistant(content="done"),
        {"role": "system", "content": "[Gene 创建任务]\n{...}"},
        _assistant(tool_calls=[_tc("g1", "memory")]),
        _tool("g1"),
        _assistant(content="Gene v6 已更新"),
        {"role": "user", "content": "next"},
    ]
    out = _messages_to_renderable(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    tools = [s for s in out[1]["timeline"] if s["kind"] == "tool"]
    assert [t["tool"] for t in tools] == ["shell"]


def test_page_bounds_snap_to_turn_start():
    msgs = [
        {"role": "user", "content": "t1"},
        _assistant(tool_calls=[_tc("c1")]),
        _tool("c1"),
        _assistant(content="a1"),
        {"role": "user", "content": "t2"},
        _assistant(tool_calls=[_tc("c2")]),
        _tool("c2"),
        _assistant(content="a2"),
    ]
    start, end = _page_bounds(msgs, limit=3, offset=0)
    assert end == len(msgs)
    assert start == 4  # 回退到 t2，不切断工具调用
    assert msgs[start]["role"] == "user"

    start, end = _page_bounds(msgs, limit=3, offset=len(msgs) - 4)
    assert start == 0
    assert end == 4
