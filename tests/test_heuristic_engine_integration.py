# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.heuristics.engine import HeuristicEngine, RuleRegistry
from app.agent.heuristics.types import (
    DecisionPoint, DecisionAction, EvaluationContext, DerivedFeatures, RulePriority
)
from app.agent.heuristics.config import HeuristicConfig
from app.agent.heuristics.rules.termination import (
    MaxIterationRule, TokenBudgetRule, EmptyResultChainRule, NoProgressRule
)
from app.agent.heuristics.rules.loop_detection import (
    SameToolRepetitionRule, PatternLoopRule
)


class TestHeuristicEngineReal(unittest.TestCase):
    def setUp(self):
        self.config = HeuristicConfig._create_default()
        self.engine = HeuristicEngine(self.config)

    def _create_context(self, **kwargs):
        defaults = {
            'session_id': 'test',
            'iteration': 1,
            'max_iterations': 10,
            'recent_tool_calls': [],
            'tool_call_history': [],
            'available_tools': ['file', 'shell', 'memory'],
            'total_tokens_used': 100,
            'token_budget': 10000,
            'elapsed_ms': 0,
            'user_input': 'test',
            'last_tool_result': None,
            'recent_llm_outputs': [],
        }
        defaults.update(kwargs)
        return EvaluationContext(**defaults)

    def _create_features(self, **kwargs):
        features = DerivedFeatures()
        for k, v in kwargs.items():
            if hasattr(features, k):
                setattr(features, k, v)
        return features

    def _force_enable_max_iteration(self):
        rule = self.engine.registry.get("term-001")
        if rule:
            rule.enabled = True

    def test_token_budget_rule_stop(self):
        context = self._create_context(total_tokens_used=9600, token_budget=10000)
        features = self._create_features()

        results, _ = self.engine.evaluate(DecisionPoint.ITERATION_TERMINATION, context, features)

        rule_ids = [r[0].id for r in results]
        self.assertIn("term-002", rule_ids)

        fused = self.engine.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)
        self.assertEqual(fused.action, DecisionAction.STOP)

    def test_token_budget_rule_redirect(self):
        context = self._create_context(total_tokens_used=8700, token_budget=10000)
        features = self._create_features()

        results, _ = self.engine.evaluate(DecisionPoint.ITERATION_TERMINATION, context, features)

        rule_ids = [r[0].id for r in results]
        self.assertIn("term-002", rule_ids)

        fused = self.engine.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)
        self.assertEqual(fused.action, DecisionAction.REDIRECT)

    def test_token_budget_rule_warn(self):
        context = self._create_context(total_tokens_used=7200, token_budget=10000)
        features = self._create_features()

        results, _ = self.engine.evaluate(DecisionPoint.ITERATION_TERMINATION, context, features)

        rule_ids = [r[0].id for r in results]
        self.assertIn("term-002", rule_ids)

        fused = self.engine.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)
        self.assertEqual(fused.action, DecisionAction.WARN)

    def test_token_budget_rule_compress(self):
        context = self._create_context(total_tokens_used=5100, token_budget=10000)
        features = self._create_features()

        results, _ = self.engine.evaluate(DecisionPoint.ITERATION_TERMINATION, context, features)

        rule_ids = [r[0].id for r in results]
        self.assertIn("term-002", rule_ids)

        rule_result = [r[1] for r in results if r[0].id == "term-002"][0]
        self.assertEqual(rule_result.action, DecisionAction.RECOMMEND)

    def test_empty_result_chain_rule(self):
        calls = [
            {"name": "file", "result": {"error": "File not found"}},
            {"name": "file", "result": {"error": "File not found"}},
            {"name": "file", "result": {"error": "File not found"}},
            {"name": "file", "result": {"error": "File not found"}},
            {"name": "file", "result": {"error": "File not found"}},
            {"name": "file", "result": {"error": "File not found"}},
        ]
        context = self._create_context(recent_tool_calls=calls, tool_call_history=calls)
        features = self._create_features()

        results, _ = self.engine.evaluate(DecisionPoint.ITERATION_TERMINATION, context, features)

        rule_ids = [r[0].id for r in results]
        self.assertIn("term-003", rule_ids)

        fused = self.engine.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)
        self.assertEqual(fused.action, DecisionAction.REDIRECT)
        self.assertIn("redirect_suggestions", fused.metadata)

    def test_same_tool_repetition_rule(self):
        calls = [
            {"name": "file", "args": {"path": "/a"}},
            {"name": "file", "args": {"path": "/b"}},
            {"name": "file", "args": {"path": "/c"}},
            {"name": "file", "args": {"path": "/d"}},
        ]
        context = self._create_context(
            recent_tool_calls=calls,
            tool_call_history=calls
        )
        features = self._create_features(repetition_score=0.8)

        results, _ = self.engine.evaluate(DecisionPoint.LOOP_DETECTION, context, features)

        rule_ids = [r[0].id for r in results]
        self.assertIn("loop-001", rule_ids)

    def test_no_progress_rule(self):
        context = self._create_context(iteration=5)
        features = self._create_features(
            stuck_iterations=5,
            progress_trend=-0.5,
            is_making_progress=False
        )

        results, _ = self.engine.evaluate(DecisionPoint.ITERATION_TERMINATION, context, features)

        rule_ids = [r[0].id for r in results]
        self.assertIn("term-004", rule_ids)

    def test_rule_priority_fusion_token_budget(self):
        context = self._create_context(
            total_tokens_used=9600,
            token_budget=10000
        )
        features = self._create_features()

        fused = self.engine.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)

        self.assertEqual(fused.action, DecisionAction.STOP)
        self.assertIn("term-002", fused.contributing_rules)

    def test_no_match_return_none(self):
        context = self._create_context(iteration=1, max_iterations=100, total_tokens_used=100)
        features = self._create_features()

        fused = self.engine.evaluate_fused(DecisionPoint.ITERATION_TERMINATION, context, features)

        self.assertIsNone(fused)

    def test_max_iteration_rule_manual_enable(self):
        rule = MaxIterationRule()
        rule.enabled = True
        self.engine.registry.register(rule)

        context = self._create_context(iteration=10, max_iterations=10)
        features = self._create_features()

        result = rule.evaluate(context, features)
        self.assertTrue(result.matched)
        self.assertEqual(result.action, DecisionAction.STOP)

    def test_feature_extraction_basic(self):
        calls = [
            {"name": "file", "result": {"content": "data"}},
            {"name": "shell", "result": {"output": "ok"}},
        ]
        context = self._create_context(
            recent_tool_calls=calls,
            tool_call_history=calls,
            total_tokens_used=5000,
            token_budget=10000
        )

        features = self.engine.feature_extractor.extract(context)

        self.assertGreaterEqual(features.unique_tools_used, 1)
        self.assertGreaterEqual(features.tool_diversity_score, 0)
        self.assertLessEqual(features.tool_diversity_score, 1)


if __name__ == '__main__':
    unittest.main()
