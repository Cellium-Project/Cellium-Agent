# -*- coding: utf-8 -*-

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

from app.agent.control.thought_parser import (
    ThoughtParser,
    ParsedThought,
    ThoughtStep,
    ActionType,
)

logger = logging.getLogger(__name__)


class HybridPhase(Enum):
    OBSERVE = "observe"      # 初始观察（收集信息）
    PLAN = "plan"            # 规划
    EXECUTE = "execute"      # 执行
    EVALUATE = "evaluate"    # 评估结果
    REPLAN = "replan"        # 重新规划
    DONE = "done"            # 完成


@dataclass
class Observation:
    """执行观察结果"""
    step: ThoughtStep
    success: bool
    output_summary: str = ""
    output_preview: str = ""
    matched_expectation: bool = True
    needs_replan: bool = False
    replan_reason: str = ""


@dataclass
class HybridState:
    """混合模式状态"""
    phase: HybridPhase = HybridPhase.OBSERVE  
    
    current_plan: List[ThoughtStep] = field(default_factory=list)
    executed_steps: List[Observation] = field(default_factory=list)
    pending_steps: List[ThoughtStep] = field(default_factory=list)
    
    thought: Optional[ParsedThought] = None
    iteration: int = 0
    replan_count: int = 0
    max_replans: int = 3
    
    skip_llm: bool = False
    direct_response: str = ""
    needs_clarification: bool = False
    
    last_observation: Optional[Observation] = None
    initial_observation_done: bool = False 


class HybridController:
    
    def __init__(
        self,
        max_plan_steps: int = 3,
        max_replans: int = 3,
        observe_after_each_step: bool = True,
        auto_continue_on_success: bool = True,
    ):
        self.max_plan_steps = max_plan_steps
        self.max_replans = max_replans
        self.observe_after_each_step = observe_after_each_step
        self.auto_continue_on_success = auto_continue_on_success
        self._state = HybridState()
    
    @property
    def state(self) -> HybridState:
        return self._state
    
    def reset(self):
        """重置状态"""
        self._state = HybridState()
    
    def process_thought(self, content: str) -> ParsedThought:
        """
        处理模型的思考输出
        
        Args:
            content: 模型输出内容
            
        Returns:
            解析后的思考
        """
        thought = ThoughtParser.parse(content)
        self._state.thought = thought
        
        if thought.action == ActionType.DIRECT_RESPONSE:
            self._state.phase = HybridPhase.DONE
            self._state.skip_llm = True
            self._state.direct_response = content
            logger.info("[Hybrid] 模型判断可直接回答")
            return thought
        
        if thought.action == ActionType.CLARIFY:
            self._state.phase = HybridPhase.DONE
            self._state.skip_llm = True
            self._state.needs_clarification = True
            logger.info("[Hybrid] 需要用户澄清")
            return thought
        
        if self._state.phase == HybridPhase.OBSERVE and not self._state.initial_observation_done:
            if thought.plan:
                self._state.current_plan = thought.plan[:1]  
                self._state.pending_steps = list(self._state.current_plan)
                self._state.phase = HybridPhase.EXECUTE
                logger.info(
                    "[Hybrid] 初始观察: %s",
                    [s.tool for s in self._state.current_plan]
                )
            return thought
        
        if thought.plan:
            # 局部重规划：保留已执行的步骤，将新计划追加到后面
            if self._state.phase == HybridPhase.REPLAN and self._state.executed_steps:

                executed_count = len(self._state.executed_steps)
                if executed_count > len(self._state.current_plan):
                    executed_count = len(self._state.current_plan)

                new_plan = thought.plan[:self.max_plan_steps]
                self._state.current_plan = self._state.current_plan[:executed_count] + new_plan
                self._state.pending_steps = list(new_plan) 
                self._state.phase = HybridPhase.EXECUTE
                logger.info(
                    "[Hybrid] 局部重规划完成: 保留 %d 步 | 新增 %d 步 | 总计 %d 步 | %s",
                    executed_count,
                    len(new_plan),
                    len(self._state.current_plan),
                    [s.tool for s in new_plan]
                )
            else:

                self._state.current_plan = thought.plan[:self.max_plan_steps]
                self._state.pending_steps = list(self._state.current_plan)
                self._state.phase = HybridPhase.EXECUTE
                logger.info(
                    "[Hybrid] 计划已建立: %d 步 | %s",
                    len(self._state.current_plan),
                    [s.tool for s in self._state.current_plan]
                )
        else:
            logger.warning("[Hybrid] 未解析到计划")
            self._state.phase = HybridPhase.EXECUTE
        
        return thought
    
    def get_next_step(self) -> Optional[ThoughtStep]:
        """获取下一个要执行的步骤"""
        if self._state.phase not in (HybridPhase.EXECUTE, HybridPhase.REPLAN):
            return None
        
        if not self._state.pending_steps:
            self._state.phase = HybridPhase.DONE
            return None
        
        return self._state.pending_steps[0]
    
    def observe_result(
        self,
        step: ThoughtStep,
        success: bool,
        output: Any,
    ) -> Observation:
        output_str = self._summarize_output(output)
        output_preview = output_str[:200] if len(output_str) > 200 else output_str
        
        matched = self._check_expectation(step, success, output_str)
        needs_replan = not matched or not success
        
        obs = Observation(
            step=step,
            success=success,
            output_summary=output_str[:500],
            output_preview=output_preview,
            matched_expectation=matched,
            needs_replan=needs_replan,
            replan_reason="" if matched else self._get_replan_reason(step, success, output_str),
        )
        
        self._state.last_observation = obs
        self._state.executed_steps.append(obs)
        
        if self._state.pending_steps:
            self._state.pending_steps.pop(0)
        
        if not self._state.initial_observation_done:
            self._state.initial_observation_done = True
            self._state.phase = HybridPhase.PLAN
            logger.info(
                "[Hybrid] 初始观察完成 | 结果: %s | 进入规划阶段",
                output_preview[:100] if output_preview else "(空)"
            )
            return obs
        
        if needs_replan and self._state.replan_count < self.max_replans:
            self._state.phase = HybridPhase.REPLAN
            self._state.replan_count += 1

            failed_step_index = len(self._state.executed_steps) - 1
            self._state.pending_steps = []

            logger.warning(
                "[Hybrid] 局部重规划 | 原因: %s | replan_count: %d | 已执行 %d 步 | 保留成功步骤",
                obs.replan_reason, self._state.replan_count, failed_step_index
            )
        elif not self._state.pending_steps:
            self._state.phase = HybridPhase.DONE
            logger.info("[Hybrid] 计划执行完成")
        else:
            self._state.phase = HybridPhase.EXECUTE
            logger.info("[Hybrid] 继续执行下一步")
        
        return obs
    
    def _summarize_output(self, output: Any) -> str:
        if output is None:
            return "(空)"
        
        if isinstance(output, dict):
            if "error" in output:
                return f"错误: {output['error']}"
            if "result" in output:
                return str(output["result"])[:500]
            return str(output)[:500]
        
        return str(output)[:500]
    
    def _check_expectation(self, step: ThoughtStep, success: bool, output: str) -> bool:
        """
        智能验证执行结果是否符合预期
        
        策略：
        1. 执行失败 → 直接返回 False
        2. 无预期结果 → 返回 True（无需验证）
        3. 语义匹配：使用多种启发式规则判断
        """
        if not success:
            return False
        
        if not step.expected_result:
            return True
        
        expected = step.expected_result.lower().strip()
        actual = output.lower().strip()
        
        # 规则1：直接包含（子串匹配）
        if expected in actual or actual in expected:
            return True
        
        # 规则2：关键词集合匹配（Jaccard 相似度）
        expected_words = set(expected.split())
        actual_words = set(actual.split())
        
        if expected_words and actual_words:
            intersection = expected_words & actual_words
            union = expected_words | actual_words
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard >= 0.5:  # 50% 以上的词重叠
                return True
        
        # 规则3：关键模式匹配（处理命名变体如 get_X vs X）
        # 提取字母数字字符进行模糊匹配
        import re
        expected_chars = set(re.findall(r'[a-z0-9]+', expected))
        actual_chars = set(re.findall(r'[a-z0-9]+', actual))
        
        if expected_chars and actual_chars:
            # 检查是否有共同的核心标识符
            common = expected_chars & actual_chars
            # 如果预期中的核心词在实际结果中出现超过一半
            if len(common) >= len(expected_chars) * 0.5:
                return True
        
        # 规则4：目的导向验证（使用 step.purpose）
        # 如果 purpose 包含在结果中，认为成功
        if step.purpose:
            purpose_keywords = set(step.purpose.lower().split())
            actual_words = set(actual.split())
            if purpose_keywords:
                purpose_match = len(purpose_keywords & actual_words) / len(purpose_keywords)
                if purpose_match >= 0.6:  # 60% 以上的目的关键词出现
                    return True
        
        return False
    
    def _get_replan_reason(self, step: ThoughtStep, success: bool, output: str) -> str:
        if not success:
            return f"工具 {step.tool} 执行失败"
        
        if step.expected_result:
            return f"结果不符合预期: 期望 '{step.expected_result}'"
        
        return "需要根据新信息调整计划"
    
    def should_call_llm(self) -> bool:
        if self._state.skip_llm:
            return False
        
        if self._state.phase == HybridPhase.PLAN:
            return True
        
        if self._state.phase == HybridPhase.REPLAN:
            return True
        
        if self._state.phase == HybridPhase.DONE:
            return False
        
        return False
    
    def should_execute_tool(self) -> bool:
        return self._state.phase == HybridPhase.EXECUTE and bool(self._state.pending_steps)
    
    def is_done(self) -> bool:
        return self._state.phase == HybridPhase.DONE
    
    def get_phase_message(self) -> Dict[str, Any]:
        phase_info = {
            HybridPhase.OBSERVE: {
                "icon": "",
                "message": "观察中",
                "description": "收集信息...",
            },
            HybridPhase.PLAN: {
                "icon": "",
                "message": "规划中",
                "description": "基于观察结果制定计划...",
            },
            HybridPhase.EXECUTE: {
                "icon": "",
                "message": "执行中",
                "description": f"执行计划 ({len(self._state.executed_steps)}/{len(self._state.current_plan)})",
            },
            HybridPhase.EVALUATE: {
                "icon": "",
                "message": "评估中",
                "description": "评估执行结果...",
            },
            HybridPhase.REPLAN: {
                "icon": "",
                "message": "重新规划",
                "description": f"第 {self._state.replan_count} 次调整计划",
            },
            HybridPhase.DONE: {
                "icon": "",
                "message": "完成",
                "description": "计划执行完成",
            },
        }
        
        info = phase_info.get(self._state.phase, {
            "icon": "",
            "message": "未知",
            "description": "",
        })
        
        detail = ""
        if self._state.current_plan:
            current_tool = self._state.current_plan[0].tool if self._state.current_plan else ""
            detail = current_tool
        
        if self._state.last_observation:
            detail = self._state.last_observation.replan_reason or detail
        
        return {
            "phase": self._state.phase.value,
            "icon": info["icon"],
            "message": info["message"],
            "description": info["description"],
            "detail": detail[:50] if detail else "",
            "plan_steps": len(self._state.current_plan),
            "executed_steps": len(self._state.executed_steps),
            "replan_count": self._state.replan_count,
        }
    
    def sync_to_loop_state(self, loop_state: Any) -> None:
        loop_state.hybrid_phase = self._state.phase.value
        loop_state.hybrid_replan_count = self._state.replan_count
        loop_state.hybrid_plan_steps = len(self._state.current_plan)
        loop_state.hybrid_executed_steps = len(self._state.executed_steps)
        loop_state.hybrid_needs_replan = self._state.phase == HybridPhase.REPLAN
        
        if self._state.last_observation:
            loop_state.hybrid_last_observation = self._state.last_observation.replan_reason or "ok"
        else:
            loop_state.hybrid_last_observation = None
        
        # 计划摘要：工具列表
        if self._state.current_plan:
            tools = [s.tool.split(":")[-1] for s in self._state.current_plan]
            loop_state.hybrid_plan_summary = f"{len(tools)}步: " + " -> ".join(tools)
        else:
            loop_state.hybrid_plan_summary = ""
    
    def get_context_for_replan(self) -> Dict[str, Any]:
        executed = [
            {
                "tool": obs.step.tool,
                "purpose": obs.step.purpose,
                "success": obs.success,
                "result_preview": obs.output_preview,
            }
            for obs in self._state.executed_steps
        ]
        
        remaining = [
            {"tool": s.tool, "purpose": s.purpose}
            for s in self._state.pending_steps
        ]
        
        return {
            "executed_steps": executed,
            "remaining_plan": remaining,
            "replan_count": self._state.replan_count,
            "last_observation": {
                "success": self._state.last_observation.success if self._state.last_observation else None,
                "reason": self._state.last_observation.replan_reason if self._state.last_observation else None,
            } if self._state.last_observation else None,
        }
    
    def get_execution_summary(self) -> Dict[str, Any]:
        total = len(self._state.executed_steps)
        success = sum(1 for o in self._state.executed_steps if o.success)
        
        return {
            "total_steps": total,
            "success_steps": success,
            "failed_steps": total - success,
            "replan_count": self._state.replan_count,
            "phase": self._state.phase.value,
            "plan_completed": not self._state.pending_steps,
        }


def create_hybrid_controller(
    max_plan_steps: int = 3,
    max_replans: int = 3,
) -> HybridController:
    return HybridController(
        max_plan_steps=max_plan_steps,
        max_replans=max_replans,
    )
