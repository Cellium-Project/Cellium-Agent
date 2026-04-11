# -*- coding: utf-8 -*-
"""
启发式模块单元测试
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.heuristics.types import (
    DecisionPoint,
    DecisionAction,
    RulePriority,
    EvaluationContext,
    DerivedFeatures,
    RuleEvaluationResult,
)
from app.agent.heuristics.engine import HeuristicEngine
from app.agent.heuristics.features import FeatureExtractor
from app.agent.learning.memory_policy import PolicyBanditMemory
from app.agent.control.action_bandit import ActionBandit
from app.agent.control.control_loop import ControlLoop
from app.agent.control.feedback_evaluator import FeedbackEvaluator
from app.agent.control.loop_state import ControlDecision, LoopState
from app.agent.heuristics.rules.termination import (
    MaxIterationRule,
    TokenBudgetRule,
    EmptyResultChainRule,
    NoProgressRule,
)
from app.agent.heuristics.rules.loop_detection import (
    SameToolRepetitionRule,
    PatternLoopRule,
)


class TestFeatureExtractor(unittest.TestCase):
    """特征提取器测试"""

    def test_extract_empty_context(self):
        """测试空上下文"""
        extractor = FeatureExtractor()
        context = EvaluationContext(
            session_id="test",
            iteration=1,
            max_iterations=10,
        )
        features = extractor.extract(context)

        self.assertGreaterEqual(features.progress_score, 0)
        self.assertEqual(features.stuck_iterations, 0)

    def test_extract_with_tool_calls(self):
        """测试有工具调用的情况"""
        extractor = FeatureExtractor()
        context = EvaluationContext(
            session_id="test",
            iteration=3,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell", "result": {"output": "ok"}},
                {"tool_name": "file", "result": {"content": "test"}},
                {"tool_name": "shell", "result": {"output": "ok2"}},
            ],
            recent_tool_calls=[
                {"tool_name": "shell", "result": {"output": "ok"}},
            ],
        )
        features = extractor.extract(context)

        self.assertEqual(features.unique_tools_used, 2)
        self.assertGreater(features.tool_diversity_score, 0)

    def test_repetition_score(self):
        """测试重复分数计算"""
        extractor = FeatureExtractor()
        context = EvaluationContext(
            session_id="test",
            iteration=5,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell"},
                {"tool_name": "shell"},
                {"tool_name": "shell"},
            ],
            recent_tool_calls=[
                {"tool_name": "shell"},
                {"tool_name": "shell"},
                {"tool_name": "shell"},
            ],
        )
        features = extractor.extract(context)

        self.assertGreaterEqual(features.repetition_score, 0.5)

    def test_pattern_detection_abab(self):
        """测试 ABAB 模式检测"""
        extractor = FeatureExtractor()
        context = EvaluationContext(
            session_id="test",
            iteration=5,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell"},
                {"tool_name": "file"},
                {"tool_name": "shell"},
                {"tool_name": "file"},
            ],
            recent_tool_calls=[
                {"tool_name": "shell"},
                {"tool_name": "file"},
                {"tool_name": "shell"},
                {"tool_name": "file"},
            ],
        )
        features = extractor.extract(context)

        self.assertEqual(features.pattern_detected, "cycle")
        self.assertEqual(features.pattern_cycle_length, 2)


class TestFeatureExtractorRuntimeSignals(unittest.TestCase):
    """progress/stuck 信号测试"""

    def test_repeated_failures_raise_stuck_without_fake_progress(self):
        extractor = FeatureExtractor()

        contexts = [
            EvaluationContext(
                session_id="test",
                iteration=1,
                max_iterations=10,
                tool_call_history=[
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                ],
                recent_tool_calls=[
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                ],
            ),
            EvaluationContext(
                session_id="test",
                iteration=2,
                max_iterations=10,
                tool_call_history=[
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                ],
                recent_tool_calls=[
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                ],
            ),
            EvaluationContext(
                session_id="test",
                iteration=3,
                max_iterations=10,
                tool_call_history=[
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                ],
                recent_tool_calls=[
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                    {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                ],
            ),
        ]

        first = extractor.extract(contexts[0])
        second = extractor.extract(contexts[1])
        third = extractor.extract(contexts[2])

        self.assertLessEqual(third.progress_score, first.progress_score)
        self.assertGreaterEqual(second.stuck_iterations, 1)
        self.assertGreaterEqual(third.stuck_iterations, second.stuck_iterations)
        self.assertLessEqual(third.progress_trend, 0.0)

    def test_meaningful_result_resets_stuck_and_improves_progress(self):
        extractor = FeatureExtractor()

        failed = extractor.extract(EvaluationContext(
            session_id="test",
            iteration=1,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
            ],
            recent_tool_calls=[
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
            ],
        ))
        extractor.extract(EvaluationContext(
            session_id="test",
            iteration=2,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
            ],
            recent_tool_calls=[
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
            ],
        ))
        recovered = extractor.extract(EvaluationContext(
            session_id="test",
            iteration=3,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                {"tool_name": "file", "arguments": {"path": "a.py"}, "result": {"content": "print('ok')"}},
            ],
            recent_tool_calls=[
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                {"tool_name": "shell", "arguments": {"command": "ls"}, "result": {"error": "fail"}},
                {"tool_name": "file", "arguments": {"path": "a.py"}, "result": {"content": "print('ok')"}},
            ],
        ))

        self.assertGreater(recovered.progress_score, failed.progress_score)
        self.assertEqual(recovered.stuck_iterations, 0)
        self.assertGreater(recovered.progress_trend_raw, 0.0)


class TestMaxIterationRule(unittest.TestCase):
    """最大迭代规则测试"""

    def test_not_triggered(self):
        """未触发"""
        rule = MaxIterationRule()
        context = EvaluationContext(
            session_id="test",
            iteration=3,
            max_iterations=10,
        )
        features = DerivedFeatures()
        result = rule.evaluate(context, features)

        self.assertFalse(result.matched)

    def test_warn_threshold(self):
        """警告阈值"""
        rule = MaxIterationRule()
        context = EvaluationContext(
            session_id="test",
            iteration=8,
            max_iterations=10,
        )
        features = DerivedFeatures()
        result = rule.evaluate(context, features)

        self.assertTrue(result.matched)
        self.assertEqual(result.action, DecisionAction.WARN)

    def test_stop_threshold(self):
        """停止阈值"""
        rule = MaxIterationRule()
        context = EvaluationContext(
            session_id="test",
            iteration=10,
            max_iterations=10,
        )
        features = DerivedFeatures()
        result = rule.evaluate(context, features)

        self.assertTrue(result.matched)
        self.assertEqual(result.action, DecisionAction.STOP)


class TestNoProgressRule(unittest.TestCase):
    """NoProgressRule 测试"""

    def test_rule_exists(self):
        """测试规则存在"""
        rule = NoProgressRule()
        self.assertEqual(rule.id, "term-004")
        self.assertEqual(rule.name, "No Progress Detection")


class TestSameToolRepetitionRule(unittest.TestCase):
    """SameToolRepetitionRule 测试"""

    def test_rule_exists(self):
        """测试规则存在"""
        rule = SameToolRepetitionRule()
        self.assertEqual(rule.id, "loop-001")
        self.assertEqual(rule.name, "Same Tool Repetition")


class TestPatternLoopRule(unittest.TestCase):
    """循环模式规则测试"""

    def test_no_pattern(self):
        """无循环模式"""
        rule = PatternLoopRule()
        context = EvaluationContext(
            session_id="test",
            iteration=3,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell"},
                {"tool_name": "file"},
                {"tool_name": "memory"},
            ],
        )
        features = DerivedFeatures()
        result = rule.evaluate(context, features)

        self.assertFalse(result.matched)

    def test_detect_cycle(self):
        """检测到循环"""
        rule = PatternLoopRule()
        context = EvaluationContext(
            session_id="test",
            iteration=6,
            max_iterations=10,
            tool_call_history=[
                {"tool_name": "shell"},
                {"tool_name": "file"},
                {"tool_name": "shell"},
                {"tool_name": "file"},
                {"tool_name": "shell"},
                {"tool_name": "file"},
            ],
        )
        features = DerivedFeatures(pattern_detected="cycle")
        result = rule.evaluate(context, features)

        self.assertTrue(result.matched)


class TestHeuristicEngine(unittest.TestCase):
    """启发式引擎测试"""

    def test_engine_initialization(self):
        """引擎初始化"""
        engine = HeuristicEngine()
        self.assertIsNotNone(engine)
        self.assertIsNotNone(engine.feature_extractor)

    def test_get_rules_for_point(self):
        """获取决策点规则"""
        engine = HeuristicEngine()
        rules = engine.registry.get_rules_for_point(DecisionPoint.ITERATION_TERMINATION)
        self.assertIsInstance(rules, list)

    def test_register_rule(self):
        """注册规则"""
        engine = HeuristicEngine()

        class TestRule:
            id = "test_rule"
            name = "Test Rule"
            decision_point = DecisionPoint.ITERATION_TERMINATION
            priority = RulePriority.MEDIUM
            enabled = True

            def evaluate(self, context, features):
                return RuleEvaluationResult.not_matched()

        initial_count = len(engine.registry.all_rules())
        engine.registry.register(TestRule())
        self.assertEqual(len(engine.registry.all_rules()), initial_count + 1)


class TestDecisionTypes(unittest.TestCase):
    """决策类型测试"""

    def test_decision_point_enum(self):
        """决策点枚举"""
        self.assertEqual(DecisionPoint.ITERATION_TERMINATION.value, "iteration_termination")
        self.assertEqual(DecisionPoint.TOOL_SELECTION.value, "tool_selection")
        self.assertEqual(DecisionPoint.LOOP_DETECTION.value, "loop_detection")

    def test_decision_action_enum(self):
        """决策动作枚举"""
        self.assertEqual(DecisionAction.CONTINUE.value, "continue")
        self.assertEqual(DecisionAction.STOP.value, "stop")
        self.assertEqual(DecisionAction.WARN.value, "warn")

    def test_rule_priority_enum(self):
        """规则优先级枚举"""
        self.assertGreater(RulePriority.CRITICAL.value, RulePriority.HIGH.value)
        self.assertGreater(RulePriority.HIGH.value, RulePriority.MEDIUM.value)

    def test_rule_evaluation_result_not_matched(self):
        """规则未匹配结果"""
        result = RuleEvaluationResult.not_matched()
        self.assertFalse(result.matched)
        self.assertEqual(result.action, DecisionAction.CONTINUE)


class TestHeuristicEngineRuntimeBehavior(unittest.TestCase):
    """启发式运行时行为测试"""

    def test_should_stop_checks_iteration_termination(self):
        engine = HeuristicEngine()
        engine.config.enabled = True
        context = EvaluationContext(
            session_id="test",
            iteration=6,
            max_iterations=10,
        )
        features = DerivedFeatures()

        def _fake_evaluate(point, *_args, **_kwargs):
            if point == DecisionPoint.ITERATION_TERMINATION:
                return Mock(action=DecisionAction.STOP, reasons=["termination stop"])
            return None

        engine.evaluate_fused = Mock(side_effect=_fake_evaluate)

        should_stop, reason = engine.should_stop(context, features)

        self.assertTrue(should_stop)
        self.assertIn("termination stop", reason)
        self.assertEqual(engine.evaluate_fused.call_args_list[0].args[0], DecisionPoint.ITERATION_TERMINATION)


class TestActionBanditTiebreak(unittest.TestCase):
    """ActionBandit 仅做候选平局决策"""

    def test_select_action_respects_candidate_actions(self):
        bandit = ActionBandit()
        features = DerivedFeatures()
        bandit._thompson_with_bias = Mock(return_value="redirect")

        action = bandit.select_action(features, candidate_actions=["retry", "redirect"])

        self.assertEqual(action, "redirect")
        self.assertEqual(
            bandit._thompson_with_bias.call_args.kwargs["candidate_actions"],
            ["retry", "redirect"],
        )

    def test_single_candidate_bypasses_sampling(self):
        bandit = ActionBandit()
        features = DerivedFeatures()
        bandit._thompson_with_bias = Mock(return_value="redirect")

        action = bandit.select_action(features, candidate_actions=["compress"])

        self.assertEqual(action, "compress")
        bandit._thompson_with_bias.assert_not_called()


class TestControlLoopRuleFirstSelection(unittest.TestCase):
    """ControlLoop 规则优先测试"""

    def test_stop_rule_bypasses_bandit(self):
        heuristics = Mock()
        heuristics.feature_extractor.extract = Mock(return_value=DerivedFeatures())
        heuristics.config.get_threshold = Mock(return_value=3)
        heuristics.evaluate_fused = Mock(side_effect=[
            Mock(action=DecisionAction.STOP, reasons=["stop"], contributing_rules=["term-004"], metadata={}),
            None,
        ])
        bandit = Mock()
        loop = ControlLoop(heuristics, bandit, FeedbackEvaluator())
        state = LoopState(iteration=3, max_iterations=10, session_id="s")

        decision = loop.step(state)

        self.assertTrue(decision.should_stop)
        self.assertEqual(decision.action_type, "terminate")
        self.assertFalse(decision.params["bandit_tiebreak"])
        bandit.select_action.assert_not_called()

    def test_bandit_only_breaks_ties_between_rule_candidates(self):
        heuristics = Mock()
        heuristics.feature_extractor.extract = Mock(return_value=DerivedFeatures(
            stuck_iterations=2,
            repetition_score=0.7,
            context_saturation=0.82,
            is_making_progress=False,
        ))
        heuristics.config.get_threshold = Mock(return_value=3)
        heuristics.evaluate_fused = Mock(side_effect=[
            Mock(action=DecisionAction.WARN, reasons=["token high"], contributing_rules=["term-002"], metadata={}),
            Mock(action=DecisionAction.WARN, reasons=["loop warn"], contributing_rules=["loop-001"], metadata={}),
        ])
        bandit = Mock()
        bandit.select_action = Mock(return_value="compress")
        loop = ControlLoop(heuristics, bandit, FeedbackEvaluator())
        state = LoopState(iteration=4, max_iterations=10, session_id="s")

        decision = loop.step(state)

        self.assertEqual(decision.action_type, "compress")
        self.assertTrue(decision.params["bandit_tiebreak"])
        self.assertCountEqual(
            bandit.select_action.call_args.kwargs["candidate_actions"],
            ["retry", "redirect", "compress"],
        )

    def test_end_round_updates_bandit_only_for_tiebreak_decision(self):
        heuristics = Mock()
        bandit = Mock()
        bandit.update = Mock()
        evaluator = Mock()
        evaluator.evaluate = Mock(return_value=0.6)
        loop = ControlLoop(heuristics, bandit, evaluator)
        state = LoopState(iteration=2, max_iterations=10, session_id="s")
        state.decision_trace.append(ControlDecision(action_type="compress", params={"bandit_tiebreak": True}))

        loop.end_round(state)

        bandit.update.assert_called_once_with("compress", 0.6)

        bandit.update.reset_mock()
        state.decision_trace.append(ControlDecision(action_type="terminate", params={"bandit_tiebreak": False}))
        loop.end_round(state)
        bandit.update.assert_not_called()


class TestPolicyBanditMemory(unittest.TestCase):
    """Policy memory 持久化测试"""

    def test_should_decay_persists_session_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "policy_bandit_stats.json")
            memory = PolicyBanditMemory(path=path)

            self.assertFalse(memory.should_decay())

            reloaded = PolicyBanditMemory(path=path)
            self.assertEqual(reloaded.get_summary()["session_count"], 1)


if __name__ == "__main__":
    unittest.main()
