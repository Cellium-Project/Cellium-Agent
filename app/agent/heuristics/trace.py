# -*- coding: utf-8 -*-
"""
决策追踪
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RuleTrace:
    """规则评估追踪"""
    rule_id: str
    matched: bool
    action: str
    score: float
    reason: str


@dataclass
class DecisionTrace:
    """决策追踪记录"""
    trace_id: str
    session_id: str
    timestamp: str
    decision_point: str
    context_snapshot: Dict[str, Any]
    features_snapshot: Dict[str, Any]
    rules_evaluated: List[RuleTrace]
    final_decision: Dict[str, Any]
    total_duration_ms: float


class TraceRecorder:
    """决策追踪记录器"""

    def __init__(self, trace_dir: str = None, enabled: bool = True):
        """
        Args:
            trace_dir: 追踪文件存储目录
            enabled: 是否启用追踪
        """
        self.enabled = enabled
        self.trace_dir = trace_dir

        if trace_dir and enabled:
            os.makedirs(trace_dir, exist_ok=True)

    def record(
        self,
        point: str,
        context: Any,
        results: List[tuple],
        fused: Any,
        features: Any,
    ) -> Optional[str]:
        """
        记录决策追踪

        Args:
            point: 决策点
            context: 评估上下文
            results: 规则评估结果列表 [(rule, result), ...]
            fused: 融合决策
            features: 派生特征

        Returns:
            trace_id 或 None
        """
        if not self.enabled:
            return None

        trace_id = f"trace-{uuid.uuid4().hex[:8]}"

        trace = DecisionTrace(
            trace_id=trace_id,
            session_id=getattr(context, "session_id", "unknown"),
            timestamp=datetime.now().isoformat(),
            decision_point=str(point),
            context_snapshot={
                "iteration": getattr(context, "iteration", 0),
                "max_iterations": getattr(context, "max_iterations", 0),
                "recent_tools": [
                    c.get("tool_name", c.get("tool", "unknown"))
                    for c in getattr(context, "recent_tool_calls", [])[-5:]
                ],
            },
            features_snapshot=asdict(features) if hasattr(features, "__dataclass_fields__") else {},
            rules_evaluated=[
                RuleTrace(
                    rule_id=r.id,
                    matched=res.matched,
                    action=res.action.value if hasattr(res.action, "value") else str(res.action),
                    score=res.score,
                    reason=res.reason[:100] if res.reason else "",
                )
                for r, res in results
            ],
            final_decision={
                "action": fused.action.value if fused and hasattr(fused, "action") else None,
                "confidence": fused.confidence if fused else None,
                "reasons": fused.reasons if fused else [],
            },
            total_duration_ms=0,
        )

        if self.trace_dir:
            self._write_trace(trace)

        return trace_id

    def _write_trace(self, trace: DecisionTrace):
        """写入追踪文件"""
        try:
            filename = f"{trace.session_id}_{trace.trace_id}.json"
            filepath = os.path.join(self.trace_dir, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(asdict(trace), f, ensure_ascii=False, indent=2)

            logger.debug("[TraceRecorder] 追踪已写入: %s", filepath)

        except Exception as e:
            logger.warning("[TraceRecorder] 写入追踪失败: %s", e)

    def reset(self):
        """重置记录器状态"""
        pass
