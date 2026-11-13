# -*- coding: utf-8 -*-
from app.agent.llm.anthropic_transport import AnthropicTransport
from app.agent.loop.agent_loop import AgentLoop


def test_anthropic_converts_data_url_to_image_block():
    body = {
        "model": "claude-x",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "描述这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/webp;base64,QUJD"}},
            ],
        }],
    }
    converted = AnthropicTransport.build_request_body(body)
    blocks = converted["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "描述这张图"}
    image = blocks[1]
    assert image["type"] == "image"
    assert image["source"] == {"type": "base64", "media_type": "image/webp", "data": "QUJD"}


def test_vision_error_detection():
    assert AgentLoop._is_vision_error(RuntimeError("400 unsupported image content"))
    assert AgentLoop._is_vision_error(RuntimeError("this model does not support image input"))
    assert not AgentLoop._is_vision_error(RuntimeError("rate limit exceeded"))
