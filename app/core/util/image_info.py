# -*- coding: utf-8 -*-

import os
from typing import Optional, Dict

_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}

VISION_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def mime_for(path: str) -> Optional[str]:
    return _MIME.get(os.path.splitext(path)[1].lower())


def _png(data: bytes):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _gif(data: bytes):
    if len(data) < 10 or data[:3] != b"GIF":
        return None
    return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")


def _bmp(data: bytes):
    if len(data) < 26 or data[:2] != b"BM":
        return None
    return int.from_bytes(data[18:22], "little"), int.from_bytes(data[22:26], "little")


def _webp(data: bytes):
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return w, h
    if kind == b"VP8 ":
        w = int.from_bytes(data[26:28], "little") & 0x3FFF
        h = int.from_bytes(data[28:30], "little") & 0x3FFF
        return w, h
    if kind == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        w = 1 + (((b[1] & 0x3F) << 8) | b[0])
        h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        return w, h
    return None


def _jpeg(data: bytes):
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + max(length, 2)
    return None


def image_info(path: str) -> Optional[Dict[str, int]]:
    """返回 {mime, width, height}；不支持或解析失败返回 None"""
    mime = mime_for(path)
    if not mime:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read(512 * 1024)
    except OSError:
        return None

    size = None
    if mime == "image/png":
        size = _png(data)
    elif mime == "image/gif":
        size = _gif(data)
    elif mime == "image/webp":
        size = _webp(data)
    elif mime == "image/jpeg":
        size = _jpeg(data)
    elif mime == "image/bmp":
        size = _bmp(data)

    if not size:
        return None
    return {"mime": mime, "width": size[0], "height": size[1]}
