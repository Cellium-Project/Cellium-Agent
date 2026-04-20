# -*- coding: utf-8 -*-
"""
预定义提示词拼图块

包含：
  - BASE_PIECES: 基础层（始终存在）
  - DYNAMIC_PIECES: 动态层（按需启用）

注意：personality.md 已包含工具调用规范和约束，
      基础层只加载 personality.md，避免重复。
"""

import os
from typing import List, TYPE_CHECKING

from app.agent.prompt.piece import PromptPiece

if TYPE_CHECKING:
    from app.agent.prompt.builder import PromptBuilder


# ============================================================
# 基础层 - 始终存在
# ============================================================

def get_identity_piece(memory_dir: str = "memory") -> PromptPiece:
    """
    获取身份定义拼图块

    从 memory/personality.md 加载，不存在则使用默认
    """
    personality_path = os.path.join(memory_dir, "personality.md")
    if os.path.exists(personality_path):
        try:
            with open(personality_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            content = DEFAULT_IDENTITY
    else:
        content = DEFAULT_IDENTITY

    return PromptPiece(
        name="identity",
        content=content,
        priority=0,
        is_base=True,
    )


DEFAULT_IDENTITY = """# Cellium Agent

你是一个专业的桌面助手，擅长：
- 执行系统命令和脚本
- 读写文件和管理项目
- 回答技术问题
- 协助开发和调试

请用中文回复，保持专业、友好、简洁。

## 工具调用规范
- 每次工具调用必须包含 `_intent` 字段
- `_intent` 格式：`正在{动词}{对象}`，15~30字中文
"""


# ============================================================
# 动态层 - 按需启用
# ============================================================

DYNAMIC_PIECES: List[PromptPiece] = [
    PromptPiece(
        name="session_context",
        template="""## 当前对话上下文

{% if session_messages %}
{% for msg in session_messages %}
{% if msg.content %}
> **{{ msg.role }}**: {{ msg.content[:300] if msg.content | length > 300 else msg.content }}
{% endif %}
{% endfor %}
{% else %}
（新会话，无历史上下文）
{% endif %}""",
        priority=110,
        enabled=False,  # 有历史时启用
    ),
    PromptPiece(
        name="long_memory",
        template="""## 相关历史记忆

{% if long_term_results %}
{% for item in long_term_results %}
{% if item.content %}
### {{ item.title | default('记忆') }} (相关度: {{ item.score | default(0) }})
{{ item.content[:500] if item.content | length > 500 else item.content }}
{% endif %}
{% endfor %}
{% else %}
（未检索到相关历史记忆）
{% endif %}""",
        priority=120,
        enabled=False,
    ),
    PromptPiece(
        name="user_input",
        template="""## 用户新问题

{{ user_input }}""",
        priority=200,
        enabled=True,
    ),
    PromptPiece(
        name="self_awareness",
        template="""## 运行时状态参考

{% if runtime_status %}
{{ runtime_status }}

**请在决策时参考以上运行状态**，特别是：
- 出现 `[错误]` 或 `[警告]` → 当前方法有问题，应换策略
- 出现 `[决策] redirect` → 被要求换工具，不要重复
- 出现 `[停止]` → 已达终止条件，整理结果并结束
{% endif %}""",
        priority=105,
        enabled=False,  # 有 runtime_status 时才启用
    ),
    PromptPiece(
        name="thinking_reminder",
        template="""## ⚡ 思考输出格式 [强制]

**调用工具前必须先输出结构化思考**：

```json
{
  "reasoning": "分析当前情况（50-200字）",
  "plan": [
    {"tool": "工具名", "purpose": "目的", "expected_result": "预期结果"}
  ],
  "action": "tool_call",
  "confidence": 0.8,
  "estimated_steps": 2
}
```

**action 类型**：
- `tool_call`: 需要调用工具
- `direct_response`: 可以直接回答，无需工具
- `clarify`: 需要用户澄清

**禁止**：不输出思考直接调用工具、逐个工具试探""",
        priority=150,
        enabled=True,
    ),
]


# ============================================================
# 工厂函数
# ============================================================

def create_default_builder(memory_dir: str = "memory") -> "PromptBuilder":
    """
    创建默认配置的 PromptBuilder

    Args:
        memory_dir: 记忆目录路径

    Returns:
        配置好基础层和动态层的 PromptBuilder
    """
    from app.agent.prompt.builder import PromptBuilder

    builder = PromptBuilder()

    builder.register(get_identity_piece(memory_dir))

    for piece in DYNAMIC_PIECES:
        builder.register(piece)

    return builder
