import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

QQ_API_HOST = "q.qq.com"
QR_CODE_URL = "https://q.qq.com/qqbot/openclaw/connect.html"

BIND_STATUS_NONE = 0
BIND_STATUS_PENDING = 1
BIND_STATUS_COMPLETED = 2
BIND_STATUS_EXPIRED = 3


@dataclass
class BindTask:
    task_id: str
    key: str


@dataclass
class BindResult:
    status: int
    bot_appid: str
    bot_encrypt_secret: str


def _generate_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# Pure Python AES-256-GCM implementation
_SBOX = (
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
)
_RCON = (0x00000000, 0x01000000, 0x02000000, 0x04000000, 0x08000000, 0x10000000, 0x20000000, 0x40000000, 0x80000000, 0x1b000000, 0x36000000)


def _sub_word(w: int) -> int:
    return (_SBOX[(w >> 24) & 0xff] << 24) | (_SBOX[(w >> 16) & 0xff] << 16) | (_SBOX[(w >> 8) & 0xff] << 8) | _SBOX[w & 0xff]


def _rot_word(w: int) -> int:
    return ((w << 8) | (w >> 24)) & 0xffffffff


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x11b) & 0xff if a & 0x80 else (a << 1) & 0xff


def _gmul(a: int, b: int) -> int:
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r


def _aes_256_key_expansion(key: bytes) -> list:
    w = [0] * 60
    for i in range(8):
        w[i] = int.from_bytes(key[4*i:4*i+4], 'big')
    for i in range(8, 60):
        t = w[i-1]
        if i % 8 == 0:
            t = _sub_word(_rot_word(t)) ^ _RCON[i // 8]
        elif i % 8 == 4:
            t = _sub_word(t)
        w[i] = w[i-8] ^ t
    return w


def _sub_bytes(s: bytearray) -> None:
    for i in range(16):
        s[i] = _SBOX[s[i]]


def _shift_rows(s: bytearray) -> None:
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]


def _mix_columns(s: bytearray) -> None:
    for c in range(4):
        i = 4 * c
        s0, s1, s2, s3 = s[i], s[i+1], s[i+2], s[i+3]
        s[i]   = _gmul(2, s0) ^ _gmul(3, s1) ^ s2 ^ s3
        s[i+1] = s0 ^ _gmul(2, s1) ^ _gmul(3, s2) ^ s3
        s[i+2] = s0 ^ s1 ^ _gmul(2, s2) ^ _gmul(3, s3)
        s[i+3] = _gmul(3, s0) ^ s1 ^ s2 ^ _gmul(2, s3)


def _add_round_key(s: bytearray, w: list, rnd: int) -> None:
    for c in range(4):
        word = w[4 * rnd + c]
        i = 4 * c
        s[i]   ^= (word >> 24) & 0xff
        s[i+1] ^= (word >> 16) & 0xff
        s[i+2] ^= (word >> 8) & 0xff
        s[i+3] ^= word & 0xff


def _aes_encrypt_block(block: bytes, w: list) -> bytes:
    s = bytearray(block)
    _add_round_key(s, w, 0)
    for rnd in range(1, 14):
        _sub_bytes(s)
        _shift_rows(s)
        _mix_columns(s)
        _add_round_key(s, w, rnd)
    _sub_bytes(s)
    _shift_rows(s)
    _add_round_key(s, w, 14)
    return bytes(s)


def _inc_counter(counter: bytearray) -> None:
    val = int.from_bytes(counter[12:], 'big') + 1
    counter[12:] = val.to_bytes(4, 'big')


def _ghash_mul(x: int, y: int) -> int:
    R = 0xe1 << 120
    z = 0
    v = y
    for i in range(128):
        if x & (1 << (127 - i)):
            z ^= v
        if v & 1:
            v = (v >> 1) ^ R
        else:
            v >>= 1
    return z


def _ghash(H: int, data: bytes) -> int:
    Y = 0
    for i in range(0, len(data), 16):
        X = int.from_bytes(data[i:i+16], 'big')
        Y = _ghash_mul(Y ^ X, H)
    return Y


def _gcm_ctr(ciphertext: bytes, J0: bytes, w: list) -> bytes:
    counter = bytearray(J0)
    _inc_counter(counter)
    keystream = b''
    while len(keystream) < len(ciphertext):
        keystream += _aes_encrypt_block(bytes(counter), w)
        _inc_counter(counter)
    return bytes(c ^ k for c, k in zip(ciphertext, keystream))


def _aes_256_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b'') -> bytes:
    w = _aes_256_key_expansion(key)
    H = int.from_bytes(_aes_encrypt_block(b'\x00' * 16, w), 'big')
    J0 = iv + b'\x00\x00\x00\x01'

    plaintext = _gcm_ctr(ciphertext, J0, w)

    ghash_data = aad
    if len(aad) % 16 != 0:
        ghash_data += b'\x00' * (16 - len(aad) % 16)
    ghash_data += ciphertext
    if len(ciphertext) % 16 != 0:
        ghash_data += b'\x00' * (16 - len(ciphertext) % 16)
    ghash_data += (len(aad) * 8).to_bytes(8, 'big')
    ghash_data += (len(ciphertext) * 8).to_bytes(8, 'big')

    S = _ghash(H, ghash_data)
    expected_tag = (S ^ int.from_bytes(_aes_encrypt_block(J0, w), 'big')).to_bytes(16, 'big')

    result = 0
    for x, y in zip(tag, expected_tag):
        result |= x ^ y
    if result != 0:
        raise ValueError("Authentication failed")

    return plaintext


def _decrypt_secret(encrypted_secret: str, key_b64: str) -> str:
    key = base64.b64decode(key_b64)
    data = base64.b64decode(encrypted_secret)
    return _aes_256_gcm_decrypt(key, data[:12], data[12:-16], data[-16:]).decode()


def build_connect_url(task_id: str, source: str = "") -> str:
    from urllib.parse import urlencode, quote
    params = urlencode({"task_id": task_id, "source": source, "_wv": "2"})
    return f"{QR_CODE_URL}?{params}"


class QQConnectClient:
    def __init__(self, host: str = QQ_API_HOST, timeout: float = 10.0):
        self.host = host
        self.timeout = timeout

    async def create_bind_task(self) -> BindTask:
        key = _generate_key()
        url = f"https://{self.host}/lite/create_bind_task"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json={"key": key},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("retcode") != 0:
                raise RuntimeError(f"create_bind_task failed: {data.get('msg', 'unknown')}")
            task_id = data["data"]["task_id"]
            return BindTask(task_id=task_id, key=key)

    async def poll_bind_result(self, task_id: str) -> BindResult:
        url = f"https://{self.host}/lite/poll_bind_result"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json={"task_id": task_id},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("retcode") != 0:
                raise RuntimeError(f"poll_bind_result failed: {data.get('msg', 'unknown')}")
            d = data.get("data", {})
            return BindResult(
                status=d.get("status", BIND_STATUS_NONE),
                bot_appid=str(d.get("bot_appid", "")),
                bot_encrypt_secret=d.get("bot_encrypt_secret", ""),
            )

    async def login_qr_start(self) -> dict:
        task = await self.create_bind_task()
        qrcode_url = build_connect_url(task.task_id)
        return {
            "task_id": task.task_id,
            "key": task.key,
            "qrcode_url": qrcode_url,
        }

    async def login_qr_poll(self, task_id: str, key: str) -> dict:
        result = await self.poll_bind_result(task_id)
        if result.status == BIND_STATUS_COMPLETED:
            app_secret = _decrypt_secret(result.bot_encrypt_secret, key)
            logger.info("[QQConnect] 扫码登录成功: app_id=%s", result.bot_appid)
            return {
                "status": "confirmed",
                "app_id": result.bot_appid,
                "app_secret": app_secret,
            }
        elif result.status == BIND_STATUS_EXPIRED:
            return {"status": "expired"}
        else:
            return {"status": "waiting"}
