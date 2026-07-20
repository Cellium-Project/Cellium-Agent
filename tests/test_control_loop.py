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
        self.heuristics.config.get_threshold = Mock(side_effect=lambda name, default=None: default)
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

        features = self._create_features(repetition_score=0.75)
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
            repetition_score=0.8
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
        self.evaluator.evaluate_with_gene_evolution = Mock(return_value=0.75)

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

        self.evaluator.evaluate_with_gene_evolution = Mock(return_value=0.8)
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


class TestDecisionObservability(unittest.TestCase):
    """决策可观测性测试 - 预测和验证"""

    def setUp(self):
        self.heuristics = Mock()
        self.heuristics.config = Mock()
        self.heuristics.config.get_threshold = Mock(side_effect=lambda name, default=None: default)
        self.heuristics.feature_extractor = Mock()
        self.heuristics.evaluate_fused = Mock()
        self.heuristics.reset = Mock()

        self.bandit = Mock()
        self.bandit.select_action = Mock(return_value="continue")
        self.bandit.update = Mock()
        self.bandit.end_session = Mock()

        self.evaluator = Mock()
        self.evaluator.evaluate_with_gene_evolution = Mock(return_value=0.5)

        self.loop = ControlLoop(self.heuristics, self.bandit, self.evaluator)
        self.state = LoopState(session_id="test_observability")

    def _create_features(self, **kwargs):
        defaults = {
            'stuck_iterations': 0,
            'progress_trend': 0.5,
            'progress_trend_raw': 0.5,
            'progress_score': 0.6,
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

    def test_decision_has_prediction(self):
        """决策应包含预测和验证标准"""
        self.loop.start_session(self.state)
        self.state.hybrid_phase = "execute"

        features = self._create_features()
        self.heuristics.feature_extractor.extract = Mock(return_value=features)
        self.heuristics.evaluate_fused = Mock(return_value=Mock(
            action=Mock(value="continue"),
            contributing_rules=[],
            reasons=[],
            metadata={}
        ))

        decision = self.loop.step(self.state)

        self.assertTrue(len(decision.predicted_outcome) > 0, "决策应有预测结果")
        self.assertTrue(len(decision.verification_criteria) > 0, "决策应有验证标准")
        self.assertIsNone(decision.prediction_verified, "新决策验证状态应为None")

    def test_continue_action_prediction(self):
        """continue 动作的预测内容"""
        self.loop.start_session(self.state)
        self.state.hybrid_phase = "execute"

        features = self._create_features()
        self.heuristics.feature_extractor.extract = Mock(return_value=features)
        self.heuristics.evaluate_fused = Mock(return_value=Mock(
            action=Mock(value="continue"),
            contributing_rules=[],
            reasons=[],
            metadata={}
        ))

        decision = self.loop.step(self.state)

        self.assertEqual(decision.action_type, "continue")
        self.assertIn("继续执行", decision.predicted_outcome)
        self.assertIn("tool_traces", decision.verification_criteria)

    def test_terminate_action_prediction(self):
        """terminate 动作的预测内容"""
        self.loop.start_session(self.state)
        self.state.hybrid_phase = "execute"
        self.state.iteration = 10

        features = self._create_features()
        self.heuristics.feature_extractor.extract = Mock(return_value=features)

        def side_effect(point, context, feats):
            from app.agent.heuristics.types import DecisionAction
            return Mock(
                action=DecisionAction.STOP,
                contributing_rules=["term-001"],
                reasons=["达到最大迭代次数"],
                metadata={}
            )

        self.heuristics.evaluate_fused = Mock(side_effect=side_effect)

        decision = self.loop.step(self.state)

        self.assertEqual(decision.action_type, "terminate")
        self.assertTrue(decision.should_stop)
        self.assertIn("终止", decision.predicted_outcome)

    def test_prediction_verification_continue_success(self):
        """验证 continue 决策的预测 - 成功情况"""
        self.loop.start_session(self.state)

        # 第一轮决策
        decision1 = ControlDecision(action_type="continue")
        decision1.predicted_outcome = "继续执行"
        decision1.verification_criteria = "tool_traces新增或progress_score>0"
        self.state.decision_trace.append(decision1)

        # 第二轮决策（触发验证上一轮）
        decision2 = ControlDecision(action_type="continue")
        self.state.decision_trace.append(decision2)

        # 模拟执行后有工具调用和进展
        self.state.tool_traces.append({"tool": "test", "success": True})
        self.state.features = self._create_features(progress_score=0.7)

        # 验证上一轮（decision_trace[-2] = decision1）
        self.loop._verify_last_prediction(self.state)

        self.assertTrue(decision1.prediction_verified, "预测应被验证为正确")
        self.assertIn("progress=0.70", decision1.verification_evidence)

    def test_prediction_verification_tool_success(self):
        """验证 retry 决策的预测 - 基于工具结果"""
        self.loop.start_session(self.state)

        # 第一轮决策
        decision1 = ControlDecision(action_type="retry")
        decision1.predicted_outcome = "重试"
        decision1.verification_criteria = "last_tool_result.success=True"
        self.state.decision_trace.append(decision1)

        # 第二轮决策（触发验证上一轮）
        decision2 = ControlDecision(action_type="continue")
        self.state.decision_trace.append(decision2)

        # 模拟工具成功
        self.state.last_tool_result = {"success": True, "output": "ok"}

        # 验证上一轮
        self.loop._verify_last_prediction(self.state)

        self.assertTrue(decision1.prediction_verified, "预测应被验证为正确")
        self.assertIn("success=True", decision1.verification_evidence)

    def test_prediction_verification_no_prediction(self):
        """无预测时不应验证"""
        self.loop.start_session(self.state)

        # 第一轮决策无预测
        decision1 = ControlDecision(action_type="continue")
        # predicted_outcome 默认为空
        self.state.decision_trace.append(decision1)

        # 第二轮决策（触发验证上一轮）
        decision2 = ControlDecision(action_type="continue")
        self.state.decision_trace.append(decision2)

        # 验证
        self.loop._verify_last_prediction(self.state)

        self.assertIsNone(decision1.prediction_verified, "无预测时验证状态应保持None")

    def test_prediction_bonus_applied(self):
        """预测验证结果应影响奖励"""
        self.loop.start_session(self.state)

        # 设置基础奖励
        self.evaluator.evaluate_with_gene_evolution = Mock(return_value=0.5)

        # 第一轮决策
        decision0 = ControlDecision(action_type="continue")
        self.state.decision_trace.append(decision0)

        # 第二轮决策 - 预测正确（将被验证）
        decision1 = ControlDecision(action_type="continue")
        decision1.predicted_outcome = "继续执行"
        decision1.verification_criteria = "tool_traces"
        decision1.prediction_verified = True
        decision1.params["bandit_tiebreak"] = True
        self.state.decision_trace.append(decision1)

        # 第三轮决策（触发验证 decision1）
        decision2 = ControlDecision(action_type="continue")
        self.state.decision_trace.append(decision2)

        # 模拟有工具调用
        self.state.tool_traces.append({"tool": "test", "success": True})
        self.state.features = self._create_features(progress_score=0.7)

        # 执行 end_round
        reward = self.loop.end_round(self.state)

        # 基础奖励 0.5 + 预测正确奖励 0.1 = 0.6
        self.assertAlmostEqual(reward, 0.6, places=2, msg="预测正确应获得 +0.1 奖励")
        self.assertAlmostEqual(self.state.round_reward, 0.6, places=2)

    def test_prediction_penalty_applied(self):
        """预测失败应受到惩罚"""
        self.loop.start_session(self.state)

        # 设置基础奖励
        self.evaluator.evaluate_with_gene_evolution = Mock(return_value=0.5)

        # 第一轮决策
        decision0 = ControlDecision(action_type="continue")
        self.state.decision_trace.append(decision0)

        # 第二轮决策 - 预测错误（将被验证）
        decision1 = ControlDecision(action_type="continue")
        decision1.predicted_outcome = "继续执行"
        decision1.verification_criteria = "tool_traces"
        decision1.prediction_verified = False
        decision1.params["bandit_tiebreak"] = True
        self.state.decision_trace.append(decision1)

        # 第三轮决策（触发验证 decision1）
        decision2 = ControlDecision(action_type="continue")
        self.state.decision_trace.append(decision2)

        # 模拟无进展
        self.state.features = self._create_features(progress_score=0.0)

        # 执行 end_round
        reward = self.loop.end_round(self.state)

        # 基础奖励 0.5 + 预测错误惩罚 -0.1 = 0.4
        self.assertAlmostEqual(reward, 0.4, places=2, msg="预测错误应受到 -0.1 惩罚")


class TestCreateControlLoop(unittest.TestCase):
    def test_create_control_loop_imports(self):
        from app.agent.control.control_loop import create_control_loop
        self.assertTrue(callable(create_control_loop))


if __name__ == '__main__':
    unittest.main()
