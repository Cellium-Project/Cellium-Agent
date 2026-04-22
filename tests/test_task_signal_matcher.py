# -*- coding: utf-8 -*-
import os
import sys
import unittest
import tempfile
import shutil
from unittest.mock import Mock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.hard_constraints import (
    TaskSignalMatcher,
    HardConstraintTemplates,
    HardConstraint,
)


class TestTaskSignalMatcher(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_match_code_debug_builtin(self):
        result = TaskSignalMatcher.match("帮我 debug 这个错误")
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "code_debug")
        self.assertIn("Debug task", result["gene_template"])
        self.assertIn("web_search", result["forbidden_tools"])
        self.assertIn("file", result["preferred_tools"])

    def test_match_file_operation_builtin(self):
        result = TaskSignalMatcher.match("读取文件内容")
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "file_operation")
        self.assertIn("File operation", result["gene_template"])

    def test_match_web_search_builtin(self):
        result = TaskSignalMatcher.match("搜索相关信息")
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "web_search")
        self.assertIn("Search task", result["gene_template"])

    def test_match_no_match(self):
        result = TaskSignalMatcher.match("随便聊聊")
        self.assertIsNone(result)

    def test_match_english_signals(self):
        result = TaskSignalMatcher.match("fix this bug")
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "code_debug")

    def test_initialize_with_repository(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = []

        TaskSignalMatcher.initialize(mock_repo)

        self.assertEqual(TaskSignalMatcher._repository, mock_repo)
        mock_repo.search_memories.assert_called_once()

    def test_load_from_repository(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = [
            {
                "content": "[HARD CONSTRAINTS]\nTest Gene",
                "metadata": {
                    "task_type": "test_task",
                    "signals": ["test", "demo"],
                    "forbidden_tools": ["tool1"],
                    "preferred_tools": ["tool2"],
                }
            }
        ]

        TaskSignalMatcher.initialize(mock_repo)
        result = TaskSignalMatcher.match("this is a test")

        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "test_task")
        self.assertIn("Test Gene", result["gene_template"])

    def test_cache_priority_over_builtin(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = [
            {
                "content": "[HARD CONSTRAINTS]\nCached Debug Gene",
                "metadata": {
                    "task_type": "code_debug",
                    "signals": ["debug", "error"],
                    "forbidden_tools": [],
                    "preferred_tools": [],
                }
            }
        ]

        TaskSignalMatcher.initialize(mock_repo)
        result = TaskSignalMatcher.match("debug this")

        self.assertIn("Cached Debug Gene", result["gene_template"])

    def test_save_to_repository_on_builtin_match(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = []

        TaskSignalMatcher.initialize(mock_repo)
        TaskSignalMatcher.match("debug error")

        mock_repo.upsert_memory.assert_called()
        call_args = mock_repo.upsert_memory.call_args
        self.assertEqual(call_args.kwargs["schema_type"], "control_gene")
        self.assertEqual(call_args.kwargs["category"], "task_strategy")
        self.assertTrue(call_args.kwargs["memory_key"].startswith("gene:"))


class TestHardConstraintTemplatesRenderForTask(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_render_for_task_with_match(self):
        constraint = HardConstraintTemplates.render_for_task(
            "帮我 debug 这个错误",
            "redirect"
        )

        self.assertIsInstance(constraint, HardConstraint)
        self.assertIn("Debug task", constraint.hard_constraints)
        self.assertEqual(constraint.trigger_reason, "Task signal matched: code_debug")
        self.assertEqual(constraint.max_tokens, 300)
        self.assertIn("web_search", constraint.forbidden)
        self.assertIn("file", constraint.preferred)

    def test_render_for_task_no_match(self):
        constraint = HardConstraintTemplates.render_for_task(
            "随便聊聊",
            "redirect"
        )

        self.assertIsInstance(constraint, HardConstraint)
        self.assertIn("REDIRECT", constraint.hard_constraints)
        self.assertEqual(constraint.trigger_reason, "Generic rule triggered")
        self.assertEqual(constraint.max_tokens, 80)

    def test_render_for_task_retry_action(self):
        constraint = HardConstraintTemplates.render_for_task(
            "fix this bug",
            "retry"
        )

        self.assertIsInstance(constraint, HardConstraint)
        self.assertIn("Debug task", constraint.hard_constraints)

    def test_render_for_task_compress_action(self):
        constraint = HardConstraintTemplates.render_for_task(
            "帮我 debug 这个错误",
            "compress"
        )

        self.assertIsInstance(constraint, HardConstraint)
        self.assertIn("COMPRESS", constraint.hard_constraints)
        self.assertEqual(constraint.trigger_reason, "Generic rule triggered")


class TestIntegration(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_full_flow_builtin_only(self):
        result = TaskSignalMatcher.match("debug this error")
        self.assertIsNotNone(result)

        constraint = HardConstraintTemplates.render_for_task(
            "debug this error",
            "redirect"
        )
        self.assertIn("Debug task", constraint.hard_constraints)
        self.assertEqual(constraint.trigger_reason, "Task signal matched: code_debug")

    def test_full_flow_with_repository(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = [
            {
                "content": "[HARD CONSTRAINTS]\nCustom Gene from DB",
                "metadata": {
                    "task_type": "custom_task",
                    "signals": ["custom"],
                    "forbidden_tools": ["forbidden"],
                    "preferred_tools": ["preferred"],
                }
            }
        ]

        TaskSignalMatcher.initialize(mock_repo)

        result = TaskSignalMatcher.match("custom operation")
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "custom_task")

        constraint = HardConstraintTemplates.render_for_task(
            "custom operation",
            "redirect"
        )
        self.assertIn("Custom Gene from DB", constraint.hard_constraints)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_empty_input(self):
        result = TaskSignalMatcher.match("")
        self.assertIsNone(result)

    def test_none_input(self):
        result = TaskSignalMatcher.match(None)
        self.assertIsNone(result)

    def test_case_insensitive(self):
        result1 = TaskSignalMatcher.match("DEBUG")
        result2 = TaskSignalMatcher.match("debug")
        result3 = TaskSignalMatcher.match("Debug")
        self.assertIsNotNone(result1)
        self.assertIsNotNone(result2)
        self.assertIsNotNone(result3)
        self.assertEqual(result1["task_type"], result2["task_type"])

    def test_partial_match(self):
        result = TaskSignalMatcher.match("我需要 debugging 这个")
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "code_debug")

    def test_multiple_signals(self):
        result = TaskSignalMatcher.match("debug and fix the error")
        self.assertIsNotNone(result)

    def test_repository_error_handling(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.side_effect = Exception("DB Error")

        TaskSignalMatcher.initialize(mock_repo)
        result = TaskSignalMatcher.match("debug error")

        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "code_debug")

    def test_repository_upsert_error(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = []
        mock_repo.upsert_memory.side_effect = Exception("Upsert Error")

        TaskSignalMatcher.initialize(mock_repo)
        result = TaskSignalMatcher.match("debug error")

        self.assertIsNotNone(result)

    def test_cache_refresh(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = [
            {
                "content": "Gene v1",
                "metadata": {
                    "task_type": "test",
                    "signals": ["test"],
                    "forbidden_tools": [],
                    "preferred_tools": [],
                }
            }
        ]

        TaskSignalMatcher.initialize(mock_repo)
        result1 = TaskSignalMatcher.match("test")
        self.assertIn("Gene v1", result1["gene_template"])

        mock_repo.search_memories.return_value = [
            {
                "content": "Gene v2",
                "metadata": {
                    "task_type": "test",
                    "signals": ["test"],
                    "forbidden_tools": [],
                    "preferred_tools": [],
                }
            }
        ]

        TaskSignalMatcher._cache_loaded = False
        result2 = TaskSignalMatcher.match("test")
        self.assertIn("Gene v2", result2["gene_template"])


class TestHardConstraintStructure(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_constraint_structure(self):
        constraint = HardConstraintTemplates.render_for_task(
            "debug error",
            "redirect"
        )

        self.assertIsInstance(constraint, HardConstraint)
        self.assertIsInstance(constraint.hard_constraints, str)
        self.assertIsInstance(constraint.failure_conditions, str)
        self.assertIsInstance(constraint.trigger_reason, str)
        self.assertIsInstance(constraint.forbidden, list)
        self.assertIsInstance(constraint.preferred, list)
        self.assertIsInstance(constraint.max_tokens, int)

    def test_gene_format(self):
        constraint = HardConstraintTemplates.render_for_task(
            "debug error",
            "redirect"
        )

        self.assertIn("[HARD CONSTRAINTS]", constraint.hard_constraints)
        self.assertIn("[CONTROL ACTION]", constraint.hard_constraints)
        self.assertIn("MUST:", constraint.hard_constraints)
        self.assertIn("MUST NOT:", constraint.hard_constraints)

    def test_action_specific_behavior(self):
        redirect_constraint = HardConstraintTemplates.render_for_task(
            "debug error", "redirect"
        )
        retry_constraint = HardConstraintTemplates.render_for_task(
            "debug error", "retry"
        )
        compress_constraint = HardConstraintTemplates.render_for_task(
            "debug error", "compress"
        )
        terminate_constraint = HardConstraintTemplates.render_for_task(
            "debug error", "terminate"
        )

        self.assertIn("Debug task", redirect_constraint.hard_constraints)
        self.assertIn("Debug task", retry_constraint.hard_constraints)
        self.assertNotIn("Debug task", compress_constraint.hard_constraints)
        self.assertNotIn("Debug task", terminate_constraint.hard_constraints)


if __name__ == "__main__":
    unittest.main()
