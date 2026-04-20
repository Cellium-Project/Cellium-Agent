# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.hard_constraints import (
    HardConstraintRenderer,
    HardConstraintTemplates,
    FailureConditionBuilder,
    ActionFusion,
    HardConstraint,
)
from app.agent.control.loop_state import ControlDecision


class TestHardConstraintTemplates(unittest.TestCase):
    def test_get_redirect_template(self):
        template = HardConstraintTemplates.get_template("redirect")
        self.assertIn("REDIRECT", template)
        self.assertIn("MUST", template)

    def test_get_compress_template(self):
        template = HardConstraintTemplates.get_template("compress")
        self.assertIn("COMPRESS", template)

    def test_get_terminate_template(self):
        template = HardConstraintTemplates.get_template("terminate")
        self.assertIn("TERMINATE", template)

    def test_get_continue_template(self):
        template = HardConstraintTemplates.get_template("continue")
        self.assertEqual(template, "")

    def test_get_unknown_template(self):
        template = HardConstraintTemplates.get_template("unknown")
        self.assertEqual(template, "")


class TestFailureConditionBuilder(unittest.TestCase):
    def test_build_empty_features(self):
        result = FailureConditionBuilder.build(None)
        self.assertEqual(result, "")

    def test_build_with_repetition(self):
        features = Mock()
        features.repetition_score = 0.6
        features.stuck_iterations = 0
        features.is_output_loop = False
        features.context_saturation = 0.0
        result = FailureConditionBuilder.build(features)
        self.assertIn("repeat", result.lower())

    def test_build_with_stuck(self):
        features = Mock()
        features.repetition_score = 0.0
        features.stuck_iterations = 3
        features.is_output_loop = False
        features.context_saturation = 0.0
        result = FailureConditionBuilder.build(features)
        self.assertIn("no progress", result.lower())

    def test_build_with_output_loop(self):
        features = Mock()
        features.repetition_score = 0.0
        features.stuck_iterations = 0
        features.is_output_loop = True
        features.context_saturation = 0.0
        result = FailureConditionBuilder.build(features)
        self.assertIn("identical", result.lower())


class TestActionFusion(unittest.TestCase):
    def test_fuse_empty(self):
        result = ActionFusion.fuse([])
        self.assertEqual(result, "continue")

    def test_fuse_terminate_priority(self):
        result = ActionFusion.fuse(["retry", "terminate", "redirect"])
        self.assertEqual(result, "terminate")

    def test_fuse_single_action(self):
        result = ActionFusion.fuse(["compress"])
        self.assertEqual(result, "compress")

    def test_fuse_compress_redirect(self):
        result = ActionFusion.fuse(["compress", "redirect"])
        self.assertEqual(result, "compress_redirect")

    def test_fuse_compress_retry(self):
        result = ActionFusion.fuse(["compress", "retry"])
        self.assertEqual(result, "compress_retry")

    def test_fuse_priority_order(self):
        result = ActionFusion.fuse(["continue", "redirect", "compress"])
        self.assertEqual(result, "compress")


class TestHardConstraintRenderer(unittest.TestCase):
    def setUp(self):
        self.renderer = HardConstraintRenderer(max_output_tokens=100)

    def test_render_terminate(self):
        decision = ControlDecision(action_type="terminate")
        result = self.renderer.render(decision)
        self.assertIsInstance(result, HardConstraint)
        self.assertTrue(result.force_stop)
        self.assertIn("terminate", result.trigger_reason)

    def test_render_compress(self):
        decision = ControlDecision(action_type="compress")
        result = self.renderer.render(decision)
        self.assertIsInstance(result, HardConstraint)
        self.assertIn("compress", result.trigger_reason)
        self.assertEqual(result.max_tokens, 100)

    def test_render_redirect(self):
        decision = ControlDecision(action_type="redirect")
        decision.params["suggested_tools"] = ["tool1", "tool2"]
        result = self.renderer.render(decision)
        self.assertIsInstance(result, HardConstraint)
        self.assertIn("redirect", result.trigger_reason)

    def test_render_retry(self):
        decision = ControlDecision(action_type="retry")
        result = self.renderer.render(decision)
        self.assertIsInstance(result, HardConstraint)
        self.assertIn("retry", result.trigger_reason)

    def test_render_continue(self):
        decision = ControlDecision(action_type="continue")
        result = self.renderer.render(decision)
        self.assertIsInstance(result, HardConstraint)
        self.assertEqual(result.hard_constraints, "")

    def test_render_with_features(self):
        decision = ControlDecision(action_type="compress")
        features = Mock()
        features.repetition_score = 0.6
        features.stuck_iterations = 2
        features.is_output_loop = True
        features.context_saturation = 0.8
        result = self.renderer.render(decision, features)
        self.assertIsInstance(result, HardConstraint)
        self.assertNotEqual(result.failure_conditions, "")


if __name__ == '__main__':
    unittest.main()
