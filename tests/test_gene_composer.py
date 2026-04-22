# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import Mock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.hard_constraints import (
    TaskSignalMatcher,
    GeneComposer,
    HardConstraintTemplates,
)


class TestGeneComposer(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_match_multiple_single(self):
        result = GeneComposer.match_multiple("debug this error")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["task_type"], "code_debug")

    def test_match_multiple_multiple(self):
        result = GeneComposer.match_multiple("debug and write to file")
        task_types = [r["task_type"] for r in result]
        self.assertIn("code_debug", task_types)
        self.assertIn("file_operation", task_types)

    def test_match_multiple_priority_order(self):
        result = GeneComposer.match_multiple("debug and write to file")
        self.assertEqual(result[0]["task_type"], "code_debug")
        self.assertEqual(result[1]["task_type"], "file_operation")

    def test_match_multiple_empty(self):
        result = GeneComposer.match_multiple("")
        self.assertEqual(result, [])

    def test_compose_single(self):
        matches = GeneComposer.match_multiple("debug error")
        composed = GeneComposer.compose(matches)
        self.assertEqual(composed["task_type"], "code_debug")
        self.assertNotIn("combined:", composed["task_type"])

    def test_compose_multiple(self):
        matches = GeneComposer.match_multiple("debug and write file")
        composed = GeneComposer.compose(matches)
        self.assertIn("combined:", composed["task_type"])
        self.assertIn("code_debug", composed["component_tasks"])
        self.assertIn("file_operation", composed["component_tasks"])

    def test_compose_structure(self):
        matches = GeneComposer.match_multiple("debug and write file")
        composed = GeneComposer.compose(matches)
        template = composed["gene_template"]
        self.assertIn("[HARD CONSTRAINTS]", template)
        self.assertIn("Multi-task:", template)
        self.assertIn("STEP 1 [code_debug]:", template)
        self.assertIn("STEP 2 [file_operation]:", template)
        self.assertIn("[AVOID]", template)

    def test_compose_forbidden_tools(self):
        matches = GeneComposer.match_multiple("debug and search")
        composed = GeneComposer.compose(matches)
        self.assertIn("web_search", composed["forbidden_tools"])

    def test_compose_preferred_tools(self):
        matches = GeneComposer.match_multiple("debug and write file")
        composed = GeneComposer.compose(matches)
        self.assertIn("file", composed["preferred_tools"])

    def test_compose_none(self):
        composed = GeneComposer.compose([])
        self.assertIsNone(composed)


class TestGeneComposerIntegration(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_render_single_task(self):
        constraint = HardConstraintTemplates.render_for_task(
            "debug error", "redirect"
        )
        self.assertIn("code_debug", constraint.trigger_reason)
        self.assertEqual(constraint.max_tokens, 300)

    def test_render_combined_task(self):
        constraint = HardConstraintTemplates.render_for_task(
            "debug and write file", "redirect"
        )
        self.assertIn("Combined tasks", constraint.trigger_reason)
        self.assertIn("code_debug", constraint.trigger_reason)
        self.assertIn("file_operation", constraint.trigger_reason)
        self.assertEqual(constraint.max_tokens, 500)
        self.assertIn("Multi-task", constraint.hard_constraints)

    def test_render_no_match(self):
        constraint = HardConstraintTemplates.render_for_task(
            "随便聊聊", "redirect"
        )
        self.assertEqual(constraint.trigger_reason, "Generic rule triggered")


if __name__ == "__main__":
    unittest.main()
