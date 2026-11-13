# -*- coding: utf-8 -*-
import json

from app.server.routes.chat import _messages_to_renderable


def test_assistant_attachments_from_tool_results():
    att = {
        "file_id": "f1",
        "filename": "a.png",
        "file_type": "image",
        "file_size": 10,
        "url": "/api/files/f1",
        "local_path": "/tmp/a.png",
    }
    raw = [
        {"role": "user", "content": "发我图片"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call1",
                "function": {"name": "file", "arguments": json.dumps({"command": "send", "paths": ["/tmp/a.png"]})},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call1",
            "content": json.dumps({"success": True, "attachments": [att]}),
        },
        {"role": "assistant", "content": "已发送"},
    ]

    renderable = _messages_to_renderable(raw)
    assistant = [m for m in renderable if m["role"] == "assistant"][-1]
    assert assistant["attachments"] == [att]
