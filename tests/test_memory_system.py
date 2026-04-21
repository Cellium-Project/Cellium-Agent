# -*- coding: utf-8 -*-
"""
三层记忆系统测试
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.loop.memory import MemoryManager
from app.agent.loop.prompt_context_builder import PromptContextBuilder
from app.agent.loop.session_manager import SessionManager
from app.agent.memory.archive_store import ArchiveStore
from app.agent.memory.fts5_searcher import FTS5MemorySearcher
from app.agent.memory.knowledge_extractor import KnowledgeExtractor
from app.agent.memory.three_layer import ThreeLayerMemory
from app.agent.tools.memory_tool import MemoryTool


class TestThreeLayerMemory(unittest.TestCase):
    """测试 ThreeLayerMemory 三层记忆"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.test_dir, "archive"), exist_ok=True)
        with open(os.path.join(self.test_dir, "personality.md"), "w", encoding="utf-8") as f:
            f.write("# Test Personality\n\nTest personality content")
        self.memory = ThreeLayerMemory(self.test_dir)

    def tearDown(self):
        if hasattr(self, "memory"):
            self.memory.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_personality_loading(self):
        prompt = self.memory.build_prompt("测试问题")
        self.assertIn("Test Personality", prompt)

    def test_persist_session_writes_archive_and_repository(self):
        source_id = self.memory.persist_session(
            "如何使用 Get-Process？",
            "使用 Get-Process | Where-Object { $_.CPU -gt 100 }",
            session_id="session-a",
        )

        record = self.memory.archive.get_by_id(source_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["session_id"], "session-a")
        self.assertTrue(record.get("snapshot_hash"))

        results = self.memory.search_memories("Get-Process", top_k=3)
        self.assertTrue(results)
        self.assertTrue(any("Get-Process" in item["content"] for item in results))

    def test_retrieve_with_context_returns_raw_archive(self):
        source_id = self.memory.persist_session(
            "网络错误怎么办？",
            "错误通常可以通过重置网卡解决。",
            session_id="session-b",
        )

        results = self.memory.retrieve_with_context("重置网卡")
        self.assertTrue(results)
        self.assertEqual(results[0]["raw"]["id"], source_id)

    def test_duplicate_snapshot_is_not_archived_twice(self):
        messages = [
            {"role": "user", "content": "如何查看进程？"},
            {"role": "assistant", "content": "使用 Get-Process"},
        ]

        first = self.memory.persist_session("如何查看进程？", "使用 Get-Process", session_id="dup-session", messages=messages)
        second = self.memory.persist_session("如何查看进程？", "使用 Get-Process", session_id="dup-session", messages=messages)

        self.assertEqual(first, second)
        records = self.memory.archive.get_by_session("dup-session")
        self.assertEqual(len(records), 1)


class TestFTS5MemorySearcher(unittest.TestCase):
    """测试 FTS5 搜索器"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.searcher = FTS5MemorySearcher(self.test_dir)

    def tearDown(self):
        self.searcher.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_insert_memory_returns_rowid_and_search_includes_rowid(self):
        rowid = self.searcher.insert_memory(
            title="进程管理",
            content="使用 Get-Process 查看进程",
            category="command",
            tags="process,powershell",
            source_file="manual:test-1",
        )
        self.assertIsInstance(rowid, int)

        results = self.searcher.search("Get-Process")
        self.assertTrue(results)
        self.assertEqual(results[0]["rowid"], rowid)

    def test_update_memory_by_rowid(self):
        rowid = self.searcher.insert_memory(
            title="旧标题",
            content="旧内容",
            category="general",
            source_file="manual:test-2",
        )
        updated = self.searcher.update_memory(
            rowid=rowid,
            source_file="manual:test-2",
            title="新标题",
            content="新内容",
            category="general",
            tags="updated",
        )
        self.assertTrue(updated)

        record = self.searcher.get_memory(rowid=rowid)
        self.assertEqual(record["title"], "新标题")
        self.assertEqual(record["content"], "新内容")

    def test_search_empty(self):
        results = self.searcher.search("nonexistent_query")
        self.assertEqual(len(results), 0)


class TestArchiveStore(unittest.TestCase):
    """测试归档存储"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.archive = ArchiveStore(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_append_conversation(self):
        source_id = self.archive.append("用户输入", "助手回复")
        record = self.archive.get_by_id(source_id)
        # 新格式：只保存 messages 数组，不再单独保存 user/assistant 字段
        self.assertTrue(record.get("snapshot_hash"))
        self.assertIn("messages", record)
        messages = record["messages"]
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "用户输入")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[1]["content"], "助手回复")

    def test_get_latest_by_session(self):
        self.archive.append("问题1", "回答1", session_id="session1")
        latest_id = self.archive.append("问题2", "回答2", session_id="session1")
        latest = self.archive.get_latest_by_session("session1")
        self.assertEqual(latest["id"], latest_id)


class TestKnowledgeExtractor(unittest.TestCase):
    """测试知识提取器"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.searcher = FTS5MemorySearcher(self.test_dir)
        self.extractor = KnowledgeExtractor(searcher=self.searcher)

    def tearDown(self):
        self.searcher.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_error_solution_as_issue_schema(self):
        items = self.extractor.extract("出现错误：连接失败", "解决方法是重试并检查网卡")
        self.assertTrue(items)
        self.assertEqual(items[0]["schema_type"], "issue")
        self.assertEqual(items[0]["category"], "troubleshooting")

    def test_is_noise_static(self):
        self.assertTrue(KnowledgeExtractor.is_noise("谢谢"))
        self.assertFalse(KnowledgeExtractor.is_noise("Get-Process 命令用于查看进程信息"))

    def test_extract_from_messages_includes_tool_errors(self):
        items = self.extractor.extract_from_messages(
            "为什么失败？",
            "我来检查",
            messages=[{"role": "tool", "content": '{"error": "permission denied"}'}],
        )
        self.assertTrue(any(item.get("schema_type") == "issue" for item in items))


class TestMemoryGovernance(unittest.TestCase):
    """测试统一记忆仓库的治理能力"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.test_dir, "archive"), exist_ok=True)
        with open(os.path.join(self.test_dir, "personality.md"), "w", encoding="utf-8") as f:
            f.write("# AI Assistant\n\nHelpful assistant")
        self.memory = ThreeLayerMemory(self.test_dir)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_structured_profile_upsert_merges_by_memory_key(self):
        first = self.memory.upsert_memory(
            title="语言偏好",
            content="用户偏好使用简体中文。",
            category="user_info",
            schema_type="profile",
            memory_key="profile:language",
            metadata={"field": "language"},
        )
        second = self.memory.upsert_memory(
            title="语言偏好",
            content="回答应尽量简洁。",
            category="preference",
            schema_type="profile",
            memory_key="profile:language",
            metadata={"field": "language"},
        )

        self.assertTrue(first["success"])
        self.assertEqual(second["action"], "merged")
        result = self.memory.list_memories(schema_type="profile")
        profiles = result.get("items", [])
        self.assertEqual(len(profiles), 1)
        self.assertIn("简体中文", profiles[0]["content"])
        self.assertIn("回答应尽量简洁", profiles[0]["content"])

    def test_hybrid_recall_handles_fuzzy_query(self):
        self.memory.upsert_memory(
            title="命令使用",
            content="使用 Get-Process 查看当前进程。",
            category="command",
            schema_type="general",
        )

        results = self.memory.search_memories("Get-Proces", top_k=3)
        self.assertTrue(results)
        self.assertIn("Get-Process", results[0]["content"])

    def test_sensitive_store_redacts_or_blocks(self):
        redacted = self.memory.upsert_memory(
            title="API Token",
            content="api_key = SECRET-1234567890",
            category="user_info",
            schema_type="profile",
            memory_key="profile:token",
        )
        self.assertTrue(redacted["success"])
        self.assertTrue(redacted["sensitive"])

        result = self.memory.list_memories(schema_type="profile", include_sensitive=True, limit=10)
        items = result.get("items", [])
        self.assertTrue(len(items) > 0, "应该返回至少一条敏感记忆")
        item = items[0]
        self.assertIn("[REDACTED]", item["content"])
        self.assertNotIn("SECRET-1234567890", item["content"])

        blocked = self.memory.upsert_memory(
            title="私钥",
            content="-----BEGIN PRIVATE KEY-----",
            category="user_info",
            schema_type="profile",
            memory_key="profile:key",
        )
        self.assertFalse(blocked["success"])

    def test_update_delete_and_forget_memory(self):
        created = self.memory.upsert_memory(
            title="项目命令",
            content="运行 npm test",
            category="project",
            schema_type="project",
            memory_key="project:command",
        )
        self.assertTrue(created["success"])

        updated = self.memory.update_memory(
            source=created["source"],
            content="运行 pnpm test",
        )
        self.assertTrue(updated["success"])
        self.assertTrue(self.memory.search_memories("pnpm test"))

        deleted = self.memory.delete_memory(source=created["source"])
        self.assertTrue(deleted["success"])
        self.assertFalse(self.memory.search_memories("pnpm test"))

        second = self.memory.upsert_memory(
            title="Issue 修复",
            content="问题: 构建失败 -> 解决: 清理缓存后重试",
            category="troubleshooting",
            schema_type="issue",
            memory_key="issue:build",
        )
        forgotten = self.memory.forget_memories(query="构建失败")
        self.assertTrue(forgotten["success"])
        self.assertFalse(self.memory.search_memories("构建失败"))
        self.assertTrue(second["success"])

    def test_merge_conflicts_merges_active_records(self):
        self.memory.upsert_memory(
            title="项目路径",
            content="仓库位于 C:/repo/app",
            category="project",
            schema_type="project",
            memory_key="project:path",
            merge_strategy="create_new",
        )
        self.memory.upsert_memory(
            title="项目路径",
            content="构建目录在 C:/repo/app/dist",
            category="project",
            schema_type="project",
            memory_key="project:path",
            merge_strategy="create_new",
        )

        merged = self.memory.merge_conflicts(memory_key="project:path", schema_type="project")
        self.assertTrue(merged["success"])
        result = self.memory.list_memories(schema_type="project")
        active = result.get("items", [])
        self.assertEqual(len(active), 1)
        self.assertIn("C:/repo/app", active[0]["content"])
        self.assertIn("C:/repo/app/dist", active[0]["content"])


class TestMemoryTool(unittest.TestCase):
    """测试 MemoryTool 命令接线"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.test_dir, "archive"), exist_ok=True)
        with open(os.path.join(self.test_dir, "personality.md"), "w", encoding="utf-8") as f:
            f.write("# AI Assistant\n\nHelpful assistant")
        self.memory = ThreeLayerMemory(self.test_dir)
        self.tool = MemoryTool(three_layer_memory=self.memory)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_store_update_search_delete_commands(self):
        stored = self.tool.execute(
            {
                "command": "store",
                "title": "项目约定",
                "content": "测试使用 pytest -q",
                "category": "project",
                "schema_type": "project",
                "memory_key": "project:test-command",
            }
        )
        self.assertTrue(stored["success"])

        updated = self.tool.execute(
            {
                "command": "update",
                "source": stored["source"],
                "content": "测试使用 python -m pytest -q",
            }
        )
        self.assertTrue(updated["success"])

        search = self.tool.execute({"command": "search", "query": "python -m pytest -q"})
        self.assertGreaterEqual(search["found"], 1)

        deleted = self.tool.execute({"command": "delete", "source": stored["source"]})
        self.assertTrue(deleted["success"])


class TestPromptContextBuilder(unittest.TestCase):
    """测试统一检索入口是否经过 ThreeLayerMemory"""

    def test_build_first_round_uses_three_layer_memory_api(self):
        prompt_builder = Mock()
        prompt_builder.build.return_value = "system prompt"
        prompt_builder.clear_dynamic = Mock()
        prompt_builder.inject = Mock()

        three_layer_memory = Mock()
        three_layer_memory.retrieve_context.return_value = [{"title": "记忆", "content": "历史内容"}]
        three_layer_memory.format_retrieved_context.return_value = "1. 记忆\n   历史内容"

        builder = PromptContextBuilder(prompt_builder=prompt_builder, three_layer_memory=three_layer_memory)
        messages = builder.build_first_round("请回忆之前的命令", session_messages=[{"role": "user", "content": "请回忆之前的命令"}])

        three_layer_memory.retrieve_context.assert_called_once_with("请回忆之前的命令", top_k=3)
        self.assertTrue(any("长期记忆检索结果" in (msg.get("content") or "") for msg in messages))


class TestSessionManagerPersistence(unittest.TestCase):
    """测试 SessionManager 关闭时走统一持久化入口"""

    def test_close_session_uses_persist_session(self):
        three_layer = Mock()
        three_layer.archive.get_by_session = Mock(return_value=[])
        three_layer.persist_session = Mock()

        manager = SessionManager(three_layer_memory=three_layer)
        info = manager.get_or_create("session-x")
        info.memory.add_user_message("用户消息")
        info.memory.add_assistant_message("助手消息")

        closed = manager.close_session("session-x")

        self.assertTrue(closed)
        three_layer.persist_session.assert_called_once()
        kwargs = three_layer.persist_session.call_args.kwargs
        self.assertEqual(kwargs["session_id"], "session-x")
        self.assertEqual(kwargs["user_input"], "用户消息")
        self.assertEqual(kwargs["response"], "助手消息")


class TestMemoryManager(unittest.TestCase):
    """测试 MemoryManager 短期记忆"""

    def setUp(self):
        self.memory = MemoryManager()

    def test_add_tool_call_and_result(self):
        tool_call_id = self.memory.add_tool_call("shell", {"command": "Get-Process"})
        self.memory.add_tool_result(tool_call_id, {"output": "result content"})
        messages = self.memory.get_messages()
        self.assertEqual(messages[0]["tool_calls"][0]["id"], tool_call_id)
        self.assertEqual(messages[1]["role"], "tool")

    def test_max_history_limit(self):
        memory = MemoryManager(max_history=3)
        for i in range(5):
            memory.add_user_message(f"message {i}")
        messages = memory.get_messages()
        self.assertLessEqual(len(messages), 3)


class TestVectorEmbedding(unittest.TestCase):
    """测试 96 维向量嵌入模型"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.test_dir, "archive"), exist_ok=True)
        with open(os.path.join(self.test_dir, "personality.md"), "w", encoding="utf-8") as f:
            f.write("# AI Assistant\n\nHelpful assistant")
        self.memory = ThreeLayerMemory(self.test_dir)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_embed_text_returns_96_dimensions(self):
        vec = self.memory.repository._embed_text("Python 编程语言")
        self.assertEqual(len(vec), 96)

    def test_embed_text_normalized(self):
        vec = self.memory.repository._embed_text("测试文本")
        norm = sum(v * v for v in vec) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_cosine_similarity_identical_texts(self):
        vec1 = self.memory.repository._embed_text("Python 教程")
        vec2 = self.memory.repository._embed_text("Python 教程")
        sim = self.memory.repository._cosine_similarity(vec1, vec2)
        self.assertGreater(sim, 0.99)

    def test_cosine_similarity_similar_texts_higher_than_dissimilar(self):
        vec_python = self.memory.repository._embed_text("Python 编程")
        vec_java = self.memory.repository._embed_text("Java 编程")
        vec_unrelated = self.memory.repository._embed_text("天气很好")

        sim_similar = self.memory.repository._cosine_similarity(vec_python, vec_java)
        sim_unrelated = self.memory.repository._cosine_similarity(vec_python, vec_unrelated)

        self.assertGreater(sim_similar, sim_unrelated)

    def test_embedding_search_returns_results_with_score(self):
        self.memory.upsert_memory(
            title="进程管理",
            content="使用 Get-Process 查看进程信息",
            category="command",
            schema_type="general",
        )

        results = self.memory.repository._embedding_search(
            query="如何查看进程",
            top_k=3,
            category=None,
            schema_type=None,
            include_sensitive=False,
        )

        self.assertTrue(len(results) > 0)
        self.assertIn("embedding_score", results[0])

    def test_hybrid_search_combines_fts5_and_vector(self):
        self.memory.upsert_memory(
            title="Python 教程",
            content="Python 是一种高级编程语言，适合初学者",
            category="programming",
            schema_type="general",
        )

        results = self.memory.search_memories("Python 编程", top_k=5)

        self.assertTrue(len(results) > 0)
        fts5_rank = None
        vector_score = None
        for i, r in enumerate(results):
            if "Python" in r.get("content", ""):
                if fts5_rank is None:
                    fts5_rank = i
                if "score" in r:
                    vector_score = r.get("score")
                break

        self.assertIsNotNone(fts5_rank)
        self.assertIsNotNone(vector_score)


if __name__ == "__main__":
    unittest.main()
