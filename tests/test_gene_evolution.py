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

    @patch("app.agent.control.constraint_gene.GeneEvolution")
    def test_evaluate_with_gene_evolution_failure(self, mock_gene_evolution):
        from app.agent.control.feedback_evaluator import FeedbackEvaluator

        mock_gene_evolution.extract_avoid_cue.return_value = "DON'T: Test"
        mock_gene_evolution.should_prompt_agent_for_gene.return_value = False 

        evaluator = FeedbackEvaluator()
        state = Mock(spec=LoopState)
        state.last_tool_result = {"error": "Failed"}
        state.last_error = "Test error"
        state.iteration = 5
        state.max_iterations = 10
        state.tokens_used = 1000
        state.token_budget = 10000
        state.features = None
        state.elapsed_ms = 5000
        state.gene_failure_count = 0  
        state.gene_failure_history = []

        reward = evaluator.evaluate_with_gene_evolution(state, "test_task")

        self.assertLess(reward, 0.5)
        mock_gene_evolution.extract_avoid_cue.assert_called_once()
        mock_gene_evolution.should_prompt_agent_for_gene.assert_not_called()
        mock_gene_evolution.update_gene_from_failure.assert_not_called()

        self.assertEqual(state.gene_failure_count, 1)

    @patch("app.agent.control.constraint_gene.GeneEvolution")
    def test_evaluate_with_gene_evolution_triggers_agent_creation(self, mock_gene_evolution):
        """测试连续2次失败后触发 Agent 创建"""
        from app.agent.control.feedback_evaluator import FeedbackEvaluator

        mock_gene_evolution.extract_avoid_cue.return_value = "DON'T: Test"
        mock_gene_evolution.should_prompt_agent_for_gene.return_value = True 
        mock_gene_evolution._get_existing_gene.return_value = None

        evaluator = FeedbackEvaluator()
        state = Mock(spec=LoopState)
        state.last_tool_result = {"error": "Failed"}
        state.last_error = "Test error"
        state.iteration = 5
        state.max_iterations = 10
        state.tokens_used = 1000
        state.token_budget = 10000
        state.features = None
        state.elapsed_ms = 5000
        state.gene_failure_count = 1  
        state.gene_failure_history = [{"iteration": 4, "avoid_cue": "DON'T: First"}]

        reward = evaluator.evaluate_with_gene_evolution(state, "test_task", "测试输入")

        self.assertLess(reward, 0.5)
        mock_gene_evolution.extract_avoid_cue.assert_called_once()
        mock_gene_evolution.should_prompt_agent_for_gene.assert_called_once()
        # 验证设置了 Gene 创建标记
        self.assertTrue(state.needs_agent_gene_creation)
        # 验证提示包含系统提示标记
        self.assertIn("[系统提示]", state.gene_creation_prompt)
        # 应该调用自动更新
        mock_gene_evolution.update_gene_from_failure.assert_called_once()
        # 验证重置失败计数
        self.assertEqual(state.gene_failure_count, 0)
        self.assertEqual(len(state.gene_failure_history), 0)

    @patch("app.agent.control.constraint_gene.GeneEvolution")
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
        state.gene_failure_count = 1  
        state.gene_failure_history = [{"iteration": 2, "avoid_cue": "test"}]

        reward = evaluator.evaluate_with_gene_evolution(state, "test_task")

        self.assertGreater(reward, 0.5)
        mock_gene_evolution.record_success.assert_called_once()
        # 新逻辑：成功时不重置失败计数器（保持累积）
        self.assertEqual(state.gene_failure_count, 1)
        self.assertEqual(len(state.gene_failure_history), 1)


class TestAgentGeneCreation(unittest.TestCase):
    """测试 Agent 创建 Gene 功能"""

    def setUp(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def tearDown(self):
        TaskSignalMatcher._repository = None
        TaskSignalMatcher._cache.clear()
        TaskSignalMatcher._cache_loaded = False

    def test_should_prompt_agent_for_gene_no_task_type(self):
        """测试没有任务类型时不触发（返回False）"""
        from app.agent.control.constraint_gene import GeneEvolution
        
        state = Mock(spec=LoopState)
        state.features = None
        
        result = GeneEvolution.should_prompt_agent_for_gene(state, "", "some cue")
        self.assertFalse(result)  # 现在没有task_type返回False

    def test_should_prompt_agent_for_gene_no_avoid_cue(self):
        """测试无法提取 avoid cue 时不触发（返回False）"""
        from app.agent.control.constraint_gene import GeneEvolution
        
        state = Mock(spec=LoopState)
        state.features = None
        
        result = GeneEvolution.should_prompt_agent_for_gene(state, "test_task", None)
        self.assertFalse(result)  # 现在没有avoid_cue返回False

    def test_should_prompt_agent_for_gene_consecutive_failures(self):
        """测试连续2次失败时提示 Agent"""
        from app.agent.control.constraint_gene import GeneEvolution
        
        state = Mock(spec=LoopState)
        state.features = Mock()
        state.features.consecutive_failures = 2  # 改为2次
        
        result = GeneEvolution.should_prompt_agent_for_gene(state, "test_task", "cue")
        self.assertTrue(result)

    def test_should_not_prompt_agent_when_normal(self):
        """测试正常情况不提示 Agent（放宽条件后，只要有 task_type 和 avoid_cue 就返回 True）"""
        from app.agent.control.constraint_gene import GeneEvolution
        
        state = Mock(spec=LoopState)
        state.features = Mock()
        state.features.consecutive_failures = 1
        
        # 放宽条件后，只要有 task_type 和 avoid_cue 就返回 True
        result = GeneEvolution.should_prompt_agent_for_gene(state, "test_task", "cue")
        self.assertTrue(result)  # 放宽条件后返回 True
        
        # 没有 task_type 时返回 False
        result_no_task = GeneEvolution.should_prompt_agent_for_gene(state, "", "cue")
        self.assertFalse(result_no_task)
        
        # 没有 avoid_cue 时返回 False
        result_no_cue = GeneEvolution.should_prompt_agent_for_gene(state, "test_task", "")
        self.assertFalse(result_no_cue)

    def test_parse_agent_gene_response_with_code_block(self):
        """测试解析带代码块的 Agent 响应"""
        from app.agent.control.constraint_gene import GeneEvolution
        
        response = """```gene
[HARD CONSTRAINTS]
[任务类型]: API调试
[CONTROL ACTION]
MUST: 检查API文档
MUST NOT: 盲目重试
[AVOID]
- 忽略错误码
```"""
        
        result = GeneEvolution.parse_agent_gene_response(response)
        
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "api调试")
        self.assertIn("[HARD CONSTRAINTS]", result["content"])
        self.assertIn("API调试", result["content"])

    def test_parse_agent_gene_response_without_code_block(self):
        """测试解析不带代码块的 Agent 响应"""
        from app.agent.control.constraint_gene import GeneEvolution
        
        response = """[HARD CONSTRAINTS]
[任务类型]: 数据处理
[CONTROL ACTION]
MUST: 验证数据格式
MUST NOT: 直接处理脏数据"""
        
        result = GeneEvolution.parse_agent_gene_response(response)
        
        self.assertIsNotNone(result)
        self.assertEqual(result["task_type"], "数据处理")

    def test_parse_agent_gene_response_invalid(self):
        """测试解析无效的 Agent 响应"""
        from app.agent.control.constraint_gene import GeneEvolution
        
        response = "这是无效的响应"
        
        result = GeneEvolution.parse_agent_gene_response(response)
        
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
