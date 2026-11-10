# -*- coding: utf-8 -*-
"""thinking 分割不能把代码块里引用的 JSON 当成前置思考"""
import json


def _segments_server(content):
    from app.server.routes.chat import _split_content_with_thinking
    return _split_content_with_thinking(content)


def _segments_tui(content):
    from app.tui.history_render import split_content_with_thinking
    return split_content_with_thinking(content)


def _json_text(needle="引用"):
    return f"以下是历史回复：\n\n```\n{{\"reasoning\": \"{needle}\", \"action\": \"direct_response\"}}\n后续正文\n```\n\n结尾说明"


def test_fenced_quote_not_treated_as_thinking_server():
    segs = _segments_server(_json_text())
    assert [s["type"] for s in segs] == ["text"]
    assert "```" in segs[0]["content"]


def test_fenced_quote_not_treated_as_thinking_tui():
    segs = _segments_tui(_json_text())
    assert [s["type"] for s in segs] == ["text"]
    assert "```" in segs[0]["content"]


def test_inline_code_json_not_treated_as_thinking():
    content = "示例：`{\"reasoning\": \"x\", \"action\": \"y\"}` 就这些"
    for split in (_segments_server, _segments_tui):
        segs = split(content)
        assert [s["type"] for s in segs] == ["text"]


def test_inline_json_still_split():
    content = "前言\n{\"reasoning\": \"思考\", \"action\": \"direct_response\"}\n后文"
    for split in (_segments_server, _segments_tui):
        types = [s["type"] for s in split(content)]
        assert types == ["text", "thinking", "text"]


def test_json_fenced_block_still_thinking():
    payload = json.dumps({"reasoning": "思考", "action": "direct_response"}, ensure_ascii=False)
    content = f"```json\n{payload}\n```\n\n正文"
    for split in (_segments_server, _segments_tui):
        segs = split(content)
        assert segs[0]["type"] == "thinking"
        assert any(s["type"] == "text" and "正文" in s["content"] for s in segs)


def test_unclosed_fence_stays_single_text():
    content = "开头\n\n```\n被截断的引用"
    for split in (_segments_server, _segments_tui):
        segs = split(content)
        assert [s["type"] for s in segs] == ["text"]
