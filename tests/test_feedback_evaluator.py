# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.feedback_evaluator import FeedbackEvaluator
from app.agent.control.loop_state import LoopState


class TestFeedbackEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = FeedbackEvaluator()
        self.state = LoopState(session_id="test")

    def test_evaluate_success_no_tool_result(self):
        self.state.last_tool_result = None
        reward = self.evaluator.evaluate(self.state)
        self.assertGreaterEqual(reward, 0.7)
        self.assertLessEqual(reward, 1.0)

    def test_evaluate_success_dict_result(self):
        self.state.last_tool_result = {"success": True, "data": "test"}
        reward = self.evaluator.evaluate(self.state)
        self.assertGreaterEqual(reward, 0.7)

    def test_evaluate_failure_error_result(self):
        self.state.last_tool_result = {"error": "Something went wrong"}
        reward = self.evaluator.evaluate(self.state)
        self.assertLessEqual(reward, 0.3)

    def test_evaluate_failure_success_false(self):
        self.state.last_tool_result = {"success": False}
        reward = self.evaluator.evaluate(self.state)
        self.assertLessEqual(reward, 0.3)

    def test_evaluate_success_iteration_penalty(self):
        self.state.last_tool_result = {"success": True}
        self.state.iteration = 5
        self.state.max_iterations = 10
        reward1 = self.evaluator.evaluate(self.state)
        
        self.state.iteration = 10
        reward2 = self.evaluator.evaluate(self.state)
        
        self.assertGreater(reward1, reward2)

    def test_evaluate_success_token_penalty(self):
        self.state.last_tool_result = {"success": True}
        self.state.tokens_used = 900
        self.state.token_budget = 1000
        reward = self.evaluator.evaluate(self.state)
        
        self.state.tokens_used = 500
        reward2 = self.evaluator.evaluate(self.state)
        
        self.assertGreaterEqual(reward, 0.7)
        self.assertGreaterEqual(reward2, reward)

    def test_evaluate_failure_stuck_penalty(self):
        self.state.last_tool_result = {"error": "Failed"}
        self.state.features = Mock()
        self.state.features.stuck_iterations = 0
        reward1 = self.evaluator.evaluate(self.state)

        self.state.features.stuck_iterations = 10
        reward2 = self.evaluator.evaluate(self.state)

        self.assertGreaterEqual(reward1, reward2)

    def test_reward_bounds(self):
        for _ in range(10):
            self.state.iteration = 100
            self.state.max_iterations = 100
            self.state.tokens_used = 10000
            self.state.token_budget = 10000
            reward = self.evaluator.evaluate(self.state)
            self.assertGreaterEqual(reward, 0.0)
            self.assertLessEqual(reward, 1.0)


class TestFeedbackEvaluatorExplain(unittest.TestCase):
    def setUp(self):
        self.evaluator = FeedbackEvaluator()
        self.state = LoopState(session_id="test")

    def test_explain_success(self):
        self.state.last_tool_result = {"success": True}
        self.state.iteration = 2
        self.state.max_iterations = 10
        explanation = self.evaluator.explain(self.state)
        
        self.assertTrue(explanation["success"])
        self.assertEqual(explanation["base_reward"], 1.0)
        self.assertIn("iteration", explanation["penalties"])

    def test_explain_failure(self):
        self.state.last_tool_result = {"error": "Failed"}
        self.state.features = Mock()
        self.state.features.stuck_iterations = 3
        explanation = self.evaluator.explain(self.state)
        
        self.assertFalse(explanation["success"])
        self.assertEqual(explanation["base_reward"], 0.0)
        self.assertIn("stuck", explanation["penalties"])

    def test_explain_with_token_penalty(self):
        self.state.last_tool_result = {"success": True}
        self.state.tokens_used = 850
        self.state.token_budget = 1000
        explanation = self.evaluator.explain(self.state)
        
        self.assertTrue(explanation["success"])
        if "cost" in explanation["penalties"]:
            self.assertIn("token_ratio", explanation["penalties"]["cost"])


if __name__ == '__main__':
    unittest.main()
