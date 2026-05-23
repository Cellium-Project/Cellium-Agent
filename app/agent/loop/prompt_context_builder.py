# -*- coding: utf-8 -*-
"""
提示词上下文构建器

职责：
  - 构建第一轮对话的提示词
  - 构建后续轮次的提示词
  - 注入长期记忆和引导信息
"""

import logging
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.memory.three_layer import ThreeLayerMemory

logger = logging.getLogger(__name__)


class PromptContextBuilder:
    """
    提示词上下文构建器

    负责根据不同轮次构建 LLM 消息列表。
    """

    def __init__(
        self,
        three_layer_memory: Optional["ThreeLayerMemory"] = None,
        flash_mode: bool = False,
        memory_dir: str = "memory",
    ):
        self._three_layer_memory = three_layer_memory
        self._flash_mode = flash_mode
        self._memory_dir = memory_dir
        self._cached_fixed_personality: Optional[str] = None

    def _get_fixed_personality(self) -> str:
        if self._cached_fixed_personality is not None:
            return self._cached_fixed_personality
        
        personality_path = os.path.join(self._memory_dir, "personality.md")
        if os.path.exists(personality_path):
            try:
                with open(personality_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                content = self._get_default_identity()
        else:
            content = self._get_default_identity()
        
        self._cached_fixed_personality = content
        return content

    def _get_default_identity(self) -> str:
        return """# Cellium Agent

你是一个专业的桌面助手，擅长：
- 执行系统命令和脚本
- 读写文件和管理项目
- 回答技术问题
- 协助开发和调试

"""

    def _get_current_date(self) -> str:
        """获取当前日期字符串"""
        from datetime import datetime
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"{now.year}年{now.month}月{now.day}号 {weekdays[now.weekday()]}"

    def _get_system_info(self) -> str:
        """获取系统环境信息"""
        import platform

        system = platform.system()
        machine = platform.machine()

        if system == "Windows":
            shell = "PowerShell"
        elif system == "Darwin":
            shell = "zsh/bash"
        else:
            shell = "bash"

        return f"{system} {machine} | {shell}"

    def _build_system_message(self) -> Dict:
        """
        构建 system 消息
        
        Returns:
            固定 system 消息
        """
        fixed_content = self._get_fixed_personality()

        from app.agent.control.thought_parser import THOUGHT_SCHEMA
        
        content = f"{fixed_content}\n\n{THOUGHT_SCHEMA}"
        
        return {"role": "system", "content": content}
    
    def _build_context_message(self, runtime_status: Optional[str] = None) -> str:
        """
        构建动态上下文信息
        
        Returns:
            动态上下文字符串
        """
        context_parts = []
        
        current_date = self._get_current_date()
        context_parts.append(f"**当前日期**: {current_date}")
        
        system_info = self._get_system_info()
        context_parts.append(f"**系统环境**: {system_info}")
        
        if runtime_status:
            context_parts.append(f"\n[运行时状态]\n{runtime_status}")
        
        return "\n".join(context_parts)

    def build_first_round(
        self,
        user_input: str,
        session_messages: List[Dict],
        guidance_message: Optional[str] = None,
        system_injection: Optional[str] = None,
        runtime_status: Optional[str] = None,
    ) -> List[Dict]:
        """
        构建第一轮对话的消息

        Args:
            user_input: 用户输入
            session_messages: 当前会话消息列表
            guidance_message: 引导消息（来自启发式模块）
            system_injection: 系统提示词注入（来自控制环）
            runtime_status: 运行时状态摘要（来自 LoopState）

        Returns:
            LLM 消息列表
        """
        messages = []

        messages.append(self._build_system_message())

        prefix_parts = []

        if system_injection:
            prefix_parts.append(f"[系统指令]\n{system_injection}")

        context_content = self._build_context_message(runtime_status)
        if context_content:
            prefix_parts.append(f"[上下文信息]\n{context_content}")

        if not self._flash_mode and self._three_layer_memory:
            long_term_context = self._retrieve_long_term_memory(user_input)
            if long_term_context:
                prefix_parts.append(f"[长期记忆检索结果]\n{long_term_context}")

        if prefix_parts:
            messages.append({
                "role": "user",
                "content": "\n\n".join(prefix_parts),
            })

        messages.extend(session_messages)

        if self._flash_mode and not session_messages:
            messages.append({"role": "user", "content": user_input})

        if guidance_message:
            messages.append({
                "role": "user",
                "content": f"[系统引导]\n{guidance_message}",
            })

        logger.debug(
            "[PromptContextBuilder] 第一轮消息构建完成 | messages=%d | flash_mode=%s",
            len(messages),
            self._flash_mode,
        )

        return messages

    _BRIEF_SYSTEM_REMINDER = """[系统规则提醒]
- _intent: 正在{动作}：{对象}
- 禁止：shell 写文件 → 用 file 工具
- 读代码：先 insight 结构，再 read 内容
- 有 Skill 可用时优先使用

[决策原则]
- 不要忽略运行时状态中的红色警告信息
- 不要重复最近失败的相同操作
- 不要超过迭代限制强行继续
- 主动利用控制环给出的 redirect 建议"""

    def build_subsequent_round(
        self,
        session_messages: List[Dict],
        auto_hints: Optional[str] = None,
        guidance_message: Optional[str] = None,
        system_injection: Optional[str] = None,
        runtime_status: Optional[str] = None,
        iteration: int = 1,
    ) -> List[Dict]:
        """
        构建后续轮次的消息

        Args:
            session_messages: 当前会话消息列表
            auto_hints: 自动生成的工具提示
            guidance_message: 引导消息
            system_injection: 系统提示词注入（来自控制环）
            runtime_status: 运行时状态摘要（来自 LoopState）
            iteration: 当前迭代轮次

        Returns:
            LLM 消息列表
        """
        messages = []

        messages.append(self._build_system_message())

        prefix_parts = []

        if system_injection:
            prefix_parts.append(f"[系统指令]\n{system_injection}")

        context_content = self._build_context_message(runtime_status)
        if context_content:
            prefix_parts.append(f"[上下文信息]\n{context_content}")

        if auto_hints:
            prefix_parts.append(f"[工具使用提示]\n{auto_hints}")

        if guidance_message:
            prefix_parts.append(f"[系统引导]\n{guidance_message}")

        if prefix_parts:
            messages.append({
                "role": "user",
                "content": "\n\n".join(prefix_parts),
            })

        messages.extend(session_messages)

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
            results = self._three_layer_memory.retrieve_context(query, top_k=3, exclude_schema_types=["control_gene"])
            if not results:
                return None
            return self._three_layer_memory.format_retrieved_context(results)
        except Exception as e:
            logger.warning("[PromptContextBuilder] 长期记忆检索失败: %s", e)
            return None


    def update_flash_mode(self, enabled: bool):
        self._flash_mode = enabled
