# -*- coding: utf-8 -*-
"""
Gene API 集成测试
"""
import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.agent.memory.three_layer import ThreeLayerMemory
from app.agent.control.hard_constraints import TaskSignalMatcher


class TestGeneAPI(unittest.TestCase):
    def setUp(self):
        # 创建临时目录
        self.temp_dir = tempfile.mkdtemp()
        
        # 创建记忆服务
        self.memory = ThreeLayerMemory(memory_dir=self.temp_dir)
        TaskSignalMatcher.initialize(self.memory)
        
        # 延迟导入并创建应用
        from app.server.routes.gene import router as gene_router
        
        self.app = FastAPI()
        self.app.include_router(gene_router)
        
        # Mock DI 容器
        self.mock_container = MagicMock()
        self.mock_container.has.return_value = True
        self.mock_container.resolve.return_value = self.memory
        
        # Patch get_container
        self.container_patcher = patch('app.server.routes.gene.get_container', return_value=self.mock_container)
        self.container_patcher.start()
        
        self.client = TestClient(self.app)
    
    def tearDown(self):
        self.container_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False
    
    def test_list_genes(self):
        """测试列出所有 Genes"""
        response = self.client.get("/api/genes")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertIn("items", data)
        self.assertIn("total", data)
        # 应该有 3 个预置 Gene
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 3)
    
    def test_get_gene_stats(self):
        """测试获取 Gene 统计信息"""
        response = self.client.get("/api/genes/stats")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertIn("total_genes", data)
        self.assertIn("total_usage", data)
        self.assertIn("avg_success_rate", data)
        self.assertIn("evolved_genes", data)
        self.assertEqual(data["total_genes"], 3)
    
    def test_get_gene_detail(self):
        """测试获取单个 Gene 详情"""
        response = self.client.get("/api/genes/code_debug")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data["task_type"], "code_debug")
        self.assertEqual(data["version"], 1)
        self.assertIn("content", data)
        self.assertIn("[HARD CONSTRAINTS]", data["content"])
    
    def test_get_gene_detail(self):
        """测试获取单个 Gene 详情 - 验证返回正确的 Gene"""
        response = self.client.get("/api/genes/code_debug")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data["task_type"], "code_debug")
        self.assertEqual(data["id"], "gene:code_debug")
    
    def test_update_gene(self):
        """测试更新 Gene"""
        update_data = {
            "title": "Updated Gene Title",
            "content": "[HARD CONSTRAINTS]\nUpdated content"
        }
        response = self.client.put("/api/genes/code_debug", json=update_data)
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data["title"], "Updated Gene Title")
        self.assertEqual(data["content"], "[HARD CONSTRAINTS]\nUpdated content")
        self.assertEqual(data["version"], 2)  # 版本应该递增
        self.assertTrue(data["evolution_history"])
    
    def test_evolve_gene(self):
        """测试进化 Gene"""
        # 先添加一些失败记录
        gene = self.memory.search_memories(
            query="gene:code_debug",
            schema_type="control_gene",
            top_k=1
        )[0]
        
        # 更新 metadata 添加失败记录
        metadata = gene.get("metadata", {})
        metadata["consecutive_failure"] = 3
        metadata["failure_count"] = 5
        metadata["usage_count"] = 10
        metadata["recent_results"] = [
            {"success": False, "reward": 0.2, "at": "2024-01-01T00:00:00"}
            for _ in range(5)
        ]
        
        self.memory.upsert_memory(
            title=gene.get("title"),
            content=gene.get("content"),
            schema_type="control_gene",
            category="task_strategy",
            memory_key="gene:code_debug",
            metadata=metadata
        )
        
        response = self.client.post("/api/genes/code_debug/evolve")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertTrue(data["success"])
        self.assertGreater(data["new_version"], 1)
        self.assertGreaterEqual(data["avoid_cues_added"], 0)
    
    def test_delete_gene(self):
        """测试删除 Gene"""
        # 先创建一个测试 Gene（使用唯一的 ID）
        unique_id = "test_delete_abc123"
        self.memory.upsert_memory(
            title=f"Gene: {unique_id}",
            content="[HARD CONSTRAINTS]\nTest",
            schema_type="control_gene",
            category="task_strategy",
            memory_key=f"gene:{unique_id}",
            metadata={"task_type": unique_id}
        )
        
        # 验证 Gene 存在
        response = self.client.get(f"/api/genes/{unique_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], f"gene:{unique_id}")
        
        # 删除 Gene
        response = self.client.delete(f"/api/genes/{unique_id}")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deleted_id"], unique_id)


class TestGeneAPIEdgeCases(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.memory = ThreeLayerMemory(memory_dir=self.temp_dir)
        TaskSignalMatcher.initialize(self.memory)
        
        from app.server.routes.gene import router as gene_router
        
        self.app = FastAPI()
        self.app.include_router(gene_router)
        
        self.mock_container = MagicMock()
        self.mock_container.has.return_value = True
        self.mock_container.resolve.return_value = self.memory
        
        self.container_patcher = patch('app.server.routes.gene.get_container', return_value=self.mock_container)
        self.container_patcher.start()
        
        self.client = TestClient(self.app)
    
    def tearDown(self):
        self.container_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False
    
    def test_list_genes_pagination(self):
        """测试分页"""
        response = self.client.get("/api/genes?limit=2&offset=0")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["total"], 3)
    
    def test_update_gene_partial(self):
        """测试部分更新（只更新 title）"""
        update_data = {"title": "New Title Only"}
        response = self.client.put("/api/genes/code_debug", json=update_data)
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data["title"], "New Title Only")
        # content 应该保持不变
        self.assertIn("[HARD CONSTRAINTS]", data["content"])


if __name__ == "__main__":
    unittest.main()
