# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GenePostSessionAnalyzer:

    # 权重配置
    WEIGHT_FAIL = 0.30      # 失败权重
    WEIGHT_STUCK = 0.30     # 停滞权重
    WEIGHT_REPETITION = 0.25  # 重复权重
    WEIGHT_ITERATION = 0.15   # 迭代次数权重

    # 阈值配置
    THRESHOLD_TRIGGER = 0.70   # 强触发阈值
    THRESHOLD_WARNING = 0.50   # 警告阈值
    THRESHOLD_DELTA = 0.15     # 恶化速度阈值
    
    def __init__(self):
        self._prev_score = 0.0

    def reset_score(self):
        self._prev_score = 0.0 
    
    def calculate_complexity_score(
        self,
        tool_traces: List[Dict[str, Any]],
        loop_state,
        total_time_ms: int = 0
    ) -> float:
        """计算异常评分 (0.0-1.0)
        score = w1 * fail_score + w2 * stuck_score + w3 * repetition_score + w4 * iteration_score
        """
        iterations = len(tool_traces)
        failed_count = sum(1 for t in tool_traces if not t.get("success", True))
        fail_score = min(failed_count / 4.0, 1.0)

        stuck_score = 0.0
        repetition_score = 0.0

        if loop_state and loop_state.features:
            raw_stuck = loop_state.features.stuck_iterations
            stuck_score = min(raw_stuck / 4.0, 1.0) 

            raw_repetition = min(loop_state.features.repetition_score, 1.0)
            repetition_score = raw_repetition ** 2

        iteration_score = min(iterations / 10.0, 1.0)

        score = (
            self.WEIGHT_FAIL * fail_score +
            self.WEIGHT_STUCK * stuck_score +
            self.WEIGHT_REPETITION * repetition_score +
            self.WEIGHT_ITERATION * iteration_score
        )

        return min(score, 1.0)
    
    def calculate_score_delta(self, current_score: float) -> float:
        delta = current_score - self._prev_score
        self._prev_score = current_score
        return delta

    def should_analyze(self, score: float, delta: float = 0.0) -> bool:
        """判断是否需要分析

        触发条件：
        1. score >= 0.70：强触发
        2. score > 0.55 且 delta > 0.15：快速恶化，提前触发
        """
        if score >= self.THRESHOLD_TRIGGER:
            return True
        if score > 0.55 and delta > self.THRESHOLD_DELTA:
            return True
        return False
    
    def get_complexity_level(self, score: float) -> str:
        """获取复杂度等级"""
        if score >= self.THRESHOLD_TRIGGER:
            return "high"
        elif score >= self.THRESHOLD_WARNING:
            return "warning"
        else:
            return "normal" 
    def build_agent_gene_prompt(
        self,
        user_input: str,
        tool_traces: List[Dict[str, Any]],
        loop_state,
        total_time_ms: int = 0,
        final_content: str = ""
    ) -> Optional[str]:

        score = self.calculate_complexity_score(tool_traces, loop_state, total_time_ms)
        delta = self.calculate_score_delta(score)
        level = self.get_complexity_level(score)
        
        if not self.should_analyze(score, delta):
            logger.debug(f"[GenePostSession] Score {score:.2f} (delta={delta:+.2f}) < threshold ({level}), skipping")
            return None

        if loop_state and getattr(loop_state, 'needs_agent_gene_creation', False):
            logger.debug(f"[GenePostSession] Already prompted in this session, skipping")
            return None
        
        if loop_state:
            loop_state.needs_agent_gene_creation = True

        trigger_reason = "high_score" if score >= self.THRESHOLD_TRIGGER else "rapid_deterioration"
        logger.info(f"[GenePostSession] Score {score:.2f} (delta={delta:+.2f}, reason={trigger_reason}) ({level}), prompting agent to evaluate gene creation...")

        from .constraint_gene.matcher import TaskSignalMatcher
        matched = TaskSignalMatcher.match(user_input)
        inferred_task_type = matched.get("task_type", "") if matched else ""
        
        if not inferred_task_type and tool_traces:
            last_tool = tool_traces[-1].get("tool", "")
            if last_tool:
                inferred_task_type = last_tool

        import json

        prompt_data = {
            "type": "gene_evaluation",
            "score": round(score, 2),
            "level": level,
            "task_type": inferred_task_type or "unknown",
            "steps": [
                "memory.list_genes -> 检查已有Gene",
                "无相关Gene -> memory.store(schema_type=control_gene, memory_key=gene:<task_type>)",
                "有但不完善 -> memory.update(schema_type=control_gene)",
                "已完善 -> reply 无需创建Gene"
            ],
            "gene_content_format": {
                "required_prefix": "[HARD CONSTRAINTS]",
                "required_sections": ["[任务类型]: <具体任务类型>", "[CONTROL ACTION]\nMUST: ...\nMUST NOT: ...", "[AVOID]\n- ..."],
                "max_tool_calls": 5,
                "max_content_tokens": 300
            },
            "constraint": "仅处理Gene相关操作, 禁止输出无关内容"
        }

        return json.dumps(prompt_data, ensure_ascii=False, indent=2)


def generate_gene_prompt_for_agent(
    user_input: str,
    tool_traces: List[Dict[str, Any]],
    loop_state,
    total_time_ms: int = 0,
    final_content: str = "",
    llm_engine=None
) -> Optional[str]:
    """生成给主 Agent 的 Gene 创建提示

    这是对外提供的主要接口函数

    Args:
        user_input: 用户输入
        tool_traces: 工具调用轨迹
        loop_state: 循环状态
        total_time_ms: 总执行时间（毫秒）
        final_content: 最终回复内容
        llm_engine: LLM 引擎（保留参数用于兼容，但不使用）

    Returns:
        提示字符串，如果不需要创建则返回 None
    """
    analyzer = GenePostSessionAnalyzer()
    prompt = analyzer.build_agent_gene_prompt(
        user_input=user_input,
        tool_traces=tool_traces,
        loop_state=loop_state,
        total_time_ms=total_time_ms,
        final_content=final_content
    )
    return prompt


# 保留旧函数名用于兼容（已弃用）
async def analyze_session_for_gene(
    user_input: str,
    tool_traces: List[Dict[str, Any]],
    loop_state,
    llm_engine,
    total_time_ms: int = 0,
    final_content: str = ""
):
    """已弃用：改为使用 generate_gene_prompt_for_agent"""
    prompt = generate_gene_prompt_for_agent(
        user_input=user_input,
        tool_traces=tool_traces,
        loop_state=loop_state,
        total_time_ms=total_time_ms,
        final_content=final_content,
        llm_engine=llm_engine
    )
    return prompt
