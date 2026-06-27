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


def _decrypt_secret(encrypted_secret: str, key_b64: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = base64.b64decode(key_b64)
    data = base64.b64decode(encrypted_secret)
    iv = data[:12]
    ct = data[12:-16]
    tag = data[-16:]

    aesgcm = AESGCM(key)
    return aesgcm.decrypt(iv, ct + tag, None).decode()


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
