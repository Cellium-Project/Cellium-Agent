# -*- coding: utf-8 -*-
"""QQ 富媒体（文件/图片）发送链路测试

覆盖：
- 群聊 / 单聊 上传与发送的 URL、字段名（msg_seq 而非 seq）
- msg_id 被动回复透传、msg_seq 递增
- 二进制直传 5MB 边界、分片上传四步流程
- 软限制降级、硬限制拒绝、空文件/目录/文件不存在
- 上传结果缓存（含 ttl=0）
- qq_files 组件的平台上下文解析与错误透传
"""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import httpx

import components.qq_files as qq_files_mod
from app.channels.qq import qq_adapter as qq_mod
from app.channels.qq.qq_adapter import QQAdapter
from components.qq_files import QQFiles

MB = 1024 * 1024


# ------------------------------------------------------------------
# 测试基础设施
# ------------------------------------------------------------------

class FakeQQServer:
    """用 httpx.MockTransport 模拟 QQ Bot API"""

    def __init__(self, block_size=5 * MB, file_info="FILE_INFO_TOKEN"):
        self.block_size = block_size
        self.file_info = file_info
        self.file_uuid = "FILE_UUID"
        self.ttl = 300

        self.requests = []
        self.put_requests = []
        self.finish_payloads = []
        self.upload_payloads = []
        self.prepare_payloads = []
        self.merge_payloads = []
        self.message_payloads = []

        # 故障注入
        self.upload_status = 200
        self.upload_body = None
        self.prepare_status = 200
        self.put_status = 200
        self.finish_status = 200
        self.merge_status = 200
        self.message_status = 200
        self.message_body = None
        self.prepare_override = None

    # -- 内部 --
    def _json(self, request):
        return json.loads(request.content.decode("utf-8"))

    def _response(self, payload, status=200):
        if status >= 400:
            return httpx.Response(status, json=payload)
        return httpx.Response(status, json=payload)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method

        # 预签名 URL 的 PUT
        if method == "PUT":
            self.put_requests.append(request)
            return httpx.Response(self.put_status)

        if path.endswith("/upload_prepare"):
            body = self._json(request)
            self.prepare_payloads.append(body)
            if self.prepare_status != 200:
                return httpx.Response(self.prepare_status, json={"message": "prepare failed", "code": 40093001})
            if self.prepare_override is not None:
                return httpx.Response(200, json=self.prepare_override)

            file_size = int(body["file_size"])
            count = max(1, (file_size + self.block_size - 1) // self.block_size)
            parts = []
            for i in range(count):
                remain = file_size - i * self.block_size
                parts.append({
                    "index": i,
                    "presigned_url": f"https://cos.example.com/upload/part{i}",
                    "block_size": str(min(self.block_size, remain)),
                })
            return httpx.Response(200, json={
                "upload_id": "upload_test",
                "block_size": str(self.block_size),
                "parts": parts,
                "upload_config": {"concurrency": 2, "retry_timeout": 300, "retry_delay": 1},
            })

        if path.endswith("/upload_part_finish"):
            body = self._json(request)
            self.finish_payloads.append(body)
            if self.finish_status != 200:
                return httpx.Response(self.finish_status, json={"message": "finish failed"})
            return httpx.Response(200, json={})

        if path.endswith("/files"):
            body = self._json(request)
            if "upload_id" in body:
                self.merge_payloads.append(body)
                if self.merge_status != 200:
                    return httpx.Response(self.merge_status, json={"message": "merge failed"})
                return httpx.Response(200, json={
                    "file_uuid": self.file_uuid,
                    "file_info": self.file_info,
                    "ttl": self.ttl,
                })
            self.upload_payloads.append(body)
            if self.upload_status != 200:
                return httpx.Response(self.upload_status, json=self.upload_body or {"message": "upload failed"})
            return httpx.Response(200, json={
                "file_uuid": self.file_uuid,
                "file_info": self.file_info,
                "ttl": self.ttl,
            })

        if path.endswith("/messages"):
            body = self._json(request)
            self.message_payloads.append(body)
            if self.message_status != 200:
                return httpx.Response(self.message_status, json=self.message_body or {"message": "send failed"})
            return httpx.Response(200, json={"id": "MSG_SENT_ID", "timestamp": "2026-01-01T00:00:00+08:00"})

        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})

    # -- 断言辅助 --
    def paths(self):
        return [r.url.path for r in self.requests]


def _patch_async_client(server: FakeQQServer):
    """把模块内的 httpx.AsyncClient 替换为使用 MockTransport 的实例"""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(server.handler)

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    return mock.patch.object(qq_mod.httpx, "AsyncClient", factory)


def _make_adapter() -> QQAdapter:
    adapter = QQAdapter(app_id="", app_secret="")

    async def _fake_token():
        return "test-token"

    adapter._get_access_token = _fake_token
    return adapter


class QQMediaTestBase(unittest.TestCase):

    def setUp(self):
        qq_mod._upload_cache.clear()
        self._tmpdir = tempfile.mkdtemp(prefix="qqmedia_")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        qq_mod._upload_cache.clear()
        for root, _dirs, files in os.walk(self._tmpdir, topdown=False):
            for name in files:
                try:
                    os.unlink(os.path.join(root, name))
                except OSError:
                    pass
            for name in _dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except OSError:
                    pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def make_file(self, name: str, size: int) -> str:
        path = os.path.join(self._tmpdir, name)
        with open(path, "wb") as f:
            if size:
                f.write(b"A" * size)
        return path


# ------------------------------------------------------------------
# 上传 + 发送
# ------------------------------------------------------------------

class TestSendMediaPayload(QQMediaTestBase):

    def test_passive_budget_exhausted_skips_upload(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("pic.png", 64)
        adapter._msg_seq_counters["MSG_FULL"] = qq_mod.MAX_PASSIVE_REPLIES

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_X", path, 1, is_group=True, msg_id="MSG_FULL"
            ))

        self.assertFalse(result["success"])
        self.assertIn("被动回复次数已用尽", result["error"])
        self.assertEqual(server.requests, [], "额度用尽时不应再发起上传/发送请求")

    def test_passive_expired_error_is_translated(self):
        server = FakeQQServer()
        server.message_status = 400
        server.message_body = {
            "code": 40034128,
            "err_code": 40034128,
            "message": "回复消息失败，被动回复时间或者次数超过限制",
        }
        adapter = _make_adapter()
        path = self.make_file("pic.png", 64)

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_X", path, 1, is_group=True, msg_id="MSG_OLD"
            ))

        self.assertFalse(result["success"])
        self.assertIn("被动回复", result["error"])
        self.assertIn("重新发送一条消息", result["error"])

    def test_synthetic_msg_id_sent_as_active_message(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("pic.png", 64)

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_X", path, 1, is_group=True, msg_id="scheduler_1700000000"
            ))

        self.assertTrue(result["success"], result)
        self.assertNotIn("msg_id", server.message_payloads[0])

    def test_group_file_uses_msg_seq_and_msg_id(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("report.txt", 1024)

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_OPENID", path, 4, is_group=True, msg_id="MSG_ABC"
            ))

        self.assertTrue(result["success"], result)
        self.assertEqual(result["message_id"], "MSG_SENT_ID")

        # 上传走群聊接口
        self.assertEqual(server.paths()[0], "/v2/groups/GROUP_OPENID/files")
        body = server.upload_payloads[0]
        self.assertEqual(body["file_type"], 4)
        self.assertEqual(body["file_name"], "report.txt")
        self.assertIs(body["srv_send_msg"], False)
        self.assertEqual(base64.b64decode(body["file_data"]), b"A" * 1024)

        # 发送消息
        payload = server.message_payloads[0]
        self.assertEqual(server.paths()[-1], "/v2/groups/GROUP_OPENID/messages")
        self.assertEqual(payload["msg_type"], 7)
        self.assertEqual(payload["msg_id"], "MSG_ABC")
        self.assertEqual(payload["msg_seq"], 1)
        self.assertNotIn("seq", payload, "字段名必须是 msg_seq，不能是 seq")
        self.assertEqual(payload["media"], {"file_info": "FILE_INFO_TOKEN"})
        self.assertEqual(payload["content"], "report.txt")

    def test_c2c_image_uses_user_endpoints(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("pic.png", 2048)

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "USER_OPENID", path, 1, is_group=False, msg_id="MSG_C2C"
            ))

        self.assertTrue(result["success"], result)
        self.assertEqual(server.paths()[0], "/v2/users/USER_OPENID/files")
        self.assertEqual(server.paths()[-1], "/v2/users/USER_OPENID/messages")
        self.assertEqual(server.upload_payloads[0]["file_type"], 1)
        self.assertEqual(server.message_payloads[0]["msg_id"], "MSG_C2C")

    def test_msg_seq_increments_per_msg_id(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("a.txt", 100)

        with _patch_async_client(server):
            for _ in range(3):
                asyncio.run(adapter.send_media_message(
                    "GROUP_1", path, 4, is_group=True, msg_id="SAME_MSG"
                ))

        seqs = [p["msg_seq"] for p in server.message_payloads]
        self.assertEqual(seqs, [1, 2, 3])

    def test_active_message_without_msg_id(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("a.txt", 100)

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_1", path, 4, is_group=True, msg_id=None
            ))

        self.assertTrue(result["success"], result)
        payload = server.message_payloads[0]
        self.assertNotIn("msg_id", payload)
        self.assertEqual(payload["msg_seq"], 1)
        self.assertEqual(payload["msg_type"], 7)

    def test_send_failure_surfaces_platform_error(self):
        server = FakeQQServer()
        server.message_status = 400
        server.message_body = {"code": 40054005, "message": "消息被去重"}
        adapter = _make_adapter()
        path = self.make_file("a.txt", 100)

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_1", path, 4, is_group=True, msg_id="M1"
            ))

        self.assertFalse(result["success"])
        self.assertIn("400", result["error"])
        self.assertIn("消息被去重", result["error"])

    def test_upload_failure_surfaces_platform_error(self):
        server = FakeQQServer()
        server.upload_status = 400
        server.upload_body = {"code": 850019, "message": "不支持的文件格式"}
        adapter = _make_adapter()
        path = self.make_file("a.txt", 100)

        with _patch_async_client(server):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_1", path, 4, is_group=True, msg_id="M1"
            ))

        self.assertFalse(result["success"])
        self.assertIn("850019", result["error"])
        self.assertIn("不支持的文件格式", result["error"])
        self.assertEqual(server.message_payloads, [], "上传失败不应再发消息")

    def test_empty_response_body_still_success(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("a.txt", 100)

        real_handler = server.handler

        def handler(request):
            resp = real_handler(request)
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, content=b"")
            return resp

        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)

        def factory(*args, **kwargs):
            kwargs.pop("transport", None)
            return real_client(transport=transport, **kwargs)

        with mock.patch.object(qq_mod.httpx, "AsyncClient", factory):
            result = asyncio.run(adapter.send_media_message(
                "GROUP_1", path, 4, is_group=True, msg_id="M1"
            ))

        self.assertTrue(result["success"], result)
        self.assertEqual(result["message_id"], "")


# ------------------------------------------------------------------
# 上传边界
# ------------------------------------------------------------------

class TestUploadBoundaries(QQMediaTestBase):

    def test_missing_target_id(self):
        adapter = _make_adapter()
        path = self.make_file("a.txt", 10)
        result = asyncio.run(adapter.upload_media("", path, 4))
        self.assertIn("target_id", result["error"])

    def test_unsupported_file_type(self):
        adapter = _make_adapter()
        path = self.make_file("a.txt", 10)
        result = asyncio.run(adapter.upload_media("G", path, 9))
        self.assertIn("file_type", result["error"])

    def test_missing_file(self):
        adapter = _make_adapter()
        result = asyncio.run(adapter.upload_media("G", os.path.join(self._tmpdir, "nope.txt"), 4))
        self.assertIn("文件不存在", result["error"])

    def test_directory_rejected(self):
        adapter = _make_adapter()
        sub = os.path.join(self._tmpdir, "subdir")
        os.makedirs(sub, exist_ok=True)
        result = asyncio.run(adapter.upload_media("G", sub, 4))
        self.assertIn("不是普通文件", result["error"])

    def test_empty_file_rejected(self):
        adapter = _make_adapter()
        path = self.make_file("empty.txt", 0)
        result = asyncio.run(adapter.upload_media("G", path, 4))
        self.assertIn("文件为空", result["error"])

    def test_hard_limit_rejected(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("huge.bin", 4096)
        # 把平台硬限制临时调小，避免真的写 200MB 大文件
        with mock.patch.object(qq_mod, "UPLOAD_HARD_LIMIT", 1024):
            result = asyncio.run(adapter.upload_media("G", path, 4))
        self.assertIn("200MB", result["error"])
        self.assertEqual(server.upload_payloads, [])

    def test_exact_5mb_uses_direct_upload(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("exact.bin", 5 * MB)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertEqual(result["file_info"], "FILE_INFO_TOKEN")
        self.assertEqual(len(server.upload_payloads), 1)
        self.assertEqual(server.prepare_payloads, [], "恰好 5MB 应走二进制直传")
        self.assertEqual(len(base64.b64decode(server.upload_payloads[0]["file_data"])), 5 * MB)

    def test_over_5mb_uses_chunked_upload(self):
        server = FakeQQServer(block_size=5 * MB)
        adapter = _make_adapter()
        path = self.make_file("over.bin", 5 * MB + 1)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertEqual(result["file_info"], "FILE_INFO_TOKEN")
        self.assertEqual(server.upload_payloads, [], "超过 5MB 不应走二进制直传")
        self.assertEqual(len(server.prepare_payloads), 1)
        # 文件小于 10002432 字节时，md5_10m 与整体 md5 一致
        prepare = server.prepare_payloads[0]
        self.assertEqual(prepare["md5_10m"], prepare["md5"])

    def test_image_over_soft_limit_degrades_to_file(self):
        server = FakeQQServer(block_size=10 * MB)
        adapter = _make_adapter()
        path = self.make_file("big.png", 25 * MB)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 1, is_group=True))

        self.assertEqual(result["file_info"], "FILE_INFO_TOKEN")
        self.assertEqual(server.prepare_payloads[0]["file_type"], 4)

    def test_image_under_soft_limit_keeps_type(self):
        server = FakeQQServer(block_size=10 * MB)
        adapter = _make_adapter()
        path = self.make_file("ok.png", 10 * MB)

        with _patch_async_client(server):
            asyncio.run(adapter.upload_media("G", path, 1, is_group=True))

        self.assertEqual(server.prepare_payloads[0]["file_type"], 1)

    def test_unsupported_image_ext_degrades_to_file(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("anim.webp", 1024)

        with _patch_async_client(server):
            asyncio.run(adapter.upload_media("G", path, 1, is_group=True))

        self.assertEqual(server.upload_payloads[0]["file_type"], 4)

    def test_jpeg_kept_as_image(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("photo.JPEG", 1024)

        with _patch_async_client(server):
            asyncio.run(adapter.upload_media("G", path, 1, is_group=True))

        self.assertEqual(server.upload_payloads[0]["file_type"], 1)

    def test_non_mp4_video_degrades_to_file(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("clip.mov", 1024)

        with _patch_async_client(server):
            asyncio.run(adapter.upload_media("G", path, 2, is_group=True))

        self.assertEqual(server.upload_payloads[0]["file_type"], 4)

    def test_cache_reuse_skips_second_upload(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("dup.txt", 512)

        with _patch_async_client(server):
            asyncio.run(adapter.send_media_message("G", path, 4, is_group=True, msg_id="M1"))
            asyncio.run(adapter.send_media_message("G", path, 4, is_group=True, msg_id="M2"))

        self.assertEqual(len(server.upload_payloads), 1, "相同文件应命中缓存，不重复上传")
        self.assertEqual(len(server.message_payloads), 2)

    def test_cache_key_isolated_between_group_and_c2c(self):
        server = FakeQQServer()
        adapter = _make_adapter()
        path = self.make_file("dup.txt", 512)

        with _patch_async_client(server):
            asyncio.run(adapter.send_media_message("X", path, 4, is_group=True, msg_id="M1"))
            asyncio.run(adapter.send_media_message("X", path, 4, is_group=False, msg_id="M2"))

        self.assertEqual(len(server.upload_payloads), 2, "群聊/单聊缓存不可互通")


class TestFileHashes(QQMediaTestBase):
    """md5_10m 切片长度边界"""

    def test_slice_constant_matches_official(self):
        # 官方定义：文件前 10002432 字节的 MD5（秒传判断），而非 10MB
        self.assertEqual(qq_mod.MD5_10M_SLICE_SIZE, 10002432)

    def test_within_slice_equals_full_md5(self):
        path = self.make_file("small.bin", 1000)
        with mock.patch.object(qq_mod, "MD5_10M_SLICE_SIZE", 1000):
            md5, sha1, md5_10m = asyncio.run(qq_mod._compute_file_hashes(path))
        self.assertEqual(md5, hashlib.md5(b"A" * 1000).hexdigest())
        self.assertEqual(sha1, hashlib.sha1(b"A" * 1000).hexdigest())
        self.assertEqual(md5_10m, md5)

    def test_beyond_slice_truncates(self):
        path = self.make_file("big.bin", 2000)
        with mock.patch.object(qq_mod, "MD5_10M_SLICE_SIZE", 1000):
            md5, _sha1, md5_10m = asyncio.run(qq_mod._compute_file_hashes(path))
        self.assertEqual(md5, hashlib.md5(b"A" * 2000).hexdigest())
        self.assertEqual(md5_10m, hashlib.md5(b"A" * 1000).hexdigest())
        self.assertNotEqual(md5_10m, md5)

    def test_slice_across_chunk_boundary(self):
        # 64KB 分块读取，切片落在第二块中间时也要精确截断
        slice_size = 64 * 1024 + 10
        path = self.make_file("multi.bin", 200 * 1024)
        with mock.patch.object(qq_mod, "MD5_10M_SLICE_SIZE", slice_size):
            _md5, _sha1, md5_10m = asyncio.run(qq_mod._compute_file_hashes(path))
        self.assertEqual(md5_10m, hashlib.md5(b"A" * slice_size).hexdigest())

    def test_empty_file(self):
        path = self.make_file("empty.bin", 0)
        md5, sha1, md5_10m = asyncio.run(qq_mod._compute_file_hashes(path))
        self.assertEqual(md5, hashlib.md5(b"").hexdigest())
        self.assertEqual(sha1, hashlib.sha1(b"").hexdigest())
        self.assertEqual(md5_10m, md5)


class TestChunkedUpload(QQMediaTestBase):

    def test_four_step_flow(self):
        server = FakeQQServer(block_size=5 * MB)
        adapter = _make_adapter()
        size = 12 * MB
        path = self.make_file("big.bin", size)
        expected_md5 = hashlib.md5(b"A" * size).hexdigest()

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("GROUP_X", path, 4, is_group=True))

        self.assertEqual(result["file_info"], "FILE_INFO_TOKEN")

        # 1. upload_prepare
        prepare = server.prepare_payloads[0]
        self.assertEqual(prepare["file_size"], str(size))
        self.assertEqual(prepare["file_name"], "big.bin")
        self.assertEqual(prepare["md5"], expected_md5)
        self.assertEqual(len(prepare["sha1"]), 40)
        self.assertEqual(len(prepare["md5_10m"]), 32)
        # md5_10m 必须是「前 10002432 字节」而非「前 10MB」的 MD5
        self.assertEqual(
            prepare["md5_10m"], hashlib.md5(b"A" * qq_mod.MD5_10M_SLICE_SIZE).hexdigest()
        )
        self.assertNotEqual(prepare["md5_10m"], prepare["md5"])

        # 2. 分片 PUT —— 3 片，累计覆盖整个文件，且不带 QQBot 鉴权
        self.assertEqual(len(server.put_requests), 3)
        self.assertEqual(sum(len(r.content) for r in server.put_requests), size)
        for req in server.put_requests:
            self.assertNotIn("authorization", {k.lower() for k in req.headers.keys()})
            self.assertTrue(req.url.host == "cos.example.com")

        # 3. upload_part_finish —— 每片一次，block_size/md5 与实际一致
        self.assertEqual(len(server.finish_payloads), 3)
        self.assertEqual(sorted(p["part_index"] for p in server.finish_payloads), [0, 1, 2])
        self.assertEqual(server.finish_payloads[0]["upload_id"], "upload_test")
        by_index = {p["part_index"]: p for p in server.finish_payloads}
        self.assertEqual(int(by_index[0]["block_size"]), 5 * MB)
        self.assertEqual(int(by_index[2]["block_size"]), 2 * MB)
        self.assertEqual(
            by_index[0]["md5"], hashlib.md5(b"A" * (5 * MB)).hexdigest()
        )

        # 4. 合并
        self.assertEqual(len(server.merge_payloads), 1)
        merge = server.merge_payloads[0]
        self.assertEqual(merge["upload_id"], "upload_test")
        self.assertEqual(merge["file_name"], "big.bin")
        self.assertIs(merge["srv_send_msg"], False)

    def test_prepare_failure(self):
        server = FakeQQServer()
        server.prepare_status = 400
        adapter = _make_adapter()
        path = self.make_file("big.bin", 6 * MB)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertIn("预上传失败", result["error"])
        self.assertEqual(server.put_requests, [])

    def test_prepare_missing_upload_id(self):
        server = FakeQQServer()
        server.prepare_override = {"block_size": "10485760", "parts": []}
        adapter = _make_adapter()
        path = self.make_file("big.bin", 6 * MB)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertIn("upload_id", result["error"])

    def test_part_missing_presigned_url(self):
        server = FakeQQServer()
        server.prepare_override = {
            "upload_id": "upload_test",
            "parts": [{"index": 0, "block_size": str(6 * MB)}],
        }
        adapter = _make_adapter()
        path = self.make_file("big.bin", 6 * MB)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertIn("presigned_url", result["error"])
        self.assertEqual(server.merge_payloads, [], "分片失败不应调用合并")

    def test_put_failure_after_retries(self):
        server = FakeQQServer()
        server.put_status = 403
        adapter = _make_adapter()
        path = self.make_file("big.bin", 6 * MB)

        with mock.patch.object(qq_mod, "PART_UPLOAD_MAX_RETRIES", 0):
            with _patch_async_client(server):
                result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertIn("PUT 失败", result["error"])
        self.assertEqual(server.finish_payloads, [])
        self.assertEqual(server.merge_payloads, [])

    def test_part_finish_failure(self):
        server = FakeQQServer()
        server.finish_status = 500
        adapter = _make_adapter()
        path = self.make_file("big.bin", 6 * MB)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertIn("完成通知失败", result["error"])
        self.assertEqual(server.merge_payloads, [])

    def test_merge_failure(self):
        server = FakeQQServer()
        server.merge_status = 400
        adapter = _make_adapter()
        path = self.make_file("big.bin", 6 * MB)

        with _patch_async_client(server):
            result = asyncio.run(adapter.upload_media("G", path, 4, is_group=True))

        self.assertIn("分片合并失败", result["error"])


class TestUploadCacheTtl(unittest.TestCase):

    def setUp(self):
        qq_mod._upload_cache.clear()
        self.addCleanup(qq_mod._upload_cache.clear)

    def test_ttl_zero_is_long_lived(self):
        qq_mod._set_cached_file_info("k1", "FI", "UUID", 0)
        self.assertIsNotNone(qq_mod._get_cached_file_info("k1"))

    def test_short_ttl_not_immediately_expired(self):
        qq_mod._set_cached_file_info("k2", "FI", "UUID", 30)
        self.assertIsNotNone(qq_mod._get_cached_file_info("k2"))

    def test_negative_ttl_treated_as_long_lived(self):
        qq_mod._set_cached_file_info("k3", "FI", "UUID", -5)
        self.assertIsNotNone(qq_mod._get_cached_file_info("k3"))


# ------------------------------------------------------------------
# qq_files 组件
# ------------------------------------------------------------------

class _StubAdapter:
    """记录 send_media_message 的入参，返回预设结果"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    async def send_media_message(self, target_id, file_path, file_type,
                                 is_group=False, msg_id=None):
        self.calls.append({
            "target_id": target_id,
            "file_path": file_path,
            "file_type": file_type,
            "is_group": is_group,
            "msg_id": msg_id,
        })
        return self.result


def _make_cell(platform_context=None):
    """绕过 __init__ 的目录创建，直接构造 QQFiles 并绑定平台上下文"""
    cell = QQFiles.__new__(QQFiles)
    ctx = platform_context or {}
    cell._last_platform_context = ctx

    bound = QQFiles.execute_with_context.__get__(cell, QQFiles)

    def execute_with_context(arguments, session_id=None, platform_context=ctx):
        return bound(arguments, session_id=session_id, platform_context=platform_context)

    cell.execute_with_context = execute_with_context
    return cell


class TestQQFilesCell(QQMediaTestBase):

    def _patch_adapter(self, cell, adapter):
        cell._get_adapter = lambda: adapter

    def test_group_context_passes_msg_id(self):
        adapter = _StubAdapter({"success": True, "message_id": "X"})
        cell = _make_cell({
            "target_id": "GROUP_OPENID",
            "message_type": "group",
            "msg_id": "MSG_FROM_EVENT",
        })
        self._patch_adapter(cell, adapter)

        path = self.make_file("r.txt", 10)
        result = cell.execute_with_context({"command": "send_file", "file_path": path})

        self.assertTrue(result["success"], result)
        call = adapter.calls[0]
        self.assertEqual(call["target_id"], "GROUP_OPENID")
        self.assertTrue(call["is_group"])
        self.assertEqual(call["msg_id"], "MSG_FROM_EVENT")
        self.assertEqual(call["file_type"], 4)

    def test_c2c_context(self):
        adapter = _StubAdapter({"success": True})
        cell = _make_cell({
            "target_id": "USER_OPENID",
            "message_type": "c2c",
            "msg_id": "MSG_C2C",
        })
        self._patch_adapter(cell, adapter)

        path = self.make_file("p.png", 10)
        result = cell.execute_with_context({"command": "send_image", "image_path": path})

        self.assertTrue(result["success"], result)
        call = adapter.calls[0]
        self.assertFalse(call["is_group"])
        self.assertEqual(call["file_type"], 1)
        self.assertEqual(call["msg_id"], "MSG_C2C")

    def test_explicit_params_override_context(self):
        adapter = _StubAdapter({"success": True})
        cell = _make_cell({"target_id": "CTX", "message_type": "c2c", "msg_id": "CTX_MSG"})
        self._patch_adapter(cell, adapter)

        path = self.make_file("r.txt", 10)
        result = cell.execute_with_context({
            "command": "send_file",
            "file_path": path,
            "target_id": "EXPLICIT",
            "is_group": True,
            "msg_id": "EXPLICIT_MSG",
        })

        self.assertTrue(result["success"], result)
        call = adapter.calls[0]
        self.assertEqual(call["target_id"], "EXPLICIT")
        self.assertTrue(call["is_group"])
        self.assertEqual(call["msg_id"], "EXPLICIT_MSG")

    def test_missing_context_returns_error(self):
        adapter = _StubAdapter({"success": True})
        cell = _make_cell({})
        cell._get_platform_context = lambda: {}
        self._patch_adapter(cell, adapter)

        path = self.make_file("r.txt", 10)
        result = cell.execute_with_context({"command": "send_file", "file_path": path})

        self.assertFalse(result["success"])
        self.assertIn("无法获取当前会话信息", result["error"])
        self.assertEqual(adapter.calls, [])

    def test_missing_file_path(self):
        adapter = _StubAdapter({"success": True})
        cell = _make_cell({"target_id": "G", "message_type": "group", "msg_id": "M"})
        self._patch_adapter(cell, adapter)

        result = cell.execute_with_context({"command": "send_file"})
        self.assertFalse(result["success"])
        self.assertIn("缺少 file_path", result["error"])

    def test_nonexistent_file(self):
        adapter = _StubAdapter({"success": True})
        cell = _make_cell({"target_id": "G", "message_type": "group", "msg_id": "M"})
        self._patch_adapter(cell, adapter)

        result = cell.execute_with_context({
            "command": "send_file",
            "file_path": os.path.join(self._tmpdir, "ghost.txt"),
        })
        self.assertFalse(result["success"])
        self.assertIn("文件不存在", result["error"])

    def test_directory_path_rejected(self):
        adapter = _StubAdapter({"success": True})
        cell = _make_cell({"target_id": "G", "message_type": "group", "msg_id": "M"})
        self._patch_adapter(cell, adapter)

        result = cell.execute_with_context({"command": "send_file", "file_path": self._tmpdir})
        self.assertFalse(result["success"])
        self.assertIn("不是普通文件", result["error"])

    def test_failure_propagates_reason(self):
        adapter = _StubAdapter({"success": False, "error": "文件上传失败: HTTP 400, 群被禁言"})
        cell = _make_cell({"target_id": "G", "message_type": "group", "msg_id": "M"})
        self._patch_adapter(cell, adapter)

        path = self.make_file("r.txt", 10)
        result = cell.execute_with_context({"command": "send_file", "file_path": path})

        self.assertFalse(result["success"])
        self.assertIn("群被禁言", result["error"])

    def test_failure_without_msg_id_adds_hint(self):
        adapter = _StubAdapter({"success": False, "error": "发送失败: HTTP 403"})
        cell = _make_cell({"target_id": "G", "message_type": "group", "msg_id": None})
        self._patch_adapter(cell, adapter)

        path = self.make_file("r.txt", 10)
        result = cell.execute_with_context({"command": "send_file", "file_path": path})

        self.assertFalse(result["success"])
        self.assertIn("msg_id", result["error"])

    def test_adapter_missing(self):
        cell = _make_cell({"target_id": "G", "message_type": "group", "msg_id": "M"})
        cell._get_adapter = lambda: None

        path = self.make_file("r.txt", 10)
        result = cell.execute_with_context({"command": "send_file", "file_path": path})
        self.assertFalse(result["success"])
        self.assertIn("QQ 服务未启动", result["error"])

    def test_unknown_command(self):
        cell = _make_cell({})
        result = cell.execute_with_context({"command": "nope"})
        self.assertFalse(result["success"])


class _FakeConfig:
    """只实现 get()/get_section() 的配置桩，用于展示开关判定"""

    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_section(self, name):
        return self._values.get(name, {})


class TestQQGroupDisplayDefaults(unittest.TestCase):
    """QQ 群聊默认关闭工具卡片/思考计划，私聊保持不变"""

    def _mgr(self):
        from app.channels.channel_manager import ChannelManager
        return ChannelManager()

    def test_group_defaults_off(self):
        mgr = self._mgr()
        with mock.patch("app.core.util.agent_config.get_config", return_value=_FakeConfig()):
            self.assertFalse(mgr._should_show_tool_cards("qq", "group"))
            self.assertFalse(mgr._should_show_thinking_plan("qq", "group"))

    def test_c2c_defaults_on(self):
        mgr = self._mgr()
        with mock.patch("app.core.util.agent_config.get_config", return_value=_FakeConfig()):
            self.assertTrue(mgr._should_show_tool_cards("qq", "c2c"))
            self.assertTrue(mgr._should_show_thinking_plan("qq", "c2c"))

    def test_other_platforms_unchanged(self):
        mgr = self._mgr()
        with mock.patch("app.core.util.agent_config.get_config", return_value=_FakeConfig()):
            self.assertTrue(mgr._should_show_tool_cards("telegram", "group"))
            self.assertTrue(mgr._should_show_thinking_plan("feishu", "group"))

    def test_group_flags_can_be_enabled_explicitly(self):
        mgr = self._mgr()
        cfg = _FakeConfig({
            "channels.qq.group.show_tool_cards": True,
            "channels.qq.group.show_thinking_plan": True,
        })
        with mock.patch("app.core.util.agent_config.get_config", return_value=cfg):
            self.assertTrue(mgr._should_show_tool_cards("qq", "group"))
            self.assertTrue(mgr._should_show_thinking_plan("qq", "group"))

    def test_inbound_group_mention_rendered(self):
        from app.channels.qq.qq_adapter import QQAdapter
        adapter = QQAdapter()
        msg = adapter._parse_message("GROUP_AT_MESSAGE_CREATE", {
            "id": "M1",
            "group_openid": "G1",
            "author": {"member_openid": "SENDER"},
            "content": "<@MEMBER_A> 你好 <@all>",
            "mentions": [{"member_openid": "MEMBER_A", "username": "小明"}],
        })
        self.assertEqual(msg.content, "@小明 你好 @全体成员")
        self.assertEqual(msg.user_id, "SENDER")

    def test_inbound_unknown_mention_kept(self):
        from app.channels.qq.qq_adapter import QQAdapter
        adapter = QQAdapter()
        msg = adapter._parse_message("GROUP_AT_MESSAGE_CREATE", {
            "id": "M1",
            "group_openid": "G1",
            "author": {"member_openid": "SENDER"},
            "content": "<@UNKNOWN> hi",
            "mentions": [],
        })
        self.assertEqual(msg.content, "<@UNKNOWN> hi")

    def test_inbound_c2c_content_untouched(self):
        from app.channels.qq.qq_adapter import QQAdapter
        adapter = QQAdapter()
        msg = adapter._parse_message("C2C_MESSAGE_CREATE", {
            "id": "M2",
            "author": {"user_openid": "U1"},
            "content": "<@X> hi",
            "mentions": [{"id": "X", "username": "甲"}],
        })
        self.assertEqual(msg.content, "<@X> hi")

    def test_render_group_mentions_empty_content(self):
        from app.channels.qq.qq_adapter import QQAdapter
        self.assertEqual(QQAdapter._render_group_mentions("", []), "")

    def test_group_flags_explicitly_disabled(self):
        mgr = self._mgr()
        cfg = _FakeConfig({
            "channels.qq.group.show_tool_cards": False,
            "channels.qq.group.show_thinking_plan": False,
        })
        with mock.patch("app.core.util.agent_config.get_config", return_value=cfg):
            self.assertFalse(mgr._should_show_tool_cards("qq", "group"))
            self.assertFalse(mgr._should_show_thinking_plan("qq", "group"))

    def test_gating_falls_back_when_config_errors(self):
        mgr = self._mgr()
        with mock.patch(
            "app.core.util.agent_config.get_config", side_effect=RuntimeError("boom")
        ):
            self.assertTrue(mgr._should_show_tool_cards("qq", "group"))
            self.assertTrue(mgr._should_show_thinking_plan("qq", "group"))


class TestGroupMentionRender(unittest.TestCase):
    """群消息 @ 标记还原的边界情况"""

    def test_plain_text_untouched(self):
        self.assertEqual(QQAdapter._render_group_mentions("@小明 你好", []), "@小明 你好")

    def test_bang_form(self):
        content = "<@!A> 你好"
        mentions = [{"member_openid": "A", "username": "小明"}]
        self.assertEqual(QQAdapter._render_group_mentions(content, mentions), "@小明 你好")

    def test_missing_username_falls_back_to_id(self):
        self.assertEqual(
            QQAdapter._render_group_mentions("<@A> hi", [{"member_openid": "A"}]),
            "@A hi",
        )

    def test_empty_id_skipped(self):
        mentions = [{"member_openid": "", "username": "空"}]
        self.assertEqual(QQAdapter._render_group_mentions("<@A> hi", mentions), "<@A> hi")

    def test_multiple_mentions_and_all(self):
        content = "<@all> <@A> <@B>"
        mentions = [
            {"member_openid": "A", "username": "甲"},
            {"member_openid": "B", "username": "乙"},
        ]
        self.assertEqual(
            QQAdapter._render_group_mentions(content, mentions),
            "@全体成员 @甲 @乙",
        )

    def test_mentions_none(self):
        self.assertEqual(QQAdapter._render_group_mentions("<@A>", None), "<@A>")


class TestDisplayConfigHelpers(unittest.TestCase):
    """配置写入路径与热更新判定"""

    def test_display_config_key(self):
        from app.server.routes.channels import _display_config_key
        self.assertEqual(
            _display_config_key("qq", None, "show_tool_cards"),
            "channels.qq.show_tool_cards",
        )
        self.assertEqual(
            _display_config_key("qq", "group", "show_tool_cards"),
            "channels.qq.group.show_tool_cards",
        )
        self.assertEqual(
            _display_config_key("qq", "GROUP", "show_thinking_plan"),
            "channels.qq.group.show_thinking_plan",
        )

    def test_only_display_flags_changed(self):
        from app.agent.di_config import _only_display_flags_changed
        old = {"qq": {"enabled": True, "group": {"show_tool_cards": False, "show_thinking_plan": False}}}
        new = {"qq": {"enabled": True, "group": {"show_tool_cards": True, "show_thinking_plan": True}}}
        self.assertTrue(_only_display_flags_changed(old, new))

        new_other = {"qq": {"enabled": True, "group": {"show_tool_cards": True, "retry": 3}}}
        self.assertFalse(_only_display_flags_changed(old, new_other))

        self.assertFalse(
            _only_display_flags_changed({"qq": {"app_secret": "a"}}, {"qq": {"app_secret": "b"}})
        )
        self.assertTrue(_only_display_flags_changed(None, None))
        self.assertTrue(_only_display_flags_changed({}, {"qq": {}}))

    def test_strip_display_flags_drops_empty_group(self):
        from app.agent.di_config import _strip_display_flags
        self.assertEqual(
            _strip_display_flags({"enabled": True, "group": {"show_tool_cards": False}}),
            {"enabled": True},
        )

    def test_display_scope_fields_group_merges_existing(self):
        from app.agent.tools.config_tool import ConfigTool
        cfg = _FakeConfig({"channels": {"qq": {"group": {"show_tool_cards": True, "retry": 1}}}})
        with mock.patch("app.core.util.agent_config.get_config", return_value=cfg):
            fields = ConfigTool()._display_scope_fields("qq", "show_thinking_plan", False, "group")
        self.assertEqual(
            fields, {"group": {"show_tool_cards": True, "retry": 1, "show_thinking_plan": False}}
        )

    def test_display_scope_fields_platform_level(self):
        from app.agent.tools.config_tool import ConfigTool
        self.assertEqual(
            ConfigTool()._display_scope_fields("telegram", "show_tool_cards", False, ""),
            {"show_tool_cards": False},
        )

    def test_config_tool_rejects_bad_args(self):
        from app.agent.tools.config_tool import ConfigTool
        tool = ConfigTool()
        self.assertFalse(tool._cmd_set_show_tool_cards("wechat", True)["success"])
        self.assertFalse(tool._cmd_set_show_tool_cards("qq", None)["success"])
        self.assertFalse(tool._cmd_set_show_thinking_plan("qq", None)["success"])


if __name__ == "__main__":
    unittest.main()
