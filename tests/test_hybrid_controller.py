# -*- coding: utf-8 -*-
"""
PEOP 循环 (Plan-Execute-Observe-RePlan) 测试

测试 HybridController 的核心状态流转：
- OBSERVE → PLAN → EXECUTE → (EVALUATE → REPLAN → EXECUTE)* → DONE
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.hybrid_controller import (
    HybridController,
    HybridPhase,
    HybridState,
    Observation,
)
from app.agent.control.thought_parser import ThoughtStep, ParsedThought, ActionType


class TestHybridControllerPhaseTransition(unittest.TestCase):
    """测试 PEOP 循环阶段转换"""

    def setUp(self):
        self.controller = HybridController()

    def test_initial_phase_is_observe(self):
        """初始阶段应为 OBSERVE"""
        self.assertEqual(self.controller.state.phase, HybridPhase.OBSERVE)

    def test_direct_response_transitions_to_done(self):
        """直接回答应进入 DONE 阶段"""
        self.controller._state.phase = HybridPhase.PLAN
        
        result = self.controller.process_thought('''
        ```json
        {"reasoning": "可以直接回答", "plan": [], "action": "direct_response"}
        ```
        ''')
        
        self.assertEqual(self.controller.state.phase, HybridPhase.DONE)
        self.assertTrue(self.controller.state.skip_llm)

    def test_clarify_transitions_to_done(self):
        """需要澄清应进入 DONE 阶段"""
        self.controller._state.phase = HybridPhase.PLAN
        
        result = self.controller.process_thought('''
        ```json
        {"reasoning": "需要更多信息", "plan": [], "action": "clarify"}
        ```
        ''')
        
        self.assertEqual(self.controller.state.phase, HybridPhase.DONE)
        self.assertTrue(self.controller.state.skip_llm)
        self.assertTrue(self.controller.state.needs_clarification)

    def test_observe_to_execute_transition(self):
        """OBSERVE → EXECUTE 转换（初始观察）"""
        self.controller._state.initial_observation_done = False
        self.controller._state.phase = HybridPhase.OBSERVE
        
        result = self.controller.process_thought('''
        ```json
        {"reasoning": "需要搜索", "plan": [{"tool": "search", "purpose": "搜索信息", "expected_result": "结果"}], "action": "tool_call"}
        ```
        ''')
        
        self.assertEqual(self.controller.state.phase, HybridPhase.EXECUTE)
        self.assertEqual(len(self.controller.state.current_plan), 1)

    def test_plan_to_execute_transition(self):
        """PLAN → EXECUTE 转换"""
        self.controller._state.initial_observation_done = True
        self.controller._state.phase = HybridPhase.PLAN
        
        result = self.controller.process_thought('''
        ```json
        {"reasoning": "需要读取文件", "plan": [{"tool": "read_file", "purpose": "读取", "expected_result": "内容"}], "action": "tool_call"}
        ```
        ''')
        
        self.assertEqual(self.controller.state.phase, HybridPhase.EXECUTE)
        self.assertEqual(len(self.controller.state.current_plan), 1)
        self.assertEqual(len(self.controller.state.pending_steps), 1)

    def test_execute_step_and_continue(self):
        """执行步骤并继续执行"""
        step1 = ThoughtStep(
            tool="search",
            purpose="搜索",
            expected_result="搜索结果",
        )
        step2 = ThoughtStep(
            tool="read_file",
            purpose="读取",
            expected_result="文件内容",
        )
        self.controller._state.current_plan = [step1, step2]
        self.controller._state.pending_steps = [step1, step2]
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.initial_observation_done = True
        
        obs = self.controller.observe_result(
            step=step1,
            success=True,
            output={"result": "搜索结果"},
        )
        
        self.assertEqual(len(self.controller.state.executed_steps), 1)
        self.assertEqual(len(self.controller.state.pending_steps), 1)
        self.assertEqual(self.controller.state.phase, HybridPhase.EXECUTE)

    def test_execute_to_done_transition(self):
        """EXECUTE → DONE 转换（所有步骤完成）"""
        step = ThoughtStep(
            tool="search",
            purpose="搜索",
            expected_result="搜索结果",
        )
        self.controller._state.current_plan = [step]
        self.controller._state.pending_steps = [step]
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.initial_observation_done = True
        
        obs = self.controller.observe_result(
            step=step,
            success=True,
            output={"result": "搜索结果"},  # 符合预期
        )
        
        self.assertEqual(self.controller.state.phase, HybridPhase.DONE)
        self.assertEqual(len(self.controller.state.executed_steps), 1)
        self.assertEqual(len(self.controller.state.pending_steps), 0)

    def test_execute_to_replan_on_failure(self):
        """执行失败 → REPLAN"""
        step = ThoughtStep(
            tool="read_file",
            purpose="读取文件",
            expected_result="内容",
        )
        self.controller._state.current_plan = [step]
        self.controller._state.pending_steps = [step]
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.initial_observation_done = True
        self.controller._state.replan_count = 0
        
        obs = self.controller.observe_result(
            step=step,
            success=False,
            output={"error": "文件不存在"},
        )
        
        self.assertEqual(self.controller.state.phase, HybridPhase.REPLAN)
        self.assertEqual(self.controller.state.replan_count, 1)
        self.assertIn("执行失败", obs.replan_reason)

    def test_execute_to_replan_on_mismatch(self):
        """结果不符合预期 + 强制模式 → REPLAN"""
        # 使用强制重新规划模式
        self.controller.suggest_replan_on_mismatch = False
        
        step = ThoughtStep(
            tool="search",
            purpose="搜索Python",
            expected_result="Python相关结果",
        )
        self.controller._state.current_plan = [step]
        self.controller._state.pending_steps = [step]
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.initial_observation_done = True
        
        obs = self.controller.observe_result(
            step=step,
            success=True,
            output={"result": "Java相关结果"},  # 不符合预期
        )
        
        self.assertEqual(self.controller.state.phase, HybridPhase.REPLAN)
        self.assertEqual(self.controller.state.replan_count, 1)
        self.assertIn("不符合预期", obs.replan_reason)

    def test_execute_suggest_replan_on_mismatch(self):
        """结果不符合预期 + 建议模式 → 建议但不强制 REPLAN"""
        # 默认是建议模式
        self.assertTrue(self.controller.suggest_replan_on_mismatch)
        
        step = ThoughtStep(
            tool="search",
            purpose="搜索Python",
            expected_result="Python相关结果",
        )
        self.controller._state.current_plan = [step]
        self.controller._state.pending_steps = [step]
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.initial_observation_done = True
        
        obs = self.controller.observe_result(
            step=step,
            success=True,
            output={"result": "Java相关结果"},  # 不符合预期
        )
        
        # 不进入 REPLAN 阶段，而是继续执行或完成
        self.assertEqual(self.controller.state.phase, HybridPhase.DONE)
        # 但会标记建议重新规划
        self.assertTrue(obs.suggest_replan)
        self.assertIn("预期不符", obs.suggestion_reason)

    def test_replan_to_execute_transition(self):
        """REPLAN → EXECUTE 转换"""
        step1 = ThoughtStep(
            tool="read_file",
            purpose="读取",
            expected_result="内容",
        )
        step2 = ThoughtStep(
            tool="write_file",
            purpose="写入",
            expected_result="成功",
        )
        
        self.controller._state.current_plan = [step1]
        self.controller._state.executed_steps = [
            Observation(step=step1, success=True, output_summary="成功")
        ]
        self.controller._state.pending_steps = []
        self.controller._state.phase = HybridPhase.REPLAN
        self.controller._state.replan_count = 1
        self.controller._state.initial_observation_done = True
        
        # 模拟重规划
        result = self.controller.process_thought('''
        ```json
        {"reasoning": "继续执行", "plan": [{"tool": "write_file", "purpose": "写入", "expected_result": "成功"}], "action": "tool_call"}
        ```
        ''')
        
        self.assertEqual(self.controller.state.phase, HybridPhase.EXECUTE)


class TestHybridControllerGetNextStep(unittest.TestCase):
    """测试获取下一步"""

    def setUp(self):
        self.controller = HybridController()

    def test_get_next_step_in_execute_phase(self):
        """EXECUTE 阶段能获取下一步"""
        step = ThoughtStep(
            tool="search",
            purpose="搜索",
            expected_result="结果",
        )
        self.controller._state.current_plan = [step]
        self.controller._state.pending_steps = [step]
        self.controller._state.phase = HybridPhase.EXECUTE
        
        next_step = self.controller.get_next_step()
        
        self.assertIsNotNone(next_step)
        self.assertEqual(next_step.tool, "search")

    def test_get_next_step_returns_none_when_done(self):
        """DONE 阶段返回 None"""
        self.controller._state.phase = HybridPhase.DONE
        
        next_step = self.controller.get_next_step()
        
        self.assertIsNone(next_step)

    def test_get_next_step_returns_none_when_no_pending(self):
        """没有待执行步骤时返回 None（不自动改变 phase）"""
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.pending_steps = []

        next_step = self.controller.get_next_step()

        self.assertIsNone(next_step)
        # get_next_step 不改变 phase，由 observe_result 负责 phase 转换
        self.assertEqual(self.controller.state.phase, HybridPhase.EXECUTE)


class TestHybridControllerShouldMethods(unittest.TestCase):
    """测试 should_call_llm 和 should_execute_tool"""

    def setUp(self):
        self.controller = HybridController()

    def test_should_call_llm_in_plan_phase(self):
        """PLAN 阶段应该调用 LLM"""
        self.controller._state.phase = HybridPhase.PLAN
        self.assertTrue(self.controller.should_call_llm())

    def test_should_call_llm_in_replan_phase(self):
        """REPLAN 阶段应该调用 LLM"""
        self.controller._state.phase = HybridPhase.REPLAN
        self.assertTrue(self.controller.should_call_llm())

    def test_should_not_call_llm_when_done(self):
        """DONE 阶段不应该调用 LLM"""
        self.controller._state.phase = HybridPhase.DONE
        self.assertFalse(self.controller.should_call_llm())

    def test_should_not_call_llm_when_skip_llm(self):
        """skip_llm 为 True 时不应该调用 LLM"""
        self.controller._state.skip_llm = True
        self.controller._state.phase = HybridPhase.PLAN
        self.assertFalse(self.controller.should_call_llm())

    def test_should_execute_tool_in_execute_phase(self):
        """EXECUTE 阶段且有待执行步骤时应该执行工具"""
        step = ThoughtStep(
            tool="search",
            purpose="搜索",
            expected_result="结果",
        )
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.pending_steps = [step]
        
        self.assertTrue(self.controller.should_execute_tool())

    def test_should_not_execute_tool_when_no_pending(self):
        """没有待执行步骤时不应该执行工具"""
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.pending_steps = []
        
        self.assertFalse(self.controller.should_execute_tool())


class TestHybridControllerExpectationCheck(unittest.TestCase):
    """测试结果预期验证"""

    def setUp(self):
        self.controller = HybridController()

    def test_check_expectation_success_no_expected(self):
        """没有预期结果时，执行成功即通过"""
        step = ThoughtStep(
            tool="search",
            purpose="搜索",
            expected_result="",  # 无预期
        )
        result = self.controller._check_expectation(
            step, True, "任意结果"
        )
        self.assertTrue(result)

    def test_check_expectation_failed_execution(self):
        """执行失败时不通过"""
        step = ThoughtStep(
            tool="search",
            purpose="搜索",
            expected_result="预期结果",
        )
        result = self.controller._check_expectation(
            step, False, "错误信息"
        )
        self.assertFalse(result)

    def test_check_expectation_substring_match(self):
        """子串匹配通过"""
        step = ThoughtStep(
            tool="search",
            purpose="搜索Python",
            expected_result="Python",
        )
        result = self.controller._check_expectation(
            step, True, "Python 3.12 文档"
        )
        self.assertTrue(result)

    def test_check_expectation_jaccard_similarity(self):
        """Jaccard 相似度匹配"""
        step = ThoughtStep(
            tool="search",
            purpose="搜索",
            expected_result="python programming language",
        )
        result = self.controller._check_expectation(
            step, True, "python language programming"
        )
        self.assertTrue(result)


class TestHybridControllerMaxReplans(unittest.TestCase):
    """测试最大重规划次数限制"""

    def setUp(self):
        self.controller = HybridController(max_replans=2)

    def test_exceed_max_replans_goes_to_done(self):
        """超过最大重规划次数后进入 DONE"""
        step = ThoughtStep(
            tool="read_file",
            purpose="读取",
            expected_result="内容",
        )
        self.controller._state.current_plan = [step]
        self.controller._state.pending_steps = [step]
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.initial_observation_done = True
        self.controller._state.replan_count = 2  # 已达上限
        
        obs = self.controller.observe_result(
            step=step,
            success=False,
            output={"error": "失败"},
        )
        
        # 超过重规划次数限制，进入 DONE
        self.assertEqual(self.controller.state.phase, HybridPhase.DONE)


class TestHybridControllerReset(unittest.TestCase):
    """测试重置功能"""

    def setUp(self):
        self.controller = HybridController()

    def test_reset_clears_state(self):
        """重置应清空状态"""
        self.controller._state.phase = HybridPhase.EXECUTE
        self.controller._state.replan_count = 2
        self.controller._state.current_plan = [ThoughtStep(
            tool="search", purpose="搜索", expected_result=""
        )]
        
        self.controller.reset()
        
        self.assertEqual(self.controller.state.phase, HybridPhase.OBSERVE)
        self.assertEqual(self.controller.state.replan_count, 0)
        self.assertEqual(len(self.controller.state.current_plan), 0)
        self.assertEqual(len(self.controller.state.executed_steps), 0)


if __name__ == '__main__':
    unittest.main()
