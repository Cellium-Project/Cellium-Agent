# -*- coding: utf-8 -*-
import base64
from types import SimpleNamespace

from app.agent.loop.agent_loop import AgentLoop
from app.agent.llm.engine import _estimate_messages_tokens
from app.agent.llm import vision_capability

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _png(path):
    path.write_bytes(_PNG)
    return str(path)


def _loop(tmp_path, mode="on", keep=3):
    path = _png(tmp_path / "pic.png")
    loop = AgentLoop.__new__(AgentLoop)
    loop.llm = SimpleNamespace(
        model="test-model",
        vision_mode=mode,
        model_info=SimpleNamespace(supports_vision=mode != "off"),
    )
    loop._MAX_KEEP_IMAGES = keep
    loop._turn_images = [path]
    loop._pending_images = []
    loop._retained_images = []
    loop._image_anchors = []
    return loop, path


def test_vision_on_injects_image_part(tmp_path):
    loop, _ = _loop(tmp_path, "on")
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "看图"}]
    out = AgentLoop._apply_vision_parts(loop, messages)
    content = out[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[-1] == {"type": "text", "text": "看图"}


def test_vision_off_keeps_text(tmp_path):
    loop, _ = _loop(tmp_path, "off")
    messages = [{"role": "user", "content": "看图"}]
    out = AgentLoop._apply_vision_parts(loop, messages)
    assert out[-1]["content"] == "看图"


def test_vision_auto_skips_cached_unsupported(tmp_path):
    loop, _ = _loop(tmp_path, "auto")
    vision_capability.mark_unsupported("test-model")
    try:
        messages = [{"role": "user", "content": "看图"}]
        out = AgentLoop._apply_vision_parts(loop, messages)
        assert out[-1]["content"] == "看图"
    finally:
        vision_capability.clear()


def test_pending_images_only_next_call(tmp_path):
    loop, path = _loop(tmp_path, "on")
    loop._turn_images = []
    loop._pending_images = [path]
    messages = [{"role": "user", "content": "hi"}]
    out = AgentLoop._apply_vision_parts(loop, messages)
    assert out[-1]["role"] == "user"
    assert any(p.get("type") == "image_url" for p in out[-1]["content"] if isinstance(p, dict))
    assert loop._pending_images == []


def test_strip_vision_parts_returns_text(tmp_path):
    loop, _ = _loop(tmp_path, "on")
    messages = [{"role": "user", "content": "看图"}]
    with_images = AgentLoop._apply_vision_parts(loop, messages)
    stripped = AgentLoop._strip_vision_parts(with_images)
    assert stripped[-1]["content"] == "看图"


def test_build_turn_images_dedupes(tmp_path):
    loop = AgentLoop.__new__(AgentLoop)
    pic = tmp_path / "p.png"
    pic.write_bytes(b"x")
    atts = [
        {"file_type": "image", "local_path": str(pic)},
        {"file_type": "image", "local_path": str(pic)},
    ]
    assert loop._build_turn_images(atts) == [str(pic)]


def test_build_turn_images_prefers_vision_copy(tmp_path):
    loop = AgentLoop.__new__(AgentLoop)
    original = tmp_path / "orig.png"
    vision = tmp_path / "vision.webp"
    original.write_bytes(b"x")
    vision.write_bytes(b"y")

    atts = [{
        "file_type": "image",
        "local_path": str(original),
        "vision_local_path": str(vision),
    }]
    assert loop._build_turn_images(atts) == [str(vision)]


def test_read_tool_returns_image_refs(tmp_path):
    from app.agent.tools.read_tool import ReadTool

    path = _png(tmp_path / "pic.png")
    result = ReadTool().execute({"file_path": path})
    assert result["success"] is True
    assert result["_image_refs"] == [path]
    assert (result["image"]["width"], result["image"]["height"]) == (1, 1)


def test_estimate_tokens_handles_content_list():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/webp;base64,AAA"}},
        ],
    }]
    assert _estimate_messages_tokens(messages) > 1000


def test_anchored_images_reused_next_turn(tmp_path):
    loop, path = _loop(tmp_path, "on")
    loop._turn_images = []
    loop._image_anchors = [("m1", path)]
    AgentLoop._apply_vision_parts(loop, [{"role": "user", "id": "m1", "content": "图一"}])
    assert [p for _, p in loop._image_anchors] == [path]

    out = AgentLoop._apply_vision_parts(loop, [
        {"role": "user", "id": "m1", "content": "图一"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "id": "m2", "content": "刚才图里是什么"},
    ])
    assert isinstance(out[0]["content"], list)
    assert out[0]["content"][-1] == {"type": "text", "text": "图一"}
    assert out[2]["content"] == "刚才图里是什么"


def test_anchored_image_stays_on_original_message(tmp_path):
    loop, path = _loop(tmp_path, "on")
    loop._turn_images = []
    loop._image_anchors = [("m1", path)]

    first = AgentLoop._apply_vision_parts(loop, [{"role": "user", "id": "m1", "content": "看图"}])
    second = AgentLoop._apply_vision_parts(loop, [
        {"role": "user", "id": "m1", "content": "看图"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "id": "m2", "content": "继续"},
    ])
    assert first[0]["content"] == second[0]["content"]


def test_anchors_capped(tmp_path):
    loop, _ = _loop(tmp_path, "on", keep=2)
    paths = [_png(tmp_path / f"p{i}.png") for i in range(3)]
    loop._turn_images = []
    for i, p in enumerate(paths):
        loop._image_anchors.append((f"m{i}", p))
        AgentLoop._apply_vision_parts(loop, [{"role": "user", "id": f"m{i}", "content": "hi"}])

    assert [p for _, p in loop._image_anchors] == paths[-2:]


def test_keep_zero_disables_retention(tmp_path):
    loop, path = _loop(tmp_path, "on", keep=0)
    loop._turn_images = []
    loop._image_anchors = [("m1", path)]
    AgentLoop._apply_vision_parts(loop, [{"role": "user", "id": "m1", "content": "图一"}])
    assert loop._retained_images == []
    assert loop._image_anchors == []

    out = AgentLoop._apply_vision_parts(loop, [{"role": "user", "id": "m2", "content": "再问"}])
    assert out[-1]["content"] == "再问"


def test_vision_off_clears_retained(tmp_path):
    loop, path = _loop(tmp_path, "on")
    loop._turn_images = []
    loop._image_anchors = [("m1", path)]
    AgentLoop._apply_vision_parts(loop, [{"role": "user", "id": "m1", "content": "图一"}])
    loop.llm.vision_mode = "off"
    AgentLoop._apply_vision_parts(loop, [{"role": "user", "id": "m2", "content": "再问"}])
    assert loop._retained_images == []
    assert loop._image_anchors == []


def test_pending_images_carry_source_label(tmp_path):
    loop, path = _loop(tmp_path, "on")
    loop._turn_images = []
    loop._pending_images = [path]
    out = AgentLoop._apply_vision_parts(loop, [{"role": "user", "content": "hi"}])
    content = out[-1]["content"]
    assert content[0] == {"type": "text", "text": "[图片：pic.png]"}
    assert any(p.get("type") == "image_url" for p in content)


def test_pending_images_released_once(tmp_path):
    loop, path = _loop(tmp_path, "on")
    loop._turn_images = []
    loop._pending_images = [path]
    AgentLoop._apply_vision_parts(loop, [{"role": "user", "content": "hi"}])
    assert loop._retained_images == []

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
        {"role": "tool", "content": "{}", "tool_call_id": "c1"},
        {"role": "user", "content": "继续"},
    ]
    out = AgentLoop._apply_vision_parts(loop, messages)
    assert out[-1]["content"] == "继续"
    assert not any(isinstance(m.get("content"), list) for m in out)
