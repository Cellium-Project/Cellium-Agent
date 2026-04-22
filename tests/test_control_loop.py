# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.control_loop import ControlLoop
from app.agent.control.loop_state import LoopState, ControlDecision
from app.agent.control.hybrid_controller import HybridPhase
from app.agent.heuristics.types import DecisionAction, FusedDecision


class TestControlLoopIntegration(unittest.TestCase):
    def setUp(self):
        self.heuristics = Mock()
        self.heuristics.config = Mock()
        self.heuristics.config.get_threshold = Mock(return_value=3)
        self.heuristics.feature_extractor = Mock()
        self.heuristics.evaluate_fused = Mock()
        self.heuristics.reset = Mock()

        self.bandit = Mock()
        self.bandit.select_action = Mock(return_value="retry")
        self.bandit.update = Mock()
        self.bandit.end_session = Mock()
        self.bandit.set_policy_thresholds = Mock()

        self.evaluator = Mock()
        self.evaluator.evaluate = Mock(return_value=1.0)

        self.loop = ControlLoop(self.heuristics, self.bandit, self.evaluator)
        self.state = LoopState(session_id="test")

    def _create_features(self, **kwargs):
        defaults = {
            'stuck_iterations': 0,
            'progress_trend': 0.5,
            'progress_trend_raw': 0.5,
            'progress_score': 0.5,
            'is_output_loop': False,
            'exact_repetition_count': 0,
            'is_making_progress': True,
            'repetition_score': 0.0,
            'pattern_detected': None,
            'context_saturation': 0.0,
        }
        defaults.update(kwargs)
        features = Mock()
        for k, v in defaults.items():
            setattr(features, k, v)
        return features

    def _create_fused_decision(self, action, rules=None, reasons=None):
        return FusedDecision(
            action=action,
            confidence=0.8,
            contributing_rules=rules or [],
            reasons=reasons or [],
            metadata={}
        )

    def _setup_step(self, features, termination_action=DecisionAction.CONTINUE,
                    loop_action=DecisionAction.CONTINUE, term_rules=None, loop_rules=None):
        self.heuristics.feature_extractor.extract = Mock(return_value=features)

        def side_effect(point, context, feats):
            if point.value == "iteration_termination":
                return self._create_fused_decision(termination_action, rules=term_rules)
            return self._create_fused_decision(loop_action, rules=loop_rules)

        self.heuristics.evaluate_fused = Mock(side_effect=side_effect)

    def _bypass_hybrid(self):
        self.state.hybrid_phase = "execute"

    def test_start_session(self):
        decision = self.loop.start_session(self.state)
        self.assertEqual(decision.action_type, "continue")
        self.assertEqual(self.loop._state, self.state)
        self.heuristics.reset.assert_called_once()

    def test_set_policy_thresholds(self):
        thresholds = {"stuck_iterations": 5}
        self.loop.set_policy_thresholds(thresholds)
        self.bandit.set_policy_thresholds.assert_called_once_with(thresholds)

    def test_step_terminate_by_stop_action(self):
        self.loop.start_session(self.state)
        self.state.iteration = 10

        features = self._create_features()
        self._setup_step(features, termination_action=DecisionAction.STOP)

        decision = self.loop.step(self.state)
        self.assertEqual(decision.action_type, "terminate")
        self.assertTrue(decision.should_stop)

    def test_step_terminate_by_output_loop(self):
        self.loop.start_session(self.state)

        features = self._create_features(is_output_loop=True, exact_repetition_count=5)
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertEqual(decision.action_type, "terminate")

    def test_step_hybrid_observe_phase(self):
        self.loop.start_session(self.state)
        self.state.hybrid_phase = HybridPhase.OBSERVE.value

        features = self._create_features()
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertEqual(decision.action_type, "continue")

    def test_step_hybrid_replan_phase_redirect(self):
        self.loop.start_session(self.state)
        self.state.hybrid_phase = HybridPhase.REPLAN.value
        self.state.hybrid_replan_count = 3
        self.state.hybrid_needs_replan = True

        features = self._create_features()
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertEqual(decision.action_type, "redirect")
        self.assertTrue(decision.enable_redirect_guidance)

    def test_step_compress_by_term002(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(context_saturation=0.5)
        self._setup_step(features, term_rules=["term-002"])

        decision = self.loop.step(self.state)
        self.assertIn("compress", decision.params.get("candidate_actions", []))

    def test_step_compress_by_context_saturation(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(context_saturation=0.85)
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertIn("compress", decision.params.get("candidate_actions", []))

    def test_step_redirect_by_loop002(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features()
        self._setup_step(features, loop_rules=["loop-002"])

        decision = self.loop.step(self.state)
        self.assertIn("redirect", decision.params.get("candidate_actions", []))

    def test_step_retry_by_stuck_no_progress(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(stuck_iterations=2, is_making_progress=False)
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertIn("retry", decision.params.get("candidate_actions", []))

    def test_step_redirect_by_repetition_score(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(repetition_score=0.6)
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertIn("redirect", decision.params.get("candidate_actions", []))

    def test_step_redirect_by_cycle_pattern(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(pattern_detected="cycle")
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertIn("redirect", decision.params.get("candidate_actions", []))

    def test_step_bandit_tiebreak(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(
            stuck_iterations=2,
            is_making_progress=False,
            repetition_score=0.6
        )
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertEqual(decision.action_type, "retry")
        self.bandit.select_action.assert_called_once()

    def test_step_continue_when_making_progress(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(is_making_progress=True, stuck_iterations=2)
        self._setup_step(features)

        decision = self.loop.step(self.state)
        actions = decision.params.get("candidate_actions", [])
        self.assertIn("continue", actions)

    def test_step_context_trim_by_token_ratio(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()
        self.state.tokens_used = 850
        self.state.token_budget = 1000

        features = self._create_features()
        self._setup_step(features)

        decision = self.loop.step(self.state)
        self.assertEqual(decision.context_trim_level, "aggressive")

    def test_end_round(self):
        self.loop.start_session(self.state)
        self.evaluator.evaluate = Mock(return_value=0.75)

        reward = self.loop.end_round(self.state)
        self.assertEqual(reward, 0.75)
        self.assertEqual(self.state.round_reward, 0.75)
        self.assertEqual(self.state.cumulative_reward, 0.75)

    def test_end_round_with_task_type(self):
        self.loop.start_session(self.state)
        self.state.user_input = "debug python code"
        self.evaluator.evaluate_with_gene_evolution = Mock(return_value=0.75)

        reward = self.loop.end_round(self.state)
        self.assertEqual(reward, 0.75)

    def test_end_round_updates_bandit(self):
        self.loop.start_session(self.state)

        decision = ControlDecision(action_type="retry")
        decision.params["bandit_tiebreak"] = True
        self.state.decision_trace.append(decision)

        self.evaluator.evaluate = Mock(return_value=0.8)
        self.loop.end_round(self.state)

        self.bandit.update.assert_called_once_with("retry", 0.8)

    def test_end_session(self):
        self.loop.start_session(self.state)
        self.state.iteration = 5
        self.state.cumulative_reward = 3.5

        self.loop.end_session(self.state)
        self.bandit.end_session.assert_called_once()

    def test_redirect_suggestions_collection(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()
        self.state.available_tools = ["tool1", "tool2", "tool3"]

        features = self._create_features()
        self.heuristics.feature_extractor.extract = Mock(return_value=features)

        def side_effect(point, context, feats):
            if point.value == "iteration_termination":
                return FusedDecision(
                    action=DecisionAction.REDIRECT,
                    confidence=0.8,
                    metadata={"redirect_suggestions": ["Try tool A", "Try tool B"]}
                )
            return FusedDecision(action=DecisionAction.REDIRECT, confidence=0.8)

        self.heuristics.evaluate_fused = Mock(side_effect=side_effect)
        self.bandit.select_action = Mock(return_value="redirect")

        decision = self.loop.step(self.state)
        self.assertEqual(decision.action_type, "redirect")
        self.assertIsNotNone(decision.suggested_tools)
        self.assertEqual(len(decision.suggested_tools), 3)

    def test_action_candidate_ordering(self):
        self.loop.start_session(self.state)
        self._bypass_hybrid()

        features = self._create_features(
            stuck_iterations=2,
            is_making_progress=False,
            repetition_score=0.6,
            context_saturation=0.9
        )
        self._setup_step(features)

        decision = self.loop.step(self.state)
        actions = decision.params.get("candidate_actions", [])

        expected_order = ["continue", "retry", "redirect", "compress"]
        filtered_expected = [a for a in expected_order if a in actions]
        actual_order = [a for a in actions if a in expected_order]
        self.assertEqual(actual_order, filtered_expected)


class TestCreateControlLoop(unittest.TestCase):
    def test_create_control_loop_imports(self):
        from app.agent.control.control_loop import create_control_loop
        self.assertTrue(callable(create_control_loop))


if __name__ == '__main__':
    unittest.main()
