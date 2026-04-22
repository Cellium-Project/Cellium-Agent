# -*- coding: utf-8 -*-
"""
DecisionRenderer - 控制决策渲染器

将 ControlDecision 转换为 LLM 可理解的提示词。

核心职责：
  1. 将决策语义转换为自然语言提示
  2. 支持多种注入方式（system/user/context）
  3. 保持提示词的可读性和可操作性
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .loop_state import ControlDecision


@dataclass
class RenderedPrompt:
    """渲染后的提示词参数"""

    guidance_message: Optional[str] = None
    """User Message 注入的引导消息"""

    system_injection: Optional[str] = None
    """System Prompt 注入的约束信息"""

    context_modifier: Optional[str] = None
    """Context 修饰符（直接修改输入上下文）"""

    force_stop: bool = False
    """是否强制终止"""

    suggested_tools: List[str] = None
    """推荐的工具列表"""

    def __post_init__(self):
        if self.suggested_tools is None:
            self.suggested_tools = []


class DecisionRenderer:
    """
    决策渲染器
    """

    # 提示词模板
    REDIRECT_TEMPLATE = """## §方向调整建议

检测到当前执行可能陷入困境：

**问题原因：**
{reasons}

**建议：**
- 尝试换一个工具或方法
- 回顾之前的步骤，确认是否有遗漏
{tools_section}"""

    COMPRESS_TEMPLATE = """## §上下文压力警告

当前对话上下文接近 Token 限制，请：
- 精简后续回复，避免重复已有信息
- 优先使用关键信息，减少冗余描述
- 必要时主动请求结束任务并总结"""

    TERMINATE_TEMPLATE = """## 🛑 终止信号

系统检测到任务无法继续：**{stop_reason}**

请立即：
1. 总结当前已完成的工作
2. 说明未能完成的部分及原因
3. 给出用户可操作的后续建议"""

    def __init__(self, verbose: bool = False):
        """
        初始化渲染器

        Args:
            verbose: 是否生成详细提示（调试用）
        """
        self.verbose = verbose

    def render(self, decision: ControlDecision) -> RenderedPrompt:
        """
        渲染决策为提示词参数

        Args:
            decision: 控制决策

        Returns:
            渲染后的提示词参数
        """
        result = RenderedPrompt()

        if decision.action_type == "continue":
            if self.verbose:
                result.context_modifier = self._build_continue_hint(decision)

        elif decision.action_type == "redirect":
            result = self._render_redirect(decision)

        elif decision.action_type == "compress":
            result = self._render_compress(decision)

        elif decision.action_type == "terminate":
            result = self._render_terminate(decision)

        return result

    def _render_redirect(self, decision: ControlDecision) -> RenderedPrompt:
        result = RenderedPrompt()
        result.suggested_tools = decision.suggested_tools or []

        if decision.params.get("hard_constraint"):
            result.system_injection = decision.params["hard_constraint"]

        reasons = []
        if decision.guidance_message:
            reasons.append(decision.guidance_message)
        else:
            reasons.append("当前方向可能遇到困难")

        tools_section = ""
        if result.suggested_tools:
            tools_section = "\n**推荐尝试的工具：**\n"
            for i, tool in enumerate(result.suggested_tools[:3], 1):
                tools_section += f"{i}. `{tool}`\n"

        result.guidance_message = self.REDIRECT_TEMPLATE.format(
            reasons="\n".join(f"- {r}" for r in reasons),
            tools_section=tools_section,
        )

        return result

    def _render_compress(self, decision: ControlDecision) -> RenderedPrompt:
        """渲染 compress 决策"""
        result = RenderedPrompt()

        result.system_injection = self.COMPRESS_TEMPLATE

        if decision.context_trim_level == "aggressive":
            result.guidance_message = "§上下文严重不足，请极度精简回复。"

        return result

    def _render_terminate(self, decision: ControlDecision) -> RenderedPrompt:
        """渲染 terminate 决策"""
        result = RenderedPrompt()
        result.force_stop = True

        result.guidance_message = self.TERMINATE_TEMPLATE.format(
            stop_reason=decision.stop_reason or "达到系统限制",
        )

        return result

    def _build_continue_hint(self, decision: ControlDecision) -> Optional[str]:
        """构建 continue 的轻量提示（调试用）"""
        if not decision.params:
            return None

        hints = []
        if "stuck_threshold" in decision.params:
            hints.append(f"停滞阈值：{decision.params['stuck_threshold']}")
        if "repetition_threshold" in decision.params:
            hints.append(f"重复阈值：{decision.params['repetition_threshold']}")

        if hints:
            return f"[控制参数] {', '.join(hints)}"
        return None

    def render_simple(self, decision: ControlDecision) -> Dict[str, Any]:
        rendered = self.render(decision)
        return {
            "guidance_message": rendered.guidance_message,
            "system_injection": rendered.system_injection,
            "context_modifier": rendered.context_modifier,
            "force_stop": rendered.force_stop,
            "suggested_tools": rendered.suggested_tools,
        }
