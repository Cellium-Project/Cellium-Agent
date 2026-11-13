# -*- coding: utf-8 -*-
import io
import struct

from app.core.util import attachment_store as store


def _png_bytes(size=(3000, 2000), color=None) -> bytes:
    # 仅需 PNG 头，image_info 只解析 IHDR 宽高
    ihdr = struct.pack(">II", size[0], size[1]) + b"\x08\x02\x00\x00\x00"
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
        + struct.pack(">I", 0) + b"IEND" + b"\x00\x00\x00\x00"
    )


def test_sanitize_filename_blocks_traversal():
    assert "/" not in store.sanitize_filename("../../etc/passwd")
    assert "\\" not in store.sanitize_filename("..\\..\\windows\\x.png")
    assert store.sanitize_filename("") == "file"


def test_save_stream_and_find(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "UPLOAD_DIR", str(tmp_path))
    att = store.save_stream(
        io.BytesIO(_png_bytes((400, 300))),
        "../evil name.png",
        vision=io.BytesIO(b"vision-bytes"),
        thumb=io.BytesIO(b"thumb-bytes"),
    )
    assert att["file_id"]
    assert att["file_type"] == "image"
    assert "evil" in att["filename"]
    assert att["width"] == 400 and att["height"] == 300
    assert att["thumb_url"].endswith("?thumb=1")
    assert att["vision_local_path"] and store.find_vision(att["file_id"])
    assert store.find_thumb(att["file_id"])
    found = store.find_file(att["file_id"])
    assert found and found[0] == att["local_path"]


def test_image_to_part_uses_original(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(_png_bytes())
    part = store.image_to_part(str(path))
    assert part and part["mime"] == "image/png"
    assert (part["width"], part["height"]) == (3000, 2000)


def test_image_to_part_skips_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_VISION_BYTES", 10)
    path = tmp_path / "big.png"
    path.write_bytes(_png_bytes((64, 64)))
    assert store.image_to_part(str(path)) is None


def test_inline_media_types():
    assert store.is_inline_type("a.png") is True
    assert store.is_inline_type("a.svg") is False
    assert store.is_inline_type("a.zip") is False
    assert store.media_type_for("a.png") == "image/png"
