# -*- coding: utf-8 -*-
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

from .constraint_gene import GeneEvolution, TaskSignalMatcher

logger = logging.getLogger(__name__)


class GenePostSessionAnalyzer:
    def __init__(self, llm_engine=None):
        self.llm = llm_engine
    
    def calculate_complexity_score(
        self,
        tool_traces: List[Dict[str, Any]],
        loop_state,
        total_time_ms: int = 0
    ) -> int:
        score = 0
        
        iterations = len(tool_traces)
        if iterations > 5:
            score += 1
        if iterations > 10:
            score += 1
        
        failed_tools = [t for t in tool_traces if not t.get("success", True)]
        if failed_tools:
            score += 2
        
        unique_tools = set(t.get("tool") for t in tool_traces if t.get("tool"))
        if len(unique_tools) >= 3:
            score += 1
        
        if loop_state and loop_state.features:
            if loop_state.features.stuck_iterations > 2:
                score += 1
            if loop_state.features.repetition_score > 0.5:
                score += 1
        
        if total_time_ms > 30000:
            score += 1
        
        return score
    
    def should_analyze(self, score: int) -> bool:
        return score >= 3
    
    def build_analysis_context(
        self,
        user_input: str,
        tool_traces: List[Dict[str, Any]],
        loop_state,
        final_content: str = ""
    ) -> Dict[str, Any]:
        indicators = {
            "iterations": len(tool_traces),
            "stuck_iterations": 0,
            "repetition_score": 0.0,
            "had_redirect": False
        }
        
        if loop_state:
            if loop_state.features:
                indicators["stuck_iterations"] = loop_state.features.stuck_iterations
                indicators["repetition_score"] = loop_state.features.repetition_score
            if loop_state.decision_trace:
                indicators["had_redirect"] = any(
                    d.action_type == "redirect" for d in loop_state.decision_trace
                )
        
        return {
            "user_input": user_input,
            "final_response": final_content[:200],
            "complexity_indicators": indicators
        }
    
    async def analyze_with_llm(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm:
            return {"should_create": False, "reason": "no_llm"}
        
        prompt = self._build_analysis_prompt(context)
        
        try:
            response = await self.llm.ainvoke(prompt)
            return self._parse_analysis_response(response.content if hasattr(response, 'content') else str(response))
        except Exception as e:
            logger.error(f"[GenePostSession] LLM analysis failed: {e}")
            return {"should_create": False, "reason": "llm_error"}
    
    def _build_analysis_prompt(self, context: Dict[str, Any]) -> str:
        user_input = context.get("user_input", "")
        indicators = context.get("complexity_indicators", {})
        
        all_genes = GeneEvolution._get_all_genes()
        
        prompt = f"""基于对话历史，判断是否需要创建或进化 Gene。

当前任务: {user_input}

执行统计:
- 迭代次数: {indicators.get('iterations', 0)}
- 停滞轮数: {indicators.get('stuck_iterations', 0)}
- 重复分数: {indicators.get('repetition_score', 0):.2f}
- 是否触发 redirect: {indicators.get('had_redirect', False)}
"""
        
        if all_genes:
            prompt += "\n现有 Gene 列表:\n"
            for gene in all_genes[:10]:
                prompt += f"  - [{gene['task_type']}] {gene['title']}\n"
            prompt += "\n请判断当前任务与哪个 Gene 最相关，选择 CREATE 新建或 EVOLVE 进化现有 Gene。\n"
        else:
            prompt += "\n当前无现有 Gene。\n"
        
        prompt += """
请判断:
1. 这个对话是否值得记录为 Gene？（复杂任务、失败经验、成功模式）
2. 如果是，应该创建新 Gene 还是进化现有 Gene？
3. 任务类型是什么？
4. Gene 内容应该包含哪些约束？

以 JSON 格式回复:
{
  "should_create": true/false,
  "mode": "CREATE" or "EVOLVE",
  "task_type": "任务类型标识",
  "reason": "简要原因",
  "insights": "关键洞察",
  "gene_content": "完整的gene内容，包含 [HARD CONSTRAINTS]、[CONTROL ACTION]、[AVOID] 等部分"
}
"""
        return prompt
    
    def _parse_analysis_response(self, response: str) -> Dict[str, Any]:
        import json
        import re
        
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return {
                    "should_create": data.get("should_create", False),
                    "mode": data.get("mode", "CREATE"),
                    "task_type": data.get("task_type", ""),
                    "reason": data.get("reason", ""),
                    "insights": data.get("insights", ""),
                    "gene_content": data.get("gene_content", "")
                }
            except:
                pass
        
        return {"should_create": False, "reason": "parse_error"}
    
    async def create_or_evolve_gene(
        self,
        analysis: Dict[str, Any],
        user_input: str,
        loop_state
    ) -> bool:
        if not analysis.get("should_create"):
            return False
        
        task_type = analysis.get("task_type", "")
        mode = analysis.get("mode", "CREATE")
        gene_content = analysis.get("gene_content", "")
        
        if not task_type:
            return False
        
        try:
            if mode == "EVOLVE" and gene_content:
                existing = GeneEvolution._get_existing_gene(user_input)
                if existing:
                    success = GeneEvolution._evolve_existing_gene(
                        task_type=task_type,
                        avoid_cue=gene_content,
                        state=loop_state
                    )
                    if success:
                        logger.info(f"[GenePostSession] Evolved gene: {task_type}")
                    return success
            
            if gene_content:
                gene_data = {
                    "task_type": task_type,
                    "content": gene_content,
                    "signals": [task_type],
                    "forbidden_tools": [],
                    "preferred_tools": [],
                    "source": "post_session_created",
                    "mode": mode.lower()
                }
                success = GeneEvolution.save_agent_created_gene(gene_data)
                if success:
                    logger.info(f"[GenePostSession] Created gene: {task_type}")
                return success
            else:
                success = await GeneEvolution.create_gene_with_llm(
                    user_input=user_input,
                    state=loop_state,
                    llm_engine=self.llm
                )
                if success:
                    logger.info(f"[GenePostSession] Created gene via LLM: {task_type}")
                return success
            
        except Exception as e:
            logger.error(f"[GenePostSession] Create/evolve failed: {e}")
            return False
    
    async def analyze_and_create(
        self,
        user_input: str,
        tool_traces: List[Dict[str, Any]],
        loop_state,
        total_time_ms: int = 0,
        final_content: str = ""
    ):
        score = self.calculate_complexity_score(tool_traces, loop_state, total_time_ms)
        
        if not self.should_analyze(score):
            logger.debug(f"[GenePostSession] Score {score} < 3, skipping")
            return
        
        # 检查是否已存在相似的 Gene（避免与运行时 Agent 创建重复）
        existing = GeneEvolution._get_existing_gene(user_input)
        if existing and existing.get("score", 0) > 0.85:
            logger.debug(f"[GenePostSession] Similar gene already exists: {existing.get('task_type')}, skipping")
            return
        
        # 检查 loop_state 是否标记了需要 Agent 创建 Gene（避免重复）
        if loop_state and getattr(loop_state, 'needs_agent_gene_creation', False):
            logger.debug(f"[GenePostSession] Agent will create gene, skipping post-session analysis")
            return
        
        logger.info(f"[GenePostSession] Score {score} >= 3, analyzing...")
        
        context = self.build_analysis_context(user_input, tool_traces, loop_state, final_content)
        analysis = await self.analyze_with_llm(context)
        
        if analysis.get("should_create"):
            asyncio.create_task(
                self.create_or_evolve_gene(analysis, user_input, loop_state)
            )
            logger.info(f"[GenePostSession] Triggered gene creation: {analysis.get('task_type')}")
        else:
            logger.debug(f"[GenePostSession] LLM decided not to create gene: {analysis.get('reason')}")


async def analyze_session_for_gene(
    user_input: str,
    tool_traces: List[Dict[str, Any]],
    loop_state,
    llm_engine,
    total_time_ms: int = 0,
    final_content: str = ""
):
    analyzer = GenePostSessionAnalyzer(llm_engine)
    await analyzer.analyze_and_create(
        user_input=user_input,
        tool_traces=tool_traces,
        loop_state=loop_state,
        total_time_ms=total_time_ms,
        final_content=final_content
    )
