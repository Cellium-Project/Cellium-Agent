# -*- coding: utf-8 -*-
"""
提示词上下文构建器

职责：
  - 构建第一轮对话的提示词
  - 构建后续轮次的提示词
  - 注入长期记忆和引导信息
"""

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.prompt import PromptBuilder
    from app.agent.memory.three_layer import ThreeLayerMemory

logger = logging.getLogger(__name__)


class PromptContextBuilder:
    """
    提示词上下文构建器

    负责根据不同轮次构建 LLM 消息列表。
    """

    def __init__(
        self,
        prompt_builder: "PromptBuilder",
        three_layer_memory: Optional["ThreeLayerMemory"] = None,
        flash_mode: bool = False,
    ):
        """
        初始化构建器

        Args:
            prompt_builder: Prompt 构建器实例
            three_layer_memory: 三层记忆系统（可选）
            flash_mode: 是否启用闪电模式（跳过记忆注入）
        """
        self._prompt_builder = prompt_builder
        self._three_layer_memory = three_layer_memory
        self._flash_mode = flash_mode

    def build_first_round(
        self,
        user_input: str,
        session_messages: List[Dict],
        guidance_message: Optional[str] = None,
        system_injection: Optional[str] = None,
    ) -> List[Dict]:
        """
        构建第一轮对话的消息

        Args:
            user_input: 用户输入
            session_messages: 当前会话消息列表
            guidance_message: 引导消息（来自启发式模块）
            system_injection: 系统提示词注入（来自控制环）

        Returns:
            LLM 消息列表
        """
        messages = []

        # 1. 系统提示词（包含身份、工具指南等）
        # 清除残留的动态控制注入，避免跨轮次/跨会话泄漏
        self._prompt_builder.clear_dynamic()

        # 如果有 system_injection，先注入到 PromptBuilder
        if system_injection:
            self._prompt_builder.inject(
                system_injection,
                name="_control_constraint",
                priority=50,  # 高优先级
            )


        system_prompt = self._prompt_builder.build()
        messages.append({"role": "system", "content": system_prompt})

        # 2. 注入长期记忆（非闪电模式）
        if not self._flash_mode and self._three_layer_memory:
            long_term_context = self._retrieve_long_term_memory(user_input)
            if long_term_context:
                messages.append({
                    "role": "user",
                    "content": f"[长期记忆检索结果]\n{long_term_context}\n\n请参考以上信息回答用户问题。",
                })
                messages.append({
                    "role": "assistant",
                    "content": "好的，我已参考长期记忆中的相关信息。",
                })

        # 3. 会话历史
        messages.extend(session_messages)

        # 4. 引导消息（如果有）
        if guidance_message:
            messages.append({
                "role": "user",
                "content": f"[系统引导]\n{guidance_message}",
            })
            messages.append({
                "role": "assistant",
                "content": "好的，我会按照引导执行。",
            })

        logger.debug(
            "[PromptContextBuilder] 第一轮消息构建完成 | messages=%d | flash_mode=%s",
            len(messages),
            self._flash_mode,
        )

        return messages

    def build_subsequent_round(
        self,
        session_messages: List[Dict],
        auto_hints: Optional[str] = None,
        guidance_message: Optional[str] = None,
        system_injection: Optional[str] = None,
    ) -> List[Dict]:
        """
        构建后续轮次的消息

        Args:
            session_messages: 当前会话消息列表
            auto_hints: 自动生成的工具提示
            guidance_message: 引导消息
            system_injection: 系统提示词注入（来自控制环）

        Returns:
            LLM 消息列表
        """
        messages = []

        # 1. 系统提示词
        # 清除上一轮的动态注入，避免累积
        self._prompt_builder.clear_dynamic()

        # 如果有 system_injection，注入到 PromptBuilder
        if system_injection:
            self._prompt_builder.inject(
                system_injection,
                name="_control_constraint",
                priority=50,
            )

        system_prompt = self._prompt_builder.build()
        messages.append({"role": "system", "content": system_prompt})

        # 2. 会话历史（后续轮次不需要重复注入长期记忆）
        messages.extend(session_messages)

        # 3. 自动提示（如果有）
        if auto_hints:
            messages.append({
                "role": "user",
                "content": f"[工具使用提示]\n{auto_hints}",
            })
            messages.append({
                "role": "assistant",
                "content": "好的，我会参考这些提示。",
            })

        # 4. 引导消息（如果有）
        if guidance_message:
            messages.append({
                "role": "user",
                "content": f"[系统引导]\n{guidance_message}",
            })
            messages.append({
                "role": "assistant",
                "content": "好的，我会按照引导执行。",
            })

        logger.debug(
            "[PromptContextBuilder] 后续轮次消息构建完成 | messages=%d",
            len(messages),
        )

        return messages

    def _retrieve_long_term_memory(self, query: str) -> Optional[str]:
        """
        从长期记忆中检索相关信息

        Args:
            query: 查询字符串

        Returns:
            检索结果文本（如果没有则返回 None）
        """
        if not self._three_layer_memory:
            return None

        try:
            results = self._three_layer_memory.retrieve_context(query, top_k=3)
            if not results:
                return None
            return self._three_layer_memory.format_retrieved_context(results)
        except Exception as e:
            logger.warning("[PromptContextBuilder] 长期记忆检索失败: %s", e)
            return None


    def update_flash_mode(self, enabled: bool):
        """更新闪电模式状态"""
        self._flash_mode = enabled
