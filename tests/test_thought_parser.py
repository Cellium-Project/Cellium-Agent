# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.control.thought_parser import ThoughtParser, ActionType


class TestThoughtParserStructured(unittest.TestCase):
    def test_parse_valid_json_block(self):
        content = '''
        ```json
        {
            "reasoning": "需要搜索文件",
            "plan": ["步骤1: 搜索文件", "步骤2: 读取内容"],
            "action": "tool_call",
            "confidence": 0.9
        }
        ```
        '''
        result = ThoughtParser.parse(content)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.reasoning, "需要搜索文件")
        self.assertEqual(len(result.plan), 2)
        self.assertEqual(result.plan[0].purpose, "步骤1: 搜索文件")
        self.assertEqual(result.action, ActionType.TOOL_CALL)
        self.assertEqual(result.confidence, 0.9)

    def test_parse_direct_response(self):
        content = '''
        ```json
        {"reasoning": "可以直接回答", "plan": [], "action": "direct_response", "confidence": 0.95}
        ```
        '''
        result = ThoughtParser.parse(content)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.action, ActionType.DIRECT_RESPONSE)

    def test_parse_clarify(self):
        content = '''
        ```json
        {"reasoning": "需要澄清", "plan": [], "action": "clarify", "confidence": 0.5}
        ```
        '''
        result = ThoughtParser.parse(content)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.action, ActionType.CLARIFY)

    def test_parse_multiple_steps_dict_format(self):
        """兼容旧格式：plan 为 dict 列表"""
        content = '''
        ```json
        {
            "reasoning": "多步骤计划",
            "plan": [
                {"tool": "read", "purpose": "读取", "expected_result": "内容"},
                {"tool": "write", "purpose": "写入", "expected_result": "成功"}
            ],
            "action": "tool_call"
        }
        ```
        '''
        result = ThoughtParser.parse(content)
        self.assertEqual(len(result.plan), 2)
        self.assertEqual(result.plan[0].tool, "read")
        self.assertEqual(result.plan[1].tool, "write")

    def test_parse_missing_reasoning_invalid(self):
        content = '''
        ```json
        {"plan": ["步骤1"], "action": "tool_call"}
        ```
        '''
        result = ThoughtParser.parse(content)
        self.assertFalse(result.is_valid)
        self.assertIn("缺少 reasoning", result.parse_errors)

    def test_parse_tool_call_missing_plan_invalid(self):
        content = '''
        ```json
        {"reasoning": "需要工具", "plan": [], "action": "tool_call"}
        ```
        '''
        result = ThoughtParser.parse(content)
        self.assertFalse(result.is_valid)
        self.assertIn("tool_call 但缺少 plan", result.parse_errors)

    def test_parse_invalid_json_fallback(self):
        content = '''
        ```json
        {invalid json}
        ```
        一些思考内容
        '''
        result = ThoughtParser.parse(content)
        self.assertTrue(result.is_valid)
        # JSON 解析失败时会回退到非结构化解析

    def test_parse_empty_content(self):
        result = ThoughtParser.parse("")
        self.assertFalse(result.is_valid)
        self.assertIn("空内容", result.parse_errors)


class TestThoughtParserUnstructured(unittest.TestCase):
    def test_parse_no_json_block(self):
        content = "我需要读取这个文件的内容"
        result = ThoughtParser.parse(content)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.reasoning, content)
        self.assertEqual(result.action, ActionType.TOOL_CALL)
        self.assertEqual(result.confidence, 0.3)

    def test_parse_list_items(self):
        content = '''1. 首先搜索文件
2. 然后读取内容
3. 最后分析'''
        result = ThoughtParser.parse(content)
        self.assertIn("首先搜索文件", result.reasoning)


if __name__ == '__main__':
    unittest.main()
