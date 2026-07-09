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
    tool: str = ""
    purpose: str = ""
    expected_result: str = ""


@dataclass
class ParsedThought:
    reasoning: str = ""
    plan: List[ThoughtStep] = field(default_factory=list)
    action: ActionType = ActionType.TOOL_CALL
    confidence: float = 0.0
    raw_content: str = ""
    is_valid: bool = True
    parse_errors: List[str] = field(default_factory=list)


THOUGHT_SCHEMA = """
在调用工具前先输出以下 JSON 思考块（填空即可）：

```json
{
  "reasoning": "<思考>",
  "plan": [{"tool": "工具名", "purpose": "目的", "expected_result": "预期结果"}],
  "action": "tool_call",
  "confidence": 0.8
}
```

字段说明：
- reasoning: 思考过程（必填）
- plan: 步骤列表（tool + purpose + expected_result），2-5 步
- action: tool_call | direct_response | clarify
- confidence: 置信度 0-1
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
        brace_start = content.find('{')
        if brace_start != -1:
            brace_end = content.rfind('}')
            if brace_end > brace_start:
                candidate = content[brace_start:brace_end + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass
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
                    ))
                elif isinstance(step, str):
                    result.plan.append(ThoughtStep(purpose=step))
        
        action_str = data.get("action", "tool_call")
        try:
            result.action = ActionType(action_str)
        except ValueError:
            result.action = ActionType.TOOL_CALL
        
        result.confidence = float(data.get("confidence", 0.5))
        
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
        
        return result
