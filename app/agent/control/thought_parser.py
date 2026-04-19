# -*- coding: utf-8 -*-

import json
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)

class ActionType(Enum):
    TOOL_CALL = "tool_call"
    DIRECT_RESPONSE = "direct_response"
    CLARIFY = "clarify"
    SKIP = "skip"

@dataclass
class ThoughtStep:
    tool: str
    purpose: str
    expected_result: str = ""
    fallback: str = ""


@dataclass
class ParsedThought:
    reasoning: str = ""
    plan: List[ThoughtStep] = field(default_factory=list)
    action: ActionType = ActionType.TOOL_CALL
    confidence: float = 0.0
    estimated_steps: int = 1
    raw_content: str = ""
    is_valid: bool = True
    parse_errors: List[str] = field(default_factory=list)


THOUGHT_SCHEMA = """
## 思考输出格式 [强制]

在调用工具前，必须先输出以下格式的思考：

```json
{
  "reasoning": "分析当前情况，说明为什么选择这个工具",
  "plan": [
    {"tool": "工具名", "purpose": "目的", "expected_result": "预期结果"}
  ],
  "action": "tool_call|direct_response|clarify",
  "confidence": 0.8,
  "estimated_steps": 2
}
```

**字段说明**：
- `reasoning`: 思考过程（必填，50-200字）
- `plan`: 执行计划（必填，最多3步）
- `action`: 行动类型
  - `tool_call`: 需要调用工具
  - `direct_response`: 可以直接回答，无需工具
  - `clarify`: 需要用户澄清
- `confidence`: 置信度（0-1）
- `estimated_steps`: 预计还需几轮完成

**示例**：
```json
{
  "reasoning": "用户想查看 main.py 的代码结构。先用 insight 看骨架，再决定是否需要详细读取。",
  "plan": [
    {"tool": "file:insight", "purpose": "查看代码骨架", "expected_result": "获取函数/类列表"}
  ],
  "action": "tool_call",
  "confidence": 0.9,
  "estimated_steps": 2
}
```
"""


class ThoughtParser:
    
    JSON_PATTERN = re.compile(r'```json\s*([\s\S]*?)\s*```', re.IGNORECASE)
    THOUGHT_BLOCK_PATTERN = re.compile(
        r'(?:💭|思考|THOUGHT)[:：]?\s*([\s\S]*?)(?=```json|$)',
        re.IGNORECASE
    )
    
    @classmethod
    def parse(cls, content: str) -> ParsedThought:
        """
        从模型输出中解析思考内容
        
        Args:
            content: 模型的输出内容
            
        Returns:
            ParsedThought 解析结果
        """
        if not content:
            return ParsedThought(is_valid=False, parse_errors=["空内容"])
        
        result = ParsedThought(raw_content=content)
        
        json_str = cls._extract_json(content)
        
        if json_str:
            try:
                data = json.loads(json_str)
                result = cls._parse_structured(data, content)
            except json.JSONDecodeError as e:
                result.parse_errors.append(f"JSON 解析失败: {e}")
                result = cls._parse_unstructured(content)
        else:
            result = cls._parse_unstructured(content)
        
        return result
    
    @classmethod
    def _extract_json(cls, content: str) -> Optional[str]:
        match = cls.JSON_PATTERN.search(content)
        if match:
            return match.group(1)
        return None
    
    @classmethod
    def _parse_structured(cls, data: Dict, raw: str) -> ParsedThought:
        result = ParsedThought(raw_content=raw, is_valid=True)
        
        result.reasoning = data.get("reasoning", "")
        
        plan_data = data.get("plan", [])
        if isinstance(plan_data, list):
            for step in plan_data:
                if isinstance(step, dict):
                    result.plan.append(ThoughtStep(
                        tool=step.get("tool", ""),
                        purpose=step.get("purpose", ""),
                        expected_result=step.get("expected_result", ""),
                        fallback=step.get("fallback", ""),
                    ))
        
        action_str = data.get("action", "tool_call")
        try:
            result.action = ActionType(action_str)
        except ValueError:
            result.action = ActionType.TOOL_CALL
        
        result.confidence = float(data.get("confidence", 0.5))
        result.estimated_steps = int(data.get("estimated_steps", 1))
        
        if not result.reasoning:
            result.parse_errors.append("缺少 reasoning")
            result.is_valid = False
        
        if result.action == ActionType.TOOL_CALL and not result.plan:
            result.parse_errors.append("tool_call 但缺少 plan")
            result.is_valid = False
        
        return result
    
    @classmethod
    def _parse_unstructured(cls, content: str) -> ParsedThought:
        result = ParsedThought(raw_content=content, is_valid=True)
        
        lines = content.strip().split('\n')
        reasoning_parts = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if line.startswith(('1.', '2.', '3.', '-', '•')):
                reasoning_parts.append(line)
            elif '思考' in line or 'reasoning' in line.lower():
                continue
            else:
                reasoning_parts.append(line)
        
        result.reasoning = ' '.join(reasoning_parts[:3])
        
        if not result.reasoning:
            result.reasoning = content[:200]
        
        result.action = ActionType.TOOL_CALL
        result.confidence = 0.3
        result.estimated_steps = 3
        
        return result
    
    @classmethod
    def should_skip_tool(cls, thought: ParsedThought) -> bool:
        if thought.action == ActionType.DIRECT_RESPONSE:
            logger.info("[ThoughtParser] 模型判断可直接回答，跳过工具")
            return True
        
        if thought.action == ActionType.CLARIFY:
            logger.info("[ThoughtParser] 模型需要用户澄清")
            return True
        
        return False
    
    @classmethod
    def get_recommended_tools(cls, thought: ParsedThought) -> List[str]:
        return [step.tool for step in thought.plan if step.tool]
    
    @classmethod
    def validate_tool_choice(cls, thought: ParsedThought, tool_name: str) -> bool:
        if not thought.plan:
            return True
        
        planned_tools = [step.tool.split(':')[0] if ':' in step.tool else step.tool 
                        for step in thought.plan]
        planned_tools = [t.lower() for t in planned_tools]
        
        actual_tool = tool_name.split(':')[0] if ':' in tool_name else tool_name
        actual_tool = actual_tool.lower()
        
        if actual_tool in planned_tools:
            return True
        
        logger.warning(
            "[ThoughtParser] 工具 %s 不在计划中: %s",
            tool_name, planned_tools
        )
        return True
