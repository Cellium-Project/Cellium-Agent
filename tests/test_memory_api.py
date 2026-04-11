# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.agent.memory.three_layer import ThreeLayerMemory
from app.core.di.container import get_container
from app.server.web_server import create_app


class TestMemoryApi(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.test_dir, "archive"), exist_ok=True)
        with open(os.path.join(self.test_dir, "personality.md"), "w", encoding="utf-8") as f:
            f.write("# Test Assistant\n")

        self.memory = ThreeLayerMemory(self.test_dir)
        self.container = get_container()
        self.container.clear()
        self.container.register(ThreeLayerMemory, self.memory, singleton=True)
        self.client = TestClient(create_app())

    def tearDown(self):
        self.client.close()
        self.container.clear()
        self.memory.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_memory_crud_and_search_endpoints(self):
        created = self.client.post(
            "/api/memories",
            json={
                "title": "项目测试命令",
                "content": "运行 python -m pytest -q",
                "category": "project",
                "schema_type": "project",
                "memory_key": "project:test-command",
                "metadata": {"project_id": "cellium-agent"},
            },
        )
        self.assertEqual(created.status_code, 200)
        created_body = created.json()
        memory_id = created_body["id"]

        listed = self.client.get("/api/memories?schema_type=project&limit=20")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)

        detail = self.client.get(f"/api/memories/{memory_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["memory_key"], "project:test-command")

        updated = self.client.put(
            f"/api/memories/{memory_id}",
            json={
                "content": "运行 pytest -q",
                "tags": "pytest,project",
                "metadata": {"project_id": "cellium-agent", "command_type": "test"},
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertIn("pytest -q", updated.json()["memory"]["content"])

        searched = self.client.get("/api/memories?query=pytest%20-q&schema_type=project&limit=10")
        self.assertEqual(searched.status_code, 200)
        self.assertGreaterEqual(searched.json()["total"], 1)

        deleted = self.client.delete(f"/api/memories/{memory_id}")
        self.assertEqual(deleted.status_code, 200)

        listed_deleted = self.client.get("/api/memories?include_deleted=true&schema_type=project&limit=20")
        self.assertEqual(listed_deleted.status_code, 200)
        self.assertEqual(listed_deleted.json()["items"][0]["status"], "deleted")

    def test_memory_forget_merge_and_summary_endpoints(self):
        first = self.client.post(
            "/api/memories",
            json={
                "title": "项目路径",
                "content": "仓库位于 C:/repo/app",
                "category": "project",
                "schema_type": "project",
                "memory_key": "project:path",
                "merge_strategy": "create_new",
            },
        )
        second = self.client.post(
            "/api/memories",
            json={
                "title": "项目路径",
                "content": "构建目录在 C:/repo/app/dist",
                "category": "project",
                "schema_type": "project",
                "memory_key": "project:path",
                "merge_strategy": "create_new",
            },
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        merged = self.client.post(
            "/api/memories/actions/merge",
            json={"memory_key": "project:path", "schema_type": "project"},
        )
        self.assertEqual(merged.status_code, 200)
        self.assertEqual(merged.json()["merged_records"], 1)

        issue = self.client.post(
            "/api/memories",
            json={
                "title": "构建失败修复",
                "content": "问题: 构建失败 -> 解决: 清理缓存后重试",
                "category": "troubleshooting",
                "schema_type": "issue",
                "memory_key": "issue:build",
            },
        )
        self.assertEqual(issue.status_code, 200)

        forgotten = self.client.post(
            "/api/memories/actions/forget",
            json={"query": "构建失败", "all_matches": False},
        )
        self.assertEqual(forgotten.status_code, 200)
        self.assertEqual(len(forgotten.json()["forgotten"]), 1)

        summary = self.client.get("/api/memories/summary")
        self.assertEqual(summary.status_code, 200)
        body = summary.json()
        self.assertEqual(body["active_records"], 1)
        self.assertEqual(body["forgotten_records"], 1)
        self.assertEqual(body["merged_records"], 1)


if __name__ == "__main__":
    unittest.main()
