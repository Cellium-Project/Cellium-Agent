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
    THRESHOLD_TRIGGER = 0.55   # 强触发阈值
    THRESHOLD_WARNING = 0.40   # 警告阈值
    THRESHOLD_DELTA = 0.12     # 恶化速度阈值
    
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
        1. score >= 0.55：强触发
        2. score > 0.45 且 delta > 0.12：快速恶化，提前触发
        """
        if score >= self.THRESHOLD_TRIGGER:
            return True
        if score > 0.45 and delta > self.THRESHOLD_DELTA:
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

        prompt_parts = [
            "[系统提示 - Gene 创建评估]",
            "",
            f"本次对话异常评分: {score:.2f}/1.0 (等级: {level})",
        ]

        if inferred_task_type:
            prompt_parts.extend([
                f"推断的任务类型: {inferred_task_type}",
                "",
                "【重要】创建 Gene 时，任务类型字段必须使用上述推断的类型，或基于对话内容自行确定一个具体的任务类型。",
            ])

        prompt_parts.extend([
            "",
            "请按以下步骤评估是否需要创建或进化 Gene：",
            "1. 使用 memory list_genes 查看是否已存在相关 Gene",
            "2. 根据本次对话的经验教训判断：",
            "   - 没有相关 Gene → 使用 memory store 创建新的",
            "   - 有相关 Gene 但不完善 → 使用 memory update 进化",
            "   - 已有 Gene 足够完善 → 无需操作",
            "",
            "【Gene 标准格式 - 必须严格遵循】",
            "content字段格式：",
            "  [HARD CONSTRAINTS]",
            "  [任务类型]: <必须填写具体的任务类型>",
            "  ",
            "  [CONTROL ACTION]",
            "  MUST: 必须执行的操作",
            "  MUST NOT: 禁止执行的操作",
            "  ",
            "  [AVOID]",
            "  - 避免事项1",
            "  - 避免事项2",
            "",
            "存储命令（必须包含所有字段）：",
            "  memory.store(title=..., content=..., schema_type=control_gene, memory_key=gene:<任务类型>)",
            "",
            "【关键要求】",
            "- content必须以[HARD CONSTRAINTS]开头",
            "- [任务类型]字段必须填写，不能为空",
            "- 必须包含[任务类型]、[CONTROL ACTION]、[AVOID]三个段落",
            "- memory_key格式必须为 gene:<任务类型>",
            "- ≤5工具调用 | content≤300token",
            "",
            "【输出约束 - 必须遵守】",
            "- 本次任务仅处理 Gene 的创建/进化/查询，不执行任何其他操作",
            "- 已创建或进化 Gene → 只输出 Gene 内容，禁止输出其他",
            "- 判断无需操作 → 只回复'无需创建Gene'，禁止输出其他",
            "- 禁止输出与 Gene 无关的任何内容（如对用户问题的回答、额外建议、闲聊等）",
            "- 禁止矛盾：创建/进化后不得再输出'无需创建Gene'",
        ])

        return "\n".join(prompt_parts)


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
