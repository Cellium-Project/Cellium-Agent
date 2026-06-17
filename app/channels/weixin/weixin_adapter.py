# -*- coding: utf-8 -*-
"""
WeixinAdapter - 微信 iLink Bot 通道适配器
将微信 iLink Bot 协议适配为统一 ChannelAdapter 接口
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx

from ..base import ChannelAdapter, UnifiedMessage
from .weixin_config import WeixinChannelConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = 0x00020403  # 2.4.3
DEFAULT_BOT_AGENT = "Cellium Agent"
DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
DEFAULT_API_TIMEOUT_MS = 15_000
SESSION_EXPIRED_ERRCODE = -14


# ---------------------------------------------------------------------------
# 枚举 类型
# ---------------------------------------------------------------------------

class MessageItemType(IntEnum):
    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class MessageType(IntEnum):
    NONE = 0
    USER = 1
    BOT = 2


class MessageState(IntEnum):
    NEW = 0
    GENERATING = 1
    FINISH = 2


class UploadMediaType(IntEnum):
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class TypingStatus(IntEnum):
    TYPING = 1
    CANCEL = 2


@dataclass
class CDNMedia:
    encrypt_query_param: Optional[str] = None
    aes_key: Optional[str] = None
    encrypt_type: Optional[int] = None
    full_url: Optional[str] = None


@dataclass
class TextItem:
    text: Optional[str] = None


@dataclass
class ImageItem:
    media: Optional[CDNMedia] = None
    thumb_media: Optional[CDNMedia] = None
    aeskey: Optional[str] = None
    url: Optional[str] = None
    mid_size: Optional[int] = None
    hd_size: Optional[int] = None


@dataclass
class VoiceItem:
    media: Optional[CDNMedia] = None
    encode_type: Optional[int] = None
    playtime: Optional[int] = None
    text: Optional[str] = None


@dataclass
class FileItem:
    media: Optional[CDNMedia] = None
    file_name: Optional[str] = None
    md5: Optional[str] = None
    len: Optional[str] = None


@dataclass
class VideoItem:
    media: Optional[CDNMedia] = None
    video_size: Optional[int] = None
    thumb_media: Optional[CDNMedia] = None


@dataclass
class RefMessage:
    message_item: Optional[dict] = None
    title: Optional[str] = None


@dataclass
class MessageItem:
    type: Optional[int] = None
    text_item: Optional[TextItem] = None
    image_item: Optional[ImageItem] = None
    voice_item: Optional[VoiceItem] = None
    file_item: Optional[FileItem] = None
    video_item: Optional[VideoItem] = None
    ref_msg: Optional[RefMessage] = None


@dataclass
class WeixinMessage:
    seq: Optional[int] = None
    message_id: Optional[int] = None
    from_user_id: Optional[str] = None
    to_user_id: Optional[str] = None
    create_time_ms: Optional[int] = None
    session_id: Optional[str] = None
    message_type: Optional[int] = None
    message_state: Optional[int] = None
    item_list: list[MessageItem] = field(default_factory=list)
    context_token: Optional[str] = None


@dataclass
class UploadedFileInfo:
    filekey: str
    download_encrypted_query_param: str
    aeskey: str
    file_size: int
    file_size_ciphertext: int


_AES_SBOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]

_AES_RSBOX = [0] * 256
for _i, _v in enumerate(_AES_SBOX):
    _AES_RSBOX[_v] = _i

_AES_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _aes_sub_word(w: int) -> int:
    return (_AES_SBOX[(w >> 24) & 0xFF] << 24) | (_AES_SBOX[(w >> 16) & 0xFF] << 16) | (_AES_SBOX[(w >> 8) & 0xFF] << 8) | _AES_SBOX[w & 0xFF]


def _aes_rot_word(w: int) -> int:
    return ((w << 8) | (w >> 24)) & 0xFFFFFFFF


def _aes_key_expansion(key: bytes) -> list[int]:
    rk = [int.from_bytes(key[i:i+4], 'big') for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = rk[i - 1]
        if i % 4 == 0:
            t = _aes_sub_word(_aes_rot_word(t)) ^ (_AES_RCON[i // 4 - 1] << 24)
        rk.append(rk[i - 4] ^ t)
    return rk


def _aes_add_round_key(state: list[int], rk: list[int]):
    for i in range(4):
        state[i] ^= rk[i]


def _aes_sub_bytes(state: list[int]):
    for i in range(4):
        state[i] = _aes_sub_word(state[i])


def _aes_inv_sub_bytes(state: list[int]):
    for i in range(4):
        w = state[i]
        state[i] = (_AES_RSBOX[(w >> 24) & 0xFF] << 24) | (_AES_RSBOX[(w >> 16) & 0xFF] << 16) | (_AES_RSBOX[(w >> 8) & 0xFF] << 8) | _AES_RSBOX[w & 0xFF]


def _aes_shift_rows(state: list[int]):
    s = [b for w in state for b in w.to_bytes(4, 'big')]
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]
    for i in range(4):
        state[i] = int.from_bytes(s[i*4:(i+1)*4], 'big')


def _aes_inv_shift_rows(state: list[int]):
    s = [b for w in state for b in w.to_bytes(4, 'big')]
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]
    for i in range(4):
        state[i] = int.from_bytes(s[i*4:(i+1)*4], 'big')


_GMUL2 = [((i << 1) ^ 0x11B) & 0xFF if i & 0x80 else (i << 1) for i in range(256)]


def _aes_mix_columns(state: list[int]):
    for c in range(4):
        a = [(state[c] >> (24 - 8 * r)) & 0xFF for r in range(4)]
        state[c] = (
            (_GMUL2[a[0]] ^ _GMUL2[a[1]] ^ a[1] ^ a[2] ^ a[3]) << 24 |
            (a[0] ^ _GMUL2[a[1]] ^ _GMUL2[a[2]] ^ a[2] ^ a[3]) << 16 |
            (a[0] ^ a[1] ^ _GMUL2[a[2]] ^ _GMUL2[a[3]] ^ a[3]) << 8 |
            (_GMUL2[a[0]] ^ a[0] ^ a[1] ^ a[2] ^ _GMUL2[a[3]])
        )


def _aes_inv_mix_columns(state: list[int]):
    for c in range(4):
        a = [(state[c] >> (24 - 8 * r)) & 0xFF for r in range(4)]
        state[c] = (
            (_aes_xtime(a[0], 14) ^ _aes_xtime(a[1], 11) ^ _aes_xtime(a[2], 13) ^ _aes_xtime(a[3], 9)) << 24 |
            (_aes_xtime(a[0], 9) ^ _aes_xtime(a[1], 14) ^ _aes_xtime(a[2], 11) ^ _aes_xtime(a[3], 13)) << 16 |
            (_aes_xtime(a[0], 13) ^ _aes_xtime(a[1], 9) ^ _aes_xtime(a[2], 14) ^ _aes_xtime(a[3], 11)) << 8 |
            (_aes_xtime(a[0], 11) ^ _aes_xtime(a[1], 13) ^ _aes_xtime(a[2], 9) ^ _aes_xtime(a[3], 14))
        )


def _aes_xtime(x: int, mul: int) -> int:
    r = 0
    while mul:
        if mul & 1:
            r ^= x
        x = _GMUL2[x]
        mul >>= 1
    return r


def _aes_ecb_encrypt_block(block: bytes, rk: list[int]) -> bytes:
    state = [int.from_bytes(block[i:i+4], 'big') for i in range(0, 16, 4)]
    _aes_add_round_key(state, rk[:4])
    for rnd in range(1, 10):
        _aes_sub_bytes(state)
        _aes_shift_rows(state)
        _aes_mix_columns(state)
        _aes_add_round_key(state, rk[rnd*4:(rnd+1)*4])
    _aes_sub_bytes(state)
    _aes_shift_rows(state)
    _aes_add_round_key(state, rk[40:44])
    return b''.join(w.to_bytes(4, 'big') for w in state)


def _aes_ecb_decrypt_block(block: bytes, rk: list[int]) -> bytes:
    state = [int.from_bytes(block[i:i+4], 'big') for i in range(0, 16, 4)]
    _aes_add_round_key(state, rk[40:44])
    for rnd in range(9, 0, -1):
        _aes_inv_shift_rows(state)
        _aes_inv_sub_bytes(state)
        _aes_add_round_key(state, rk[rnd*4:(rnd+1)*4])
        _aes_inv_mix_columns(state)
    _aes_inv_shift_rows(state)
    _aes_inv_sub_bytes(state)
    _aes_add_round_key(state, rk[:4])
    return b''.join(w.to_bytes(4, 'big') for w in state)


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    rk = _aes_key_expansion(key)
    padded = _pkcs7_pad(plaintext)
    result = bytearray()
    for i in range(0, len(padded), 16):
        result.extend(_aes_ecb_encrypt_block(padded[i:i+16], rk))
    return bytes(result)


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    rk = _aes_key_expansion(key)
    result = bytearray()
    for i in range(0, len(ciphertext), 16):
        result.extend(_aes_ecb_decrypt_block(ciphertext[i:i+16], rk))
    return _pkcs7_unpad(bytes(result))


def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = 16 - (len(data) % 16)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data: bytes) -> bytes:
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("invalid PKCS7 padding")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("invalid PKCS7 padding")
    return data[:-pad_len]


def _aes_ecb_padded_size(plaintext_size: int) -> int:
    return ((plaintext_size // 16) + 1) * 16


def _parse_aes_key(aes_key_b64: str) -> bytes:
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            ascii_str = decoded.decode("ascii")
            if all(c in "0123456789abcdefABCDEF" for c in ascii_str):
                return bytes.fromhex(ascii_str)
        except UnicodeDecodeError:
            pass
    raise ValueError(f"Invalid aes_key: decoded {len(decoded)} bytes from base64")


def _random_wechat_uin() -> str:
    n = secrets.randbelow(0xFFFFFFFF)
    return base64.b64encode(str(n).encode()).decode()


def _build_common_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }


def _build_headers(token: Optional[str] = None) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    h.update(_build_common_headers())
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _build_base_info(bot_agent: str = DEFAULT_BOT_AGENT) -> dict:
    return {
        "channel_version": "2.4.3",
        "bot_agent": bot_agent,
    }


def _cdn_download_url(encrypted_query_param: str, cdn_base_url: str = CDN_BASE_URL) -> str:
    return f"{cdn_base_url}/download?encrypted_query_param={quote(encrypted_query_param)}"


def _cdn_upload_url(upload_param: str, filekey: str, cdn_base_url: str = CDN_BASE_URL) -> str:
    return f"{cdn_base_url}/upload?encrypted_query_param={quote(upload_param)}&filekey={quote(filekey)}"


def _generate_client_id() -> str:
    return f"openclaw-weixin-{uuid.uuid4().hex[:16]}"


def _parse_cdn_media(d: Optional[dict]) -> Optional[CDNMedia]:
    if not d:
        return None
    return CDNMedia(
        encrypt_query_param=d.get("encrypt_query_param"),
        aes_key=d.get("aes_key"),
        encrypt_type=d.get("encrypt_type"),
        full_url=d.get("full_url"),
    )


def _parse_message_item(d: Optional[dict]) -> Optional[MessageItem]:
    if not d:
        return None
    return MessageItem(
        type=d.get("type"),
        text_item=TextItem(text=d["text_item"]["text"]) if d.get("text_item") else None,
        image_item=ImageItem(
            media=_parse_cdn_media(d.get("image_item", {}).get("media")),
            aeskey=d.get("image_item", {}).get("aeskey"),
            mid_size=d.get("image_item", {}).get("mid_size"),
            hd_size=d.get("image_item", {}).get("hd_size"),
        ) if d.get("image_item") else None,
        voice_item=VoiceItem(
            media=_parse_cdn_media(d.get("voice_item", {}).get("media")),
            encode_type=d.get("voice_item", {}).get("encode_type"),
            playtime=d.get("voice_item", {}).get("playtime"),
            text=d.get("voice_item", {}).get("text"),
        ) if d.get("voice_item") else None,
        file_item=FileItem(
            media=_parse_cdn_media(d.get("file_item", {}).get("media")),
            file_name=d.get("file_item", {}).get("file_name"),
            md5=d.get("file_item", {}).get("md5"),
            len=d.get("file_item", {}).get("len"),
        ) if d.get("file_item") else None,
        video_item=VideoItem(
            media=_parse_cdn_media(d.get("video_item", {}).get("media")),
            video_size=d.get("video_item", {}).get("video_size"),
            thumb_media=_parse_cdn_media(d.get("video_item", {}).get("thumb_media")),
        ) if d.get("video_item") else None,
        ref_msg=RefMessage(
            message_item=d.get("ref_msg", {}).get("message_item"),
            title=d.get("ref_msg", {}).get("title"),
        ) if d.get("ref_msg") else None,
    )


def _parse_weixin_message(d: dict) -> WeixinMessage:
    items = []
    for i in (d.get("item_list") or []):
        item = _parse_message_item(i)
        if item:
            items.append(item)
    return WeixinMessage(
        seq=d.get("seq"),
        message_id=d.get("message_id"),
        from_user_id=d.get("from_user_id"),
        to_user_id=d.get("to_user_id"),
        create_time_ms=d.get("create_time_ms"),
        session_id=d.get("session_id"),
        message_type=d.get("message_type"),
        message_state=d.get("message_state"),
        item_list=items,
        context_token=d.get("context_token"),
    )


def _extract_text(msg: WeixinMessage) -> str:
    for item in msg.item_list:
        if item.type == MessageItemType.TEXT and item.text_item and item.text_item.text:
            text = item.text_item.text
            if item.ref_msg:
                ref_parts = []
                if item.ref_msg.title:
                    ref_parts.append(item.ref_msg.title)
                if text:
                    return f"[引用: {' | '.join(ref_parts)}]\n{text}" if ref_parts else text
            return text
        if item.type == MessageItemType.VOICE and item.voice_item and item.voice_item.text:
            return item.voice_item.text
    return ""


def _is_media_item(item: MessageItem) -> bool:
    return item.type in (MessageItemType.IMAGE, MessageItemType.VIDEO, MessageItemType.FILE, MessageItemType.VOICE)


class ContextTokenStore:
    def __init__(self, state_dir: Optional[Path] = None):
        self._store: dict[str, str] = {}
        self._state_dir = state_dir

    def _key(self, account_id: str, user_id: str) -> str:
        return f"{account_id}:{user_id}"

    def set(self, account_id: str, user_id: str, token: str):
        self._store[self._key(account_id, user_id)] = token
        self._persist(account_id)

    def get(self, account_id: str, user_id: str) -> Optional[str]:
        return self._store.get(self._key(account_id, user_id))

    def find_account_ids(self, account_ids: list[str], user_id: str) -> list[str]:
        return [aid for aid in account_ids if self._key(aid, user_id) in self._store]

    def clear_for_account(self, account_id: str):
        prefix = f"{account_id}:"
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]
        self._delete_persist_file(account_id)

    def restore(self, account_id: str):
        if not self._state_dir:
            return
        fp = self._state_dir / f"{account_id}.context-tokens.json"
        if not fp.exists():
            return
        try:
            data = json.loads(fp.read_text("utf-8"))
            prefix = f"{account_id}:"
            for uid, tok in data.items():
                if tok:
                    self._store[f"{prefix}{uid}"] = tok
        except Exception as e:
            logger.warning("restore context tokens failed: %s", e)

    def _persist(self, account_id: str):
        if not self._state_dir:
            return
        prefix = f"{account_id}:"
        tokens = {k[len(prefix):]: v for k, v in self._store.items() if k.startswith(prefix)}
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            fp = self._state_dir / f"{account_id}.context-tokens.json"
            fp.write_text(json.dumps(tokens), "utf-8")
        except Exception as e:
            logger.warning("persist context tokens failed: %s", e)

    def _delete_persist_file(self, account_id: str):
        if not self._state_dir:
            return
        fp = self._state_dir / f"{account_id}.context-tokens.json"
        try:
            if fp.exists():
                fp.unlink()
        except Exception:
            pass


@dataclass
class AccountData:
    token: Optional[str] = None
    base_url: Optional[str] = None
    user_id: Optional[str] = None


class AccountStore:
    def __init__(self, state_dir: Path):
        self._dir = state_dir / "accounts"
        self._index_file = state_dir / "accounts.json"

    def list_ids(self) -> list[str]:
        if not self._index_file.exists():
            return []
        try:
            return [x for x in json.loads(self._index_file.read_text("utf-8")) if isinstance(x, str) and x.strip()]
        except Exception:
            return []

    def register(self, account_id: str):
        ids = self.list_ids()
        if account_id in ids:
            return
        ids.append(account_id)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        self._index_file.write_text(json.dumps(ids, indent=2), "utf-8")

    def load(self, account_id: str) -> Optional[AccountData]:
        fp = self._dir / f"{account_id}.json"
        if not fp.exists():
            return None
        try:
            d = json.loads(fp.read_text("utf-8"))
            return AccountData(
                token=d.get("token"),
                base_url=d.get("baseUrl"),
                user_id=d.get("userId"),
            )
        except Exception:
            return None

    def save(self, account_id: str, token: Optional[str] = None, base_url: Optional[str] = None, user_id: Optional[str] = None):
        self._dir.mkdir(parents=True, exist_ok=True)
        existing = self.load(account_id) or AccountData()
        data = {}
        t = (token or existing.token or "").strip()
        if t:
            data["token"] = t
            data["savedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        bu = (base_url or existing.base_url or "").strip()
        if bu:
            data["baseUrl"] = bu
        uid = (user_id or existing.user_id or "").strip()
        if uid:
            data["userId"] = uid
        fp = self._dir / f"{account_id}.json"
        fp.write_text(json.dumps(data, indent=2), "utf-8")

    def clear_all(self):
        """清空所有账号（单账号）"""
        import shutil
        if self._dir.exists():
            shutil.rmtree(self._dir)
        if self._index_file.exists():
            self._index_file.unlink()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        self._index_file.write_text("[]", "utf-8")


class WeixinClient:

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        cdn_base_url: str = CDN_BASE_URL,
        token: Optional[str] = None,
        bot_agent: str = DEFAULT_BOT_AGENT,
        state_dir: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.cdn_base_url = cdn_base_url.rstrip("/")
        self.token = token
        self.bot_agent = bot_agent
        self._state_dir = Path(state_dir) if state_dir else None
        self._account_store = AccountStore(self._state_dir) if self._state_dir else None
        self._ctx_token_store = ContextTokenStore(self._state_dir / "accounts" if self._state_dir else None)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._get_updates_buf = ""
        self._running = False
        self._account_id: Optional[str] = None

    async def login_qr_start(self, bot_type: str = "3") -> dict:
        resp = await self._post(
            "ilink/bot/get_bot_qrcode",
            {"local_token_list": self._get_local_tokens()},
            params={"bot_type": bot_type},
            no_auth=True,
        )
        return resp

    async def login_qr_poll(self, qrcode: str, verify_code: Optional[str] = None) -> dict:
        params = {"qrcode": qrcode}
        if verify_code:
            params["verify_code"] = verify_code
        resp = await self._get("ilink/bot/get_qrcode_status", params=params, no_auth=True)
        return resp

    async def login_with_qr(
        self,
        on_qr: Optional[Callable[[str], None]] = None,
        timeout: float = 480.0,
        bot_type: str = "3",
    ) -> dict:
        start = await self.login_qr_start(bot_type)
        qrcode_url = start.get("qrcode_img_content", "")
        qrcode = start.get("qrcode", "")

        if not qrcode_url:
            raise RuntimeError(f"获取二维码失败: {start}")

        if on_qr:
            on_qr(qrcode_url)
        else:
            logger.info(f"请扫码添加微信bot: {qrcode_url}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self.login_qr_poll(qrcode)

            st = status.get("status", "wait")
            if st == "confirmed":
                bot_token = status.get("bot_token", "")
                ilink_bot_id = status.get("ilink_bot_id", "")
                baseurl = status.get("baseurl", "")
                user_id = status.get("ilink_user_id", "")

                if not ilink_bot_id:
                    raise RuntimeError("登录确认但缺少 ilink_bot_id")

                account_id = ilink_bot_id.replace("@", "-").replace("/", "_")
                self.token = bot_token
                self.base_url = (baseurl or self.base_url).rstrip("/")
                self._account_id = account_id

                if self._account_store:
                    self._account_store.clear_all()
                    self._account_store.register(account_id)
                    self._account_store.save(account_id, token=bot_token, base_url=self.base_url, user_id=user_id)

                return {
                    "connected": True,
                    "bot_token": bot_token,
                    "account_id": account_id,
                    "base_url": self.base_url,
                    "user_id": user_id,
                }

            elif st == "binded_redirect":
                return {"connected": True, "already_connected": True, "message": "已连接过"}

            elif st == "expired":
                start = await self.login_qr_start(bot_type)
                qrcode_url = start.get("qrcode_img_content", "")
                qrcode = start.get("qrcode", "")
                if not qrcode_url:
                    raise RuntimeError("刷新二维码失败")
                if on_qr:
                    on_qr(qrcode_url)

            elif st == "need_verify_code":
                logger.info("需要手机微信验证码")

            await asyncio.sleep(1.0)

        raise RuntimeError("登录超时")

    def _get_local_tokens(self) -> list[str]:
        if not self._account_store:
            return []
        ids = self._account_store.list_ids()
        tokens = []
        for aid in reversed(ids):
            data = self._account_store.load(aid)
            if data and data.token:
                tokens.append(data.token)
            if len(tokens) >= 10:
                break
        return tokens

    def restore_accounts(self) -> list[AccountData]:
        if not self._account_store:
            return []
        accounts = []
        for aid in self._account_store.list_ids():
            data = self._account_store.load(aid)
            if data and data.token:
                accounts.append(data)
                self._ctx_token_store.restore(aid)
        return accounts

    def restore_first_account(self) -> bool:
        accounts = self.restore_accounts()
        if not accounts:
            return False
        first = accounts[0]
        self.token = first.token
        if first.base_url:
            self.base_url = first.base_url.rstrip("/")
        ids = self._account_store.list_ids() if self._account_store else []
        self._account_id = ids[0] if ids else "default"
        return True

    async def get_updates(
        self,
        get_updates_buf: Optional[str] = None,
        timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
    ) -> dict:
        buf = get_updates_buf if get_updates_buf is not None else self._get_updates_buf
        body = {
            "get_updates_buf": buf,
            "base_info": _build_base_info(self.bot_agent),
        }
        try:
            resp = await self._post("ilink/bot/getupdates", body, timeout_ms=timeout_ms)
        except httpx.TimeoutException:
            return {"ret": 0, "msgs": [], "get_updates_buf": buf}

        if resp.get("get_updates_buf"):
            self._get_updates_buf = resp["get_updates_buf"]

        msgs = []
        for m in (resp.get("msgs") or []):
            msgs.append(_parse_weixin_message(m))
        resp["msgs"] = msgs
        return resp

    async def listen(
        self,
        on_message: Callable[[WeixinMessage], Any],
        timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
        max_consecutive_failures: int = 3,
        backoff_ms: int = 30_000,
        retry_ms: int = 2_000,
    ):
        self._running = True
        failures = 0

        while self._running:
            try:
                resp = await self.get_updates(timeout_ms=timeout_ms)

                ret = resp.get("ret")
                errcode = resp.get("errcode")

                if ret is not None and ret != 0 or errcode is not None and errcode != 0:
                    if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
                        logger.error("会话过期，暂停 5 分钟")
                        await asyncio.sleep(300)
                        continue

                    failures += 1
                    if failures >= max_consecutive_failures:
                        logger.error("连续 %d 次失败，退避 %ds", failures, backoff_ms // 1000)
                        await asyncio.sleep(backoff_ms / 1000)
                        failures = 0
                    else:
                        await asyncio.sleep(retry_ms / 1000)
                    continue

                failures = 0
                for msg in resp.get("msgs", []):
                    if msg.context_token and msg.from_user_id:
                        account_id = getattr(self, "_account_id", "default")
                        self._ctx_token_store.set(account_id, msg.from_user_id, msg.context_token)
                    await on_message(msg)

            except Exception as e:
                logger.error("getUpdates 异常: %s", e)
                failures += 1
                if failures >= max_consecutive_failures:
                    await asyncio.sleep(backoff_ms / 1000)
                    failures = 0
                else:
                    await asyncio.sleep(retry_ms / 1000)

    def stop_listening(self):
        self._running = False

    async def send_text(
        self,
        to: str,
        text: str,
        context_token: Optional[str] = None,
    ) -> str:
        client_id = _generate_client_id()
        item_list = [{"type": MessageItemType.TEXT, "text_item": {"text": text}}] if text else []
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": client_id,
                "message_type": MessageType.BOT,
                "message_state": MessageState.FINISH,
                "item_list": item_list or None,
                "context_token": context_token,
            },
            "base_info": _build_base_info(self.bot_agent),
        }
        await self._post("ilink/bot/sendmessage", body)
        return client_id

    async def send_image(
        self,
        to: str,
        file_path: str,
        text: str = "",
        context_token: Optional[str] = None,
    ) -> str:
        uploaded = await self._upload_media(file_path, to, UploadMediaType.IMAGE)
        return await self._send_media_message(
            to, text, uploaded, MessageItemType.IMAGE, context_token,
        )

    async def send_video(
        self,
        to: str,
        file_path: str,
        text: str = "",
        context_token: Optional[str] = None,
    ) -> str:
        uploaded = await self._upload_media(file_path, to, UploadMediaType.VIDEO)
        return await self._send_media_message(
            to, text, uploaded, MessageItemType.VIDEO, context_token,
        )

    async def send_file(
        self,
        to: str,
        file_path: str,
        text: str = "",
        context_token: Optional[str] = None,
    ) -> str:
        uploaded = await self._upload_media(file_path, to, UploadMediaType.FILE)
        return await self._send_media_message(
            to, text, uploaded, MessageItemType.FILE, context_token,
            file_name=os.path.basename(file_path),
        )

    async def _send_media_message(
        self,
        to: str,
        text: str,
        uploaded: UploadedFileInfo,
        item_type: MessageItemType,
        context_token: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> str:
        last_id = ""
        if text:
            last_id = await self.send_text(to, text, context_token)

        client_id = _generate_client_id()
        aes_key_b64 = base64.b64encode(bytes.fromhex(uploaded.aeskey)).decode()

        item: dict = {"type": item_type}
        cdn_ref = {
            "encrypt_query_param": uploaded.download_encrypted_query_param,
            "aes_key": aes_key_b64,
            "encrypt_type": 1,
        }

        if item_type == MessageItemType.IMAGE:
            item["image_item"] = {"media": cdn_ref, "mid_size": uploaded.file_size_ciphertext}
        elif item_type == MessageItemType.VIDEO:
            item["video_item"] = {"media": cdn_ref, "video_size": uploaded.file_size_ciphertext}
        elif item_type == MessageItemType.FILE:
            item["file_item"] = {"media": cdn_ref, "file_name": file_name, "len": str(uploaded.file_size)}

        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": client_id,
                "message_type": MessageType.BOT,
                "message_state": MessageState.FINISH,
                "item_list": [item],
                "context_token": context_token,
            },
            "base_info": _build_base_info(self.bot_agent),
        }
        await self._post("ilink/bot/sendmessage", body)
        return client_id

    async def _upload_media(
        self,
        file_path: str,
        to_user_id: str,
        media_type: UploadMediaType,
    ) -> UploadedFileInfo:
        plaintext = Path(file_path).read_bytes()
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        filesize = _aes_ecb_padded_size(rawsize)
        filekey = secrets.token_hex(16)
        aeskey = secrets.token_bytes(16)

        upload_resp = await self._post("ilink/bot/getuploadurl", {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey.hex(),
            "base_info": _build_base_info(self.bot_agent),
        })

        upload_full_url = (upload_resp.get("upload_full_url") or "").strip()
        upload_param = upload_resp.get("upload_param", "")

        ciphertext = _aes_ecb_encrypt(plaintext, aeskey)

        if upload_full_url:
            cdn_url = upload_full_url
        elif upload_param:
            cdn_url = _cdn_upload_url(upload_param, filekey, self.cdn_base_url)
        else:
            raise RuntimeError("getUploadUrl 未返回上传地址")

        download_param = await self._cdn_upload(cdn_url, ciphertext)

        return UploadedFileInfo(
            filekey=filekey,
            download_encrypted_query_param=download_param,
            aeskey=aeskey.hex(),
            file_size=rawsize,
            file_size_ciphertext=filesize,
        )

    async def _cdn_upload(self, url: str, ciphertext: bytes, max_retries: int = 3) -> str:
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = await self._client.post(
                    url,
                    content=ciphertext,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=60.0,
                )
                if 400 <= resp.status_code < 500:
                    raise RuntimeError(f"CDN 客户端错误 {resp.status_code}: {resp.text}")
                if resp.status_code != 200:
                    raise RuntimeError(f"CDN 服务端错误 {resp.status_code}")
                download_param = resp.headers.get("x-encrypted-param")
                if not download_param:
                    raise RuntimeError("CDN 响应缺少 x-encrypted-param")
                return download_param
            except Exception as e:
                last_err = e
                if attempt < max_retries and "客户端错误" not in str(e):
                    logger.warning("CDN 上传第 %d 次失败: %s", attempt, e)
                    continue
                raise
        raise last_err or RuntimeError("CDN 上传失败")

    async def download_media(
        self,
        encrypt_query_param: str,
        aes_key_b64: str,
        save_path: Optional[str] = None,
        full_url: Optional[str] = None,
    ) -> bytes:
        key = _parse_aes_key(aes_key_b64)
        url = full_url or _cdn_download_url(encrypt_query_param, self.cdn_base_url)
        resp = await self._client.get(url, timeout=60.0)
        resp.raise_for_status()
        encrypted = resp.content
        decrypted = _aes_ecb_decrypt(encrypted, key)
        if save_path:
            Path(save_path).write_bytes(decrypted)
        return decrypted

    async def get_config(self, ilink_user_id: str, context_token: Optional[str] = None) -> dict:
        body = {
            "ilink_user_id": ilink_user_id,
            "context_token": context_token,
            "base_info": _build_base_info(self.bot_agent),
        }
        return await self._post("ilink/bot/getconfig", body, timeout_ms=10_000)

    async def send_typing(
        self,
        ilink_user_id: str,
        typing_ticket: str,
        status: TypingStatus = TypingStatus.TYPING,
    ):
        body = {
            "ilink_user_id": ilink_user_id,
            "typing_ticket": typing_ticket,
            "status": status,
            "base_info": _build_base_info(self.bot_agent),
        }
        await self._post("ilink/bot/sendtyping", body, timeout_ms=10_000)

    async def notify_start(self):
        body = {"base_info": _build_base_info(self.bot_agent)}
        await self._post("ilink/bot/msg/notifystart", body, timeout_ms=10_000)

    async def notify_stop(self):
        body = {"base_info": _build_base_info(self.bot_agent)}
        await self._post("ilink/bot/msg/notifystop", body, timeout_ms=10_000)

    async def _post(
        self,
        endpoint: str,
        body: dict,
        *,
        params: Optional[dict] = None,
        timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
        no_auth: bool = False,
    ) -> dict:
        url = f"{self.base_url}/{endpoint}"
        if params:
            url += "?" + urlencode(params)
        headers = _build_common_headers() if no_auth else _build_headers(self.token)
        headers["Content-Type"] = "application/json"

        resp = await self._client.post(
            url,
            json=body,
            headers=headers,
            timeout=timeout_ms / 1000,
        )
        resp.raise_for_status()
        return resp.json()

    async def _get(
        self,
        endpoint: str,
        *,
        params: Optional[dict] = None,
        timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
        no_auth: bool = False,
    ) -> dict:
        url = f"{self.base_url}/{endpoint}"
        headers = _build_common_headers() if no_auth else _build_headers(self.token)

        resp = await self._client.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout_ms / 1000,
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._client.aclose()


class WeixinAdapter(ChannelAdapter):
    """微信 iLink Bot 适配器"""

    platform_name = "weixin"
    minimal_output = True

    def __init__(self, config: WeixinChannelConfig = None, **kwargs):
        self._config = config or WeixinChannelConfig()
        state_dir = self._config.get_state_dir()
        bot_agent = self._config.get_bot_agent()
        base_url = self._config.get_base_url()

        self._client = WeixinClient(
            base_url=base_url or DEFAULT_BASE_URL,
            bot_agent=bot_agent,
            state_dir=state_dir,
        )
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._message_handler: Optional[Callable[[UnifiedMessage], None]] = None
        # 用户ID -> context_token 缓存
        self._context_tokens: Dict[str, str] = {}

        logger.info("[WeixinAdapter] 初始化完成")

    def _get_source_label(self, message) -> str:
        return f"微信私聊（User：{message.user_id}）"

    def _get_platform_tips(self) -> str:
        return "■ 微信消息可能包含语音转文字内容"

    async def connect(self):
        if self._running:
            return

        # 存储事件循环引用
        self._loop = asyncio.get_running_loop()

        # 尝试恢复已登录账号
        restored = self._client.restore_first_account()
        if not restored:
            try:
                await self._client.login_with_qr()
            except Exception as e:
                from ..base import NonRetryableError
                raise NonRetryableError(f"微信扫码登录失败: {e}")

        self._running = True
        try:
            await self._client.notify_start()
        except Exception as e:
            logger.warning(f"[WeixinAdapter] notify_start 失败: {e}")

        self._listen_task = asyncio.create_task(self._run_listen())
        logger.info("[WeixinAdapter] 已连接，开始监听消息")

    async def _run_listen(self):
        try:
            await self._client.listen(self._on_weixin_message)
        except Exception as e:
            logger.error(f"[WeixinAdapter] 监听异常: {e}")

    async def update_config(self, state_dir: str = None):
        if state_dir:
            self._config._state_dir = state_dir
            self._client._state_dir = Path(state_dir)
            # 重新初始化 account_store
            self._client._account_store = AccountStore(self._client._state_dir) if self._client._state_dir else None
            self._client._ctx_token_store = ContextTokenStore(self._client._state_dir / "accounts" if self._client._state_dir else None)

    async def disconnect(self):
        self._running = False
        self._client.stop_listening()

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        try:
            await self._client.notify_stop()
        except Exception:
            pass

        self._loop = None
        logger.info("[WeixinAdapter] 已断开")

    def run_async(self, coro, timeout: float = 60.0):
        """
        从同步上下文安全地执行异步操作
        使用 asyncio.run_coroutine_threadsafe 在事件循环中调度协程
        """
        if not self._loop or self._loop.is_closed():
            raise RuntimeError("微信服务未连接或事件循环已关闭")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def send_message(self, target_id: str, content: str, message_type: str = "c2c", **kwargs) -> bool:
        try:
            ctx_token = self._context_tokens.get(target_id)
            await self._client.send_text(target_id, content, context_token=ctx_token)
            return True
        except Exception as e:
            logger.error(f"[WeixinAdapter] 发送消息失败: {e}")
            return False

    async def _on_weixin_message(self, msg: WeixinMessage):
        if not msg.from_user_id:
            return

        text = _extract_text(msg)

        # 缓存 context_token
        if msg.context_token and msg.from_user_id:
            self._context_tokens[msg.from_user_id] = msg.context_token

        media_parts = []
        for item in msg.item_list:
            if _is_media_item(item):
                if item.type == MessageItemType.IMAGE:
                    media_parts.append("[图片]")
                elif item.type == MessageItemType.VIDEO:
                    media_parts.append("[视频]")
                elif item.type == MessageItemType.FILE:
                    fname = item.file_item.file_name if item.file_item else "unknown"
                    media_parts.append(f"[文件: {fname}]")
                elif item.type == MessageItemType.VOICE:
                    if not text:
                        media_parts.append("[语音]")

        content = text
        if media_parts and not text:
            content = " ".join(media_parts)
        elif media_parts:
            content = text + " " + " ".join(media_parts)

        if not content.strip():
            return

        if msg.message_type == MessageType.BOT:
            return

        unified = UnifiedMessage(
            platform="weixin",
            user_id=msg.from_user_id,
            content=content,
            message_type="c2c",
            msg_id=str(msg.message_id or uuid.uuid4()),
            raw={
                "message_id": msg.message_id,
                "from_user_id": msg.from_user_id,
                "to_user_id": msg.to_user_id,
                "session_id": msg.session_id,
                "context_token": msg.context_token,
                "item_list": [
                    {
                        "type": item.type,
                        "text_item": {"text": item.text_item.text} if item.text_item else None,
                        "image_item": {
                            "aeskey": item.image_item.aeskey,
                            "media": {
                                "encrypt_query_param": item.image_item.media.encrypt_query_param if item.image_item.media else None,
                                "aes_key": item.image_item.media.aes_key if item.image_item.media else None,
                                "full_url": item.image_item.media.full_url if item.image_item.media else None,
                            },
                        } if item.image_item else None,
                        "voice_item": {
                            "text": item.voice_item.text,
                            "playtime": item.voice_item.playtime,
                            "media": {
                                "encrypt_query_param": item.voice_item.media.encrypt_query_param if item.voice_item.media else None,
                                "aes_key": item.voice_item.media.aes_key if item.voice_item.media else None,
                            },
                        } if item.voice_item else None,
                        "file_item": {
                            "file_name": item.file_item.file_name,
                            "md5": item.file_item.md5,
                            "len": item.file_item.len,
                            "media": {
                                "encrypt_query_param": item.file_item.media.encrypt_query_param if item.file_item.media else None,
                                "aes_key": item.file_item.media.aes_key if item.file_item.media else None,
                            },
                        } if item.file_item else None,
                        "video_item": {
                            "video_size": item.video_item.video_size,
                            "media": {
                                "encrypt_query_param": item.video_item.media.encrypt_query_param if item.video_item.media else None,
                                "aes_key": item.video_item.media.aes_key if item.video_item.media else None,
                            },
                        } if item.video_item else None,
                    }
                    for item in msg.item_list
                ],
            },
        )
        self._dispatch(unified)

    def extract_file_info(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_data, dict):
            return None

        item_list = raw_data.get("item_list", [])
        for item in item_list:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")

            if item_type == MessageItemType.FILE and item.get("file_item"):
                fi = item["file_item"]
                media = fi.get("media", {})
                return {
                    "filename": fi.get("file_name", "unknown"),
                    "url": media.get("full_url"),
                    "size": int(fi.get("len", 0)),
                    "file_key": media.get("encrypt_query_param"),
                    "aes_key": media.get("aes_key"),
                }

            if item_type == MessageItemType.IMAGE and item.get("image_item"):
                ii = item["image_item"]
                media = ii.get("media", {})
                return {
                    "filename": "image.jpg",
                    "url": media.get("full_url"),
                    "size": ii.get("hd_size", 0),
                    "file_key": media.get("encrypt_query_param"),
                    "aes_key": media.get("aes_key"),
                }

            if item_type == MessageItemType.VOICE and item.get("voice_item"):
                vi = item["voice_item"]
                media = vi.get("media", {})
                return {
                    "filename": "voice.amr",
                    "url": media.get("full_url"),
                    "file_key": media.get("encrypt_query_param"),
                    "aes_key": media.get("aes_key"),
                }

            if item_type == MessageItemType.VIDEO and item.get("video_item"):
                vi = item["video_item"]
                media = vi.get("media", {})
                return {
                    "filename": "video.mp4",
                    "url": media.get("full_url"),
                    "size": vi.get("video_size", 0),
                    "file_key": media.get("encrypt_query_param"),
                    "aes_key": media.get("aes_key"),
                }

        return None

    def is_file_only_message(self, message: UnifiedMessage) -> bool:
        raw_data = message.raw or {}
        item_list = raw_data.get("item_list", [])
        has_media = False
        has_text = False
        for item in item_list:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t in (MessageItemType.IMAGE, MessageItemType.VIDEO, MessageItemType.FILE):
                has_media = True
            if t == MessageItemType.VOICE and not item.get("voice_item", {}).get("text"):
                has_media = True
            if t == MessageItemType.TEXT and item.get("text_item", {}).get("text", "").strip():
                has_text = True
        return has_media and not has_text
