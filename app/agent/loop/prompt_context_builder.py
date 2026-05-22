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
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

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
        memory_dir: str = "memory",
    ):
        """
        初始化构建器

        Args:
            prompt_builder: Prompt 构建器实例
            three_layer_memory: 三层记忆系统（可选）
            flash_mode: 是否启用flash模式（跳过记忆注入）
            memory_dir: 记忆目录路径
        """
        self._prompt_builder = prompt_builder
        self._three_layer_memory = three_layer_memory
        self._flash_mode = flash_mode
        self._memory_dir = memory_dir
        self._cached_fixed_personality: Optional[str] = None

    def _get_fixed_personality(self) -> str:
        """
        获取固定的 personality 内容
        
        Returns:
            personality.md 内容，其中 {{current_date}} 保持原样或替换为占位符
        """
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
        """获取默认身份定义"""
        return """# Cellium Agent

- **当前日期**: {{current_date}}

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

    def _build_system_messages(self, runtime_status: Optional[str] = None) -> Tuple[Dict, Dict]:
        """
        构建 system 消息
        
        Returns:
            (固定 system 消息, 动态 system 消息)
        """
        # 1. 固定内容
        fixed_content = self._get_fixed_personality()
        fixed_message = {"role": "system", "content": fixed_content}
        
        # 2. 动态内容
        dynamic_parts = []

        # 日期信息
        current_date = self._get_current_date()
        dynamic_parts.append(f"**当前日期**: {current_date}")

        # 系统环境信息
        system_info = self._get_system_info()
        dynamic_parts.append(f"**系统环境**: {system_info}")
        
        # 运行时状态
        if runtime_status:
            dynamic_parts.append(f"\n[运行时状态]\n{runtime_status}")
        
        dynamic_content = "\n".join(dynamic_parts)
        dynamic_message = {"role": "system", "content": dynamic_content}
        
        return fixed_message, dynamic_message

    def _inject_runtime_status(self, runtime_status: Optional[Any]) -> None:
        """注入运行时状态到 PromptBuilder（供 Agent 自我感知）"""
        if not self._flash_mode and runtime_status:
            self._prompt_builder.enable("self_awareness", True)
            self._prompt_builder.inject(
                runtime_status,
                name="_runtime_status",
                priority=100,
                is_base=False,
            )
        else:
            self._prompt_builder.enable("self_awareness", False)

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

        self._prompt_builder.clear_dynamic()

        fixed_sys_msg, dynamic_sys_msg = self._build_system_messages(runtime_status)
        messages.append(fixed_sys_msg)  
        messages.append(dynamic_sys_msg)  

        if system_injection:
            messages.append({
                "role": "system",
                "content": system_injection,
            })

        # 2. 注入长期记忆
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

        # 3. 运行时状态通过 PromptBuilder 注入
        self._inject_runtime_status(runtime_status)

        # 4. 会话历史
        messages.extend(session_messages)

        # 4.5 Flash模式：直接注入用户输入（session_messages 为空时）
        if self._flash_mode and not session_messages:
            messages.append({"role": "user", "content": user_input})

        # 5. 引导消息
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
            iteration: 当前迭代轮次（用于控制 personality.md 注入频率）

        Returns:
            LLM 消息列表
        """
        messages = []

        # 1. 系统提示词
        self._prompt_builder.clear_dynamic()

        fixed_sys_msg, dynamic_sys_msg = self._build_system_messages(runtime_status)
        messages.append(fixed_sys_msg)
        messages.append(dynamic_sys_msg)

        if system_injection:
            messages.append({
                "role": "system",
                "content": system_injection,
            })

        # 2. 运行时状态
        self._inject_runtime_status(runtime_status)

        # 3. 会话历史
        messages.extend(session_messages)

        # 4. 自动提示
        if auto_hints:
            messages.append({
                "role": "user",
                "content": f"[工具使用提示]\n{auto_hints}",
            })
            messages.append({
                "role": "assistant",
                "content": "好的，我会参考这些提示。",
            })

        # 4. 引导消息
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
            results = self._three_layer_memory.retrieve_context(query, top_k=3, exclude_schema_types=["control_gene"])
            if not results:
                return None
            return self._three_layer_memory.format_retrieved_context(results)
        except Exception as e:
            logger.warning("[PromptContextBuilder] 长期记忆检索失败: %s", e)
            return None


    def update_flash_mode(self, enabled: bool):
        self._flash_mode = enabled
