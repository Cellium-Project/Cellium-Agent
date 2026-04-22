# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.hard_constraints import (
    TaskSignalMatcher,
    GeneEvolution,
    HardConstraintTemplates,
)
from app.agent.control.loop_state import LoopState


class TestGeneEvolution(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_extract_avoid_cue_from_error(self):
        state = Mock(spec=LoopState)
        state.last_error = "File not found"
        state.features = None
        state.decision_trace = []

        cue = GeneEvolution.extract_avoid_cue(state, 0.3)

        self.assertIsNotNone(cue)
        self.assertIn("File not found", cue)
        self.assertIn("DON'T", cue)

    def test_extract_avoid_cue_from_stuck(self):
        state = Mock(spec=LoopState)
        state.last_error = None
        state.features = Mock()
        state.features.stuck_iterations = 4
        state.features.repetition_score = 0.0
        state.decision_trace = []

        cue = GeneEvolution.extract_avoid_cue(state, 0.2)

        self.assertIsNotNone(cue)
        self.assertIn("stuck", cue.lower())
        self.assertIn("4", cue)

    def test_extract_avoid_cue_from_repetition(self):
        state = Mock(spec=LoopState)
        state.last_error = None
        state.features = Mock()
        state.features.stuck_iterations = 0
        state.features.repetition_score = 0.7
        state.decision_trace = []

        cue = GeneEvolution.extract_avoid_cue(state, 0.1)

        self.assertIsNotNone(cue)
        self.assertIn("Repeat", cue)

    def test_extract_avoid_cue_success_no_cue(self):
        state = Mock(spec=LoopState)
        state.last_error = "Some error"

        cue = GeneEvolution.extract_avoid_cue(state, 0.8)

        self.assertIsNone(cue)

    def test_update_gene_from_failure_adds_avoid_cue(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = [
            {
                "content": "[HARD CONSTRAINTS]\nTest\n[CONTROL ACTION]\nMUST: do",
                "metadata": {
                    "task_type": "test_task",
                    "usage_count": 5,
                    "failure_count": 2,
                    "version": 2,
                },
                "title": "Gene: test_task",
                "memory_key": "gene:test_task",
            }
        ]
        TaskSignalMatcher._repository = mock_repo

        state = Mock(spec=LoopState)
        state.elapsed_ms = 5000
        GeneEvolution.update_gene_from_failure("test_task", "DON'T: Test failure", state, 0.3)

        mock_repo.upsert_memory.assert_called_once()
        call_args = mock_repo.upsert_memory.call_args
        self.assertIn("DON'T: Test failure", call_args.kwargs["content"])
        self.assertIn("[AVOID]", call_args.kwargs["content"])
        self.assertEqual(call_args.kwargs["metadata"]["evolved"], True)
        self.assertEqual(call_args.kwargs["metadata"]["failure_count"], 3)
        self.assertEqual(call_args.kwargs["metadata"]["version"], 3)
        self.assertIn("evolution_history", call_args.kwargs["metadata"])
        self.assertIn("recent_results", call_args.kwargs["metadata"])
        self.assertEqual(call_args.kwargs["metadata"]["consecutive_failure"], 1)

    def test_update_gene_duplicate_avoid_cue(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = [
            {
                "content": "[HARD CONSTRAINTS]\nTest\n[AVOID]\n- DON'T: Test failure",
                "metadata": {
                    "task_type": "test_task",
                    "usage_count": 5,
                },
                "title": "Gene: test_task",
                "memory_key": "gene:test_task",
            }
        ]
        TaskSignalMatcher._repository = mock_repo

        state = Mock(spec=LoopState)
        GeneEvolution.update_gene_from_failure("test_task", "DON'T: Test failure", state)

        mock_repo.upsert_memory.assert_not_called()

    def test_record_success_updates_metadata(self):
        mock_repo = MagicMock()
        mock_repo.search_memories.return_value = [
            {
                "content": "[HARD CONSTRAINTS]\nTest",
                "metadata": {
                    "task_type": "test_task",
                    "usage_count": 5,
                    "success_count": 3,
                },
                "title": "Gene: test_task",
                "memory_key": "gene:test_task",
            }
        ]
        TaskSignalMatcher._repository = mock_repo

        GeneEvolution.record_success("test_task", 0.85, 3000)

        mock_repo.upsert_memory.assert_called_once()
        call_args = mock_repo.upsert_memory.call_args
        self.assertEqual(call_args.kwargs["metadata"]["usage_count"], 6)
        self.assertEqual(call_args.kwargs["metadata"]["success_count"], 4)
        self.assertEqual(call_args.kwargs["metadata"]["success_rate"], 4/6)
        self.assertIn("recent_results", call_args.kwargs["metadata"])
        self.assertEqual(call_args.kwargs["metadata"]["consecutive_success"], 1)

    def test_update_gene_no_repository(self):
        TaskSignalMatcher._repository = None
        state = Mock(spec=LoopState)

        result = GeneEvolution.update_gene_from_failure("test", "cue", state)
        self.assertIsNone(result)

    def test_record_success_no_task_type(self):
        mock_repo = MagicMock()
        TaskSignalMatcher._repository = mock_repo

        GeneEvolution.record_success("")
        mock_repo.upsert_memory.assert_not_called()


class TestFeedbackEvaluatorWithGeneEvolution(unittest.TestCase):
    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    @patch("app.agent.control.hard_constraints.GeneEvolution")
    def test_evaluate_with_gene_evolution_failure(self, mock_gene_evolution):
        from app.agent.control.feedback_evaluator import FeedbackEvaluator

        mock_gene_evolution.extract_avoid_cue.return_value = "DON'T: Test"

        evaluator = FeedbackEvaluator()
        state = Mock(spec=LoopState)
        state.last_tool_result = {"error": "Failed"}
        state.iteration = 5
        state.max_iterations = 10
        state.tokens_used = 1000
        state.token_budget = 10000
        state.features = None
        state.elapsed_ms = 5000

        reward = evaluator.evaluate_with_gene_evolution(state, "test_task")

        self.assertLess(reward, 0.5)
        mock_gene_evolution.extract_avoid_cue.assert_called_once()
        mock_gene_evolution.update_gene_from_failure.assert_called_once()

    @patch("app.agent.control.hard_constraints.GeneEvolution")
    def test_evaluate_with_gene_evolution_success(self, mock_gene_evolution):
        from app.agent.control.feedback_evaluator import FeedbackEvaluator

        evaluator = FeedbackEvaluator()
        state = Mock(spec=LoopState)
        state.last_tool_result = {"success": True}
        state.iteration = 3
        state.max_iterations = 10
        state.tokens_used = 1000
        state.token_budget = 10000
        state.features = None
        state.elapsed_ms = 3000

        reward = evaluator.evaluate_with_gene_evolution(state, "test_task")

        self.assertGreater(reward, 0.5)
        mock_gene_evolution.record_success.assert_called_once()


if __name__ == "__main__":
    unittest.main()
