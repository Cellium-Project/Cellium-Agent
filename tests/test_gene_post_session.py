# -*- coding: utf-8 -*-
import pytest
from app.agent.control.gene_post_session import (
    GenePostSessionAnalyzer,
    generate_gene_prompt_for_agent,
)
from app.agent.control.loop_state import LoopState


def create_loop_state_with_features(stuck=0, repetition=0.0, error_rate=0.0, context_saturation=0.0):
    from dataclasses import dataclass

    @dataclass
    class MockFeatures:
        stuck_iterations: int = 0
        repetition_score: float = 0.0
        error_rate: float = 0.0
        context_saturation: float = 0.0

    state = LoopState(max_iterations=30)
    state.features = MockFeatures(
        stuck_iterations=stuck,
        repetition_score=repetition,
        error_rate=error_rate,
        context_saturation=context_saturation
    )
    return state


class TestComplexityScore:
    def test_simple_conversation(self):
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [{"tool": "file", "success": True}]
        score = analyzer.calculate_complexity_score(tool_traces, None, 5000)
        assert 0 <= score <= 0.2  # 1轮迭代，低分

    def test_fail_dominant(self):
        """失败主导型评分"""
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [
            {"tool": "file", "success": False},
            {"tool": "shell", "success": False},
            {"tool": "web_search", "success": False},
        ]
        score = analyzer.calculate_complexity_score(tool_traces, None, 60000)
        # 3次失败 + 3轮迭代: 0.3*0.75 + 0.15*0.3 = 0.225 + 0.045 = 0.27
        assert 0.2 <= score <= 0.35

    def test_stuck_dominant_no_fail(self):
        """纯停滞主导型"""
        analyzer = GenePostSessionAnalyzer()
        tool_traces = [{"tool": "file", "success": True}]
        loop_state = create_loop_state_with_features(stuck=4, repetition=0.0)
        score = analyzer.calculate_complexity_score(tool_traces, loop_state, 60000)
        # 4次停滞: 0.3*1.0 = 0.3
        assert 0.25 <= score <= 0.35

    def test_stuck_with_fail_independent(self):
        """停滞+失败独立计算（修复负相关）"""
        analyzer = GenePostSessionAnalyzer()
        # 3次失败 + stuck=4: fail=0.75, stuck=1.0, iteration=0.7
        # score = 0.3*0.75 + 0.3*1.0 + 0.15*0.7 = 0.225 + 0.3 + 0.105 = 0.63
        tool_traces = [
            {"tool": "file", "success": False},
            {"tool": "shell", "success": False},
            {"tool": "web_search", "success": False},
        ]
        loop_state = create_loop_state_with_features(stuck=4, repetition=0.0)
        score = analyzer.calculate_complexity_score(tool_traces, loop_state, 60000)
        assert 0.55 <= score <= 0.70

    def test_repetition_low(self):
        analyzer = GenePostSessionAnalyzer()
        # repetition=0.5: squared=0.25, score=0.25*0.25=0.0625
        tool_traces = [{"tool": "file", "success": True}]
        loop_state = create_loop_state_with_features(stuck=0, repetition=0.5)
        score = analyzer.calculate_complexity_score(tool_traces, loop_state, 60000)
        assert 0.0 <= score <= 0.15

    def test_repetition_high(self):
        analyzer = GenePostSessionAnalyzer()
        # repetition=0.9: squared=0.81, score=0.25*0.81=0.20
        tool_traces = [{"tool": "file", "success": True}]
        loop_state = create_loop_state_with_features(stuck=0, repetition=0.9)
        score = analyzer.calculate_complexity_score(tool_traces, loop_state, 60000)
        assert 0.18 <= score <= 0.25  # 强惩罚

    def test_combined_high_score(self):
        analyzer = GenePostSessionAnalyzer()
        # 4次失败 + stuck=4 + rep=0.9 + 4轮迭代
        # fail=1.0, stuck=1.0, rep=0.81, iter=0.4
        # score = 0.3*1.0 + 0.3*1.0 + 0.25*0.81 + 0.15*0.4 = 0.3+0.3+0.20+0.06 = 0.86
        tool_traces = [
            {"tool": "file", "success": False},
            {"tool": "shell", "success": False},
            {"tool": "web_search", "success": False},
            {"tool": "code", "success": False},
        ]
        loop_state = create_loop_state_with_features(stuck=4, repetition=0.9)
        score = analyzer.calculate_complexity_score(tool_traces, loop_state, 60000)
        assert 0.80 <= score <= 0.95

    def test_combined_warning_level(self):
        analyzer = GenePostSessionAnalyzer()
        # 3次失败 + stuck=3 + rep=0.7 + 3轮迭代
        # fail=0.75, stuck=0.75, rep=0.49, iter=0.3
        # score = 0.3*0.75 + 0.3*0.75 + 0.25*0.49 + 0.15*0.3 = 0.225+0.225+0.122+0.045 = 0.617
        tool_traces = [
            {"tool": "file", "success": False},
            {"tool": "shell", "success": False},
            {"tool": "web_search", "success": False},
        ]
        loop_state = create_loop_state_with_features(stuck=3, repetition=0.7)
        score = analyzer.calculate_complexity_score(tool_traces, loop_state, 60000)
        assert 0.55 <= score <= 0.70


class TestScoreDelta:
    def test_delta_calculation(self):
        analyzer = GenePostSessionAnalyzer()

        delta1 = analyzer.calculate_score_delta(0.3)
        assert abs(delta1 - 0.3) < 0.001

        delta2 = analyzer.calculate_score_delta(0.5)
        assert abs(delta2 - 0.2) < 0.001

        delta3 = analyzer.calculate_score_delta(0.4)
        assert abs(delta3 - (-0.1)) < 0.001  # 降了0.1


class TestShouldAnalyze:
    def test_high_score_trigger(self):
        analyzer = GenePostSessionAnalyzer()
        # 新阈值 0.55
        assert analyzer.should_analyze(0.55, 0.0) is True
        assert analyzer.should_analyze(0.7, 0.0) is True
        assert analyzer.should_analyze(1.0, 0.0) is True

    def test_rapid_deterioration_trigger(self):
        """快速恶化提前触发"""
        analyzer = GenePostSessionAnalyzer()
        # score > 0.45 且 delta > 0.12
        assert analyzer.should_analyze(0.50, 0.15) is True
        assert analyzer.should_analyze(0.55, 0.12) is True

    def test_no_trigger_low_score(self):
        """低分不触发"""
        analyzer = GenePostSessionAnalyzer()
        assert analyzer.should_analyze(0.4, 0.0) is False
        assert analyzer.should_analyze(0.5, 0.0) is False  # 刚好0.5不触发
        assert analyzer.should_analyze(0.54, 0.0) is False

    def test_no_trigger_slow_deterioration(self):
        """缓慢恶化不提前触发"""
        analyzer = GenePostSessionAnalyzer()
        assert analyzer.should_analyze(0.50, 0.10) is False
        # 0.55 >= THRESHOLD_TRIGGER (0.55)，所以会触发，不是缓慢恶化问题
        # assert analyzer.should_analyze(0.55, 0.05) is False


class TestComplexityLevel:
    def test_level_normal(self):
        analyzer = GenePostSessionAnalyzer()
        assert analyzer.get_complexity_level(0.3) == "normal"
        assert analyzer.get_complexity_level(0.39) == "normal"  # 新阈值 0.4

    def test_level_warning(self):
        analyzer = GenePostSessionAnalyzer()
        assert analyzer.get_complexity_level(0.40) == "warning"  # 新阈值 0.4
        assert analyzer.get_complexity_level(0.54) == "warning"  # 新阈值 0.55

    def test_level_high(self):
        analyzer = GenePostSessionAnalyzer()
        assert analyzer.get_complexity_level(0.55) == "high"  # 新阈值 0.55
        assert analyzer.get_complexity_level(1.0) == "high"


class TestBuildAgentGenePrompt:
    def test_prompt_generation_high_score(self):
        analyzer = GenePostSessionAnalyzer()
        # 4次失败 + stuck=4 + rep=0.9 = 高分
        tool_traces = [
            {"tool": "file", "success": False},
            {"tool": "shell", "success": False},
            {"tool": "web_search", "success": False},
            {"tool": "code", "success": False},
        ]
        loop_state = create_loop_state_with_features(stuck=4, repetition=0.9)

        prompt = analyzer.build_agent_gene_prompt(
            user_input="测试复杂任务",
            tool_traces=tool_traces,
            loop_state=loop_state,
            total_time_ms=35000,
            final_content="任务完成"
        )

        assert prompt is not None
        assert "[系统提示 - Gene 创建评估]" in prompt
        assert "异常评分:" in prompt
        assert "[HARD CONSTRAINTS]" in prompt

    def test_prompt_generation_low_score(self):
        analyzer = GenePostSessionAnalyzer()
        # 1次失败 + stuck=1 + rep=0.2 = 低分
        tool_traces = [
            {"tool": "file", "success": False},
        ]
        loop_state = create_loop_state_with_features(stuck=1, repetition=0.2)

        prompt = analyzer.build_agent_gene_prompt(
            user_input="测试简单任务",
            tool_traces=tool_traces,
            loop_state=loop_state,
            total_time_ms=5000,
            final_content="任务完成"
        )

        assert prompt is None  # 低分不生成提示


class TestGenePostSessionIntegration:
    def test_end_to_end_high_complexity(self):
        """端到端测试：高复杂度场景"""
        tool_traces = [
            {"tool": "file", "success": False},
            {"tool": "shell", "success": False},
            {"tool": "web_search", "success": False},
            {"tool": "code", "success": False},
        ]
        loop_state = create_loop_state_with_features(stuck=4, repetition=0.9)

        prompt = generate_gene_prompt_for_agent(
            user_input="帮我处理这个复杂任务",
            tool_traces=tool_traces,
            loop_state=loop_state,
            total_time_ms=60000,
            final_content="任务最终输出"
        )

        assert prompt is not None
        assert "Gene 创建评估" in prompt

    def test_end_to_end_low_complexity(self):
        """端到端测试：低复杂度场景"""
        tool_traces = [{"tool": "file", "success": True}]
        loop_state = create_loop_state_with_features(stuck=0, repetition=0.1)

        prompt = generate_gene_prompt_for_agent(
            user_input="简单问题",
            tool_traces=tool_traces,
            loop_state=loop_state,
            total_time_ms=3000,
            final_content="回答"
        )

        assert prompt is None  # 简单任务不触发


class TestScoreDeltaTrigger:
    def test_rapid_deterioration_detection(self):
        """测试快速恶化检测"""
        analyzer = GenePostSessionAnalyzer()

        # 第一轮：低分
        score1 = 0.3
        delta1 = analyzer.calculate_score_delta(score1)
        assert analyzer.should_analyze(score1, delta1) is False

        # 第二轮：快速恶化到 0.5
        score2 = 0.50
        delta2 = analyzer.calculate_score_delta(score2)
        assert delta2 == 0.20  # 恶化了0.2
        assert analyzer.should_analyze(score2, delta2) is True  # >0.45 且 delta>0.12

    def test_slow_deterioration_no_trigger(self):
        """缓慢恶化不应触发"""
        analyzer = GenePostSessionAnalyzer()

        scores = [0.3, 0.35, 0.40, 0.45]
        for score in scores:
            delta = analyzer.calculate_score_delta(score)
            # 缓慢恶化，delta 都很小
            assert analyzer.should_analyze(score, delta) is False
