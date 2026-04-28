# -*- coding: utf-8 -*-
import pytest
from app.agent.control.gene_post_session import GenePostSessionAnalyzer
from app.agent.control.loop_state import LoopState


def create_loop_state_with_features(stuck=0, repetition=0.0):
    from dataclasses import dataclass

    @dataclass
    class MockFeatures:
        stuck_iterations: int = 0
        repetition_score: float = 0.0

    state = LoopState(max_iterations=30)
    state.features = MockFeatures(stuck_iterations=stuck, repetition_score=repetition)
    return state


class TestComplexityScore:
    def test_simple_conversation(self):
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [{"tool": "file", "success": True}]
        score = analyzer.calculate_complexity_score(tool_traces, None, 5000)
        assert 0 <= score <= 2

    def test_complex_conversation(self):
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [
            {"tool": "file", "success": False},
            {"tool": "shell", "success": True},
            {"tool": "web_search", "success": False},
            {"tool": "file", "success": True},
        ]
        score = analyzer.calculate_complexity_score(tool_traces, None, 60000)
        assert 3 <= score <= 6

    def test_high_complexity(self):
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [{"tool": "file", "success": False}] * 12
        loop_state = create_loop_state_with_features(stuck=5, repetition=0.8)
        score = analyzer.calculate_complexity_score(tool_traces, loop_state, 45000)
        assert 4 <= score <= 8


class TestShouldAnalyze:
    def test_low_scores_skip(self):
        analyzer = GenePostSessionAnalyzer()
        for score in [0, 1, 2]:
            assert analyzer.should_analyze(score) is False

    def test_high_scores_analyze(self):
        analyzer = GenePostSessionAnalyzer()
        for score in [3, 4, 5]:
            assert analyzer.should_analyze(score) is True


class TestBuildContext:
    def test_context_structure(self):
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [
            {"tool": "file", "success": False, "result": {"error": "File not found"}},
            {"tool": "shell", "success": True, "result": {"output": "done"}},
        ]
        loop_state = create_loop_state_with_features(stuck=3, repetition=0.6)

        context = analyzer.build_analysis_context(
            user_input="测试任务",
            tool_traces=tool_traces,
            loop_state=loop_state,
            final_content="任务完成"
        )

        assert context["user_input"] == "测试任务"
        assert "complexity_indicators" in context
        assert context["complexity_indicators"]["iterations"] == 2
        assert context["complexity_indicators"]["stuck_iterations"] == 3
        assert context["complexity_indicators"]["repetition_score"] == 0.6

    def test_context_without_loop_state(self):
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [{"tool": "file", "success": True}]

        context = analyzer.build_analysis_context(
            user_input="简单任务",
            tool_traces=tool_traces,
            loop_state=None,
            final_content=""
        )

        assert context["user_input"] == "简单任务"
        assert context["complexity_indicators"]["iterations"] == 1
        assert context["complexity_indicators"]["stuck_iterations"] == 0


class TestParseResponse:
    def test_valid_json_response(self):
        analyzer = GenePostSessionAnalyzer()
        response = '''
        {
            "should_create": true,
            "mode": "CREATE",
            "task_type": "file_operation",
            "reason": "复杂文件操作",
            "insights": "需要备份",
            "gene_content": "[HARD CONSTRAINTS]\\n备份文件"
        }
        '''
        result = analyzer._parse_analysis_response(response)

        assert result["should_create"] is True
        assert result["mode"] == "CREATE"
        assert result["task_type"] == "file_operation"
        assert result["gene_content"] == "[HARD CONSTRAINTS]\n备份文件"

    def test_invalid_response(self):
        analyzer = GenePostSessionAnalyzer()
        result = analyzer._parse_analysis_response("invalid json")

        assert result["should_create"] is False
        assert result["reason"] == "parse_error"
