# -*- coding: utf-8 -*-
"""
AgentLoop 集成适配器

将启发式引擎集成到 AgentLoop 中
"""

import logging
from typing import Dict, Any, List, Optional, Tuple

from app.agent.heuristics.engine import HeuristicEngine, get_heuristic_engine
from app.agent.heuristics.types import EvaluationContext, DecisionPoint, DerivedFeatures
from app.agent.heuristics.scoring import ToolScorer, ToolScore

logger = logging.getLogger(__name__)


class AgentLoopIntegration:
    """
    AgentLoop 集成适配器

    提供简洁的 API 供 AgentLoop 调用
    """

    def __init__(self, engine: HeuristicEngine = None, enable_scoring: bool = True):
        self.engine = engine or get_heuristic_engine()
        self.enable_scoring = enable_scoring
        self._scorer = ToolScorer() if enable_scoring else None
        self._session_id: str = ""

    def start_session(self, session_id: str):
        """开始新会话，重置状态"""
        self._session_id = session_id
        self.engine.reset()
        if self._scorer:
            self._scorer.reset()

    def build_context(
        self,
        session_id: str,
        iteration: int,
        max_iterations: int,
        tool_traces: List[Dict],
        user_input: str = "",
        token_usage: Dict = None,
        elapsed_ms: int = 0,
        available_tools: List[str] = None,
    ) -> EvaluationContext:
        """
        构建评估上下文

        Args:
            session_id: 会话 ID
            iteration: 当前迭代次数
            max_iterations: 最大迭代次数
            tool_traces: 工具调用追踪列表
            user_input: 用户输入
            token_usage: Token 使用情况 {"total": int, ...}
            elapsed_ms: 已用时间（毫秒）
            available_tools: 可用工具列表

        Returns:
            EvaluationContext
        """
        # 提取最近调用
        recent_calls = self._extract_recent_calls(tool_traces)

        # 提取所有调用
        all_calls = self._extract_all_calls(tool_traces)

        # 最后一次结果
        last_result = recent_calls[-1] if recent_calls else None

        context = EvaluationContext(
            session_id=session_id,
            iteration=iteration,
            max_iterations=max_iterations,
            recent_tool_calls=recent_calls,
            tool_call_history=all_calls,
            available_tools=available_tools or [],
            total_tokens_used=token_usage.get("total", 0) if token_usage else 0,
            token_budget=10000000,
            elapsed_ms=elapsed_ms,
            user_input=user_input,
            last_tool_result=last_result,
        )

        return context

    def _extract_recent_calls(self, tool_traces: List[Dict]) -> List[Dict]:
        """提取最近的工具调用"""
        if not tool_traces:
            return []

        recent = tool_traces[-10:]  # 最近 10 次
        result = []

        for trace in recent:
            call = {
                "tool_name": trace.get("tool", "unknown"),
                "arguments": trace.get("arguments", {}),
                "result": trace.get("result", {}),
                "duration_ms": trace.get("duration_ms", 0),
            }
            result.append(call)

        return result

    def _extract_all_calls(self, tool_traces: List[Dict]) -> List[Dict]:
        """提取所有工具调用"""
        if not tool_traces:
            return []

        result = []
        for trace in tool_traces:
            call = {
                "tool_name": trace.get("tool", "unknown"),
                "arguments": trace.get("arguments", {}),
                "result": trace.get("result", {}),
                "duration_ms": trace.get("duration_ms", 0),
            }
            result.append(call)

        return result

    def should_stop(
        self,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        检查是否应该停止

        Returns:
            (should_stop, reason)
        """
        return self.engine.should_stop(context, features)

    def get_warnings(
        self,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> List[str]:
        """
        获取警告列表

        Returns:
            List of warning strings
        """
        return self.engine.get_warnings(context, features)

    def get_redirect_guidance(
        self,
        context: EvaluationContext,
        features: Optional["DerivedFeatures"] = None,
    ) -> Optional[Dict]:
        """
        获取 REDIRECT 引导信息

        Returns:
            {
                "reasons": [...],           # 触发原因
                "suggestions": [...],       # 换方向建议
                "confidence": float,        # 置信度
            }
            或 None（无 REDIRECT）
        """
        return self.engine.get_redirect_guidance(context, features)

    def get_tool_scores(
        self,
        context: EvaluationContext,
        available_tools: List[str],
        features: Optional["DerivedFeatures"] = None,
    ) -> List[ToolScore]:
        """
        获取工具评分

        Args:
            context: 评估上下文
            available_tools: 可用工具列表
            features: 可选的预计算特征（避免重复提取）

        Returns:
            排序后的工具评分列表
        """
        if not self._scorer:
            return []

        if features is None:
            features = self.engine.feature_extractor.extract(context)
        return self._scorer.score_tools(context, features, available_tools)

    def get_tool_recommendations(
        self,
        context: EvaluationContext,
        available_tools: List[str],
        top_k: int = 3,
        features: Optional["DerivedFeatures"] = None,
    ) -> Optional[Dict]:
        """
        获取工具推荐（当停滞时使用）

        Args:
            context: 评估上下文
            available_tools: 可用工具列表
            top_k: 返回前 K 个推荐

        Returns:
            {
                "recommended_tools": [...],  # 推荐的工具列表
                "reasons": [...],            # 推荐原因
            }
            或 None（无推荐）
        """
        if not self._scorer or not self.enable_scoring:
            return None

        if features is None:
            features = self.engine.feature_extractor.extract(context)

        # 只有在停滞时才推荐
        if features.stuck_iterations < 1:
            return None

        scores = self._scorer.score_tools(context, features, available_tools)
        if not scores:
            return None

        # 取前 top_k 个
        top_scores = scores[:top_k]

        return {
            "recommended_tools": [
                {"name": s.tool_name, "score": s.score, "reason": s.reason}
                for s in top_scores
            ],
            "stuck_iterations": features.stuck_iterations,
        }
