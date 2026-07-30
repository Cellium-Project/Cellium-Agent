# -*- coding: utf-8 -*-
"""测试意图感知 LLM 的启用/禁用行为"""

import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch


class MockLLMEngine:
    """Mock LLM 引擎"""
    def __init__(self, name="main-model"):
        self.name = name
        self._call_count = 0

    async def chat(self, messages=None, **kwargs):
        self._call_count += 1
        from app.agent.llm.engine import ChatResponse
        return ChatResponse(content="none")


class MockAgentLoop:
    """模拟 AgentLoop 的意图相关逻辑（与实际代码行为一致）"""
    def __init__(self, llm_engine, intent_llm_engine=None, intent_enabled=True):
        self.llm = llm_engine
        self._intent_llm = intent_llm_engine or llm_engine
        self._intent_enabled = intent_enabled

    def update_config(self, intent_llm=None, intent_enabled=None):
        if intent_llm is not None:
            self._intent_llm = intent_llm
        if intent_enabled is not None:
            self._intent_enabled = intent_enabled

    async def _llm_match_gene(self, user_input):
        if not self._intent_enabled:
            return None
        resp = await self._intent_llm.chat()
        if resp.content and resp.content != "none":
            return {"task_type": "test", "gene_template": ""}
        return None

    async def _create_gene_in_background(self, user_input, state):
        if not self._intent_enabled or not self._intent_llm:
            return False
        return True


class TestIntentLLM(unittest.TestCase):
    """意图感知 LLM 启用/禁用测试"""

    def setUp(self):
        self.main_llm = MockLLMEngine("main-model")
        self.separate_llm = MockLLMEngine("intent-model")
        self.third_llm = MockLLMEngine("another-intent-model")

    # ─── 初始状态 ───

    def test_default_uses_main_model(self):
        """未配置 intent_llm → 使用主模型"""
        loop = MockAgentLoop(llm_engine=self.main_llm)
        self.assertIs(loop._intent_llm, self.main_llm)

    def test_separate_llm_when_configured(self):
        """配置了 intent_llm → 使用独立模型"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm)
        self.assertIs(loop._intent_llm, self.separate_llm)
        self.assertIsNot(loop._intent_llm, self.main_llm)

    def test_initial_intent_enabled_by_default(self):
        """默认 intent_enabled=True"""
        loop = MockAgentLoop(llm_engine=self.main_llm)
        self.assertTrue(loop._intent_enabled)

    def test_initial_intent_disabled(self):
        """初始化时 intent_enabled=False"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_enabled=False)
        self.assertFalse(loop._intent_enabled)

    # ─── 禁用时的行为 ───

    def test_disabled_intent_llm_match_returns_none(self):
        """intent_enabled=False → _llm_match_gene 返回 None"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_enabled=False)

        result = asyncio.run(loop._llm_match_gene("测试输入"))
        self.assertIsNone(result)
        # 主模型不应被调用
        self.assertEqual(self.main_llm._call_count, 0)

    def test_disabled_intent_skip_background_gene(self):
        """intent_enabled=False → _create_gene_in_background 返回 False"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_enabled=False)

        result = asyncio.run(loop._create_gene_in_background("测试", MagicMock()))
        self.assertFalse(result)

    def test_disabled_with_separate_llm_available(self):
        """intent_enabled=False 即使有独立模型也不调用"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm, intent_enabled=False)
        result = asyncio.run(loop._llm_match_gene("测试"))
        self.assertIsNone(result)
        self.assertEqual(self.separate_llm._call_count, 0)
        self.assertEqual(self.main_llm._call_count, 0)

    # ─── 启用时的行为 ───

    def test_llm_match_uses_separate_model(self):
        """启用独立模型 → _llm_match_gene 使用 intent_llm"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm)
        result = asyncio.run(loop._llm_match_gene("测试"))
        self.assertIsNone(result)
        self.assertEqual(self.separate_llm._call_count, 1)
        self.assertEqual(self.main_llm._call_count, 0)

    def test_llm_match_uses_main_model_when_no_separate(self):
        """启用但没有独立模型 → 使用主模型"""
        loop = MockAgentLoop(llm_engine=self.main_llm)
        result = asyncio.run(loop._llm_match_gene("测试"))
        self.assertIsNone(result)
        self.assertEqual(self.main_llm._call_count, 1)

    def test_create_gene_in_background_when_enabled(self):
        """启用 → _create_gene_in_background 返回 True"""
        loop = MockAgentLoop(llm_engine=self.main_llm)
        result = asyncio.run(loop._create_gene_in_background("测试", MagicMock()))
        self.assertTrue(result)

    # ─── 热重载：启用/关闭切换 ───

    def test_hot_reload_disable_updates_flag(self):
        """热重载关闭 intent → 标志位变化 + match 跳过"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm)
        self.assertTrue(loop._intent_enabled)
        self.assertIs(loop._intent_llm, self.separate_llm)

        # 热重载关闭
        loop.update_config(intent_llm=None, intent_enabled=False)

        self.assertFalse(loop._intent_enabled)
        result = asyncio.run(loop._llm_match_gene("测试"))
        self.assertIsNone(result)
        self.assertEqual(self.separate_llm._call_count, 0)

    def test_hot_reload_enable_then_disable(self):
        """热重载先启用独立模型，再关闭 → 重置为禁用"""
        loop = MockAgentLoop(llm_engine=self.main_llm)

        # 启用独立模型
        loop.update_config(intent_llm=self.separate_llm, intent_enabled=True)
        self.assertIs(loop._intent_llm, self.separate_llm)
        self.assertTrue(loop._intent_enabled)

        # 关闭
        loop.update_config(intent_llm=None, intent_enabled=False)
        self.assertFalse(loop._intent_enabled)

        # match 应该返回 None
        result = asyncio.run(loop._llm_match_gene("测试"))
        self.assertIsNone(result)
        self.assertEqual(self.separate_llm._call_count, 0)

    def test_hot_reload_disable_then_enable(self):
        """热重载先禁用，再启用 → 恢复正常"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_enabled=False)
        self.assertFalse(loop._intent_enabled)

        # 启用
        loop.update_config(intent_enabled=True)
        self.assertTrue(loop._intent_enabled)
        self.assertIs(loop._intent_llm, self.main_llm)

        # match 应该调主模型
        result = asyncio.run(loop._llm_match_gene("测试"))
        self.assertIsNone(result)
        self.assertEqual(self.main_llm._call_count, 1)

    def test_hot_reload_swap_llm_engine(self):
        """热重载切换独立模型引擎"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm)
        self.assertIs(loop._intent_llm, self.separate_llm)

        # 切换到第三个模型
        loop.update_config(intent_llm=self.third_llm)
        self.assertIs(loop._intent_llm, self.third_llm)

        # 新模型应被调用
        asyncio.run(loop._llm_match_gene("测试"))
        self.assertEqual(self.third_llm._call_count, 1)
        self.assertEqual(self.separate_llm._call_count, 0)
        self.assertEqual(self.main_llm._call_count, 0)

    def test_hot_reload_swap_back_to_main(self):
        """热重载从独立模型切换回主模型"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm)

        # 切换回主模型（传入主模型实例）
        loop.update_config(intent_llm=self.main_llm)
        self.assertIs(loop._intent_llm, self.main_llm)

        asyncio.run(loop._llm_match_gene("测试"))
        self.assertEqual(self.main_llm._call_count, 1)
        self.assertEqual(self.separate_llm._call_count, 0)

    def test_hot_reload_enabled_no_change_to_others(self):
        """热重载只改 enabled，不改 engine"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm)
        self.assertIs(loop._intent_llm, self.separate_llm)

        # 只禁用
        loop.update_config(intent_enabled=False)
        self.assertFalse(loop._intent_enabled)
        # engine 不变
        self.assertIs(loop._intent_llm, self.separate_llm)

        # match 应跳过（因为 disabled）
        asyncio.run(loop._llm_match_gene("测试"))
        self.assertEqual(self.separate_llm._call_count, 0)

    def test_hot_reload_only_engine_no_flag_change(self):
        """热重载只换 engine，不改 enabled 标志"""
        loop = MockAgentLoop(llm_engine=self.main_llm, intent_llm_engine=self.separate_llm, intent_enabled=False)
        self.assertFalse(loop._intent_enabled)

        # 换引擎
        loop.update_config(intent_llm=self.third_llm)
        # enabled 仍为 False
        self.assertFalse(loop._intent_enabled)
        # engine 已换
        self.assertIs(loop._intent_llm, self.third_llm)

    # ─── 完整生命周期 ───

    def test_full_lifecycle(self):
        """完整生命周期：默认→启用独立→禁用→重新启用"""
        loop = MockAgentLoop(llm_engine=self.main_llm)
        self.assertTrue(loop._intent_enabled)
        self.assertIs(loop._intent_llm, self.main_llm)

        # Phase 1: 启用独立模型
        loop.update_config(intent_llm=self.separate_llm, intent_enabled=True)
        asyncio.run(loop._llm_match_gene("测试"))
        self.assertEqual(self.separate_llm._call_count, 1)
        self.assertEqual(self.main_llm._call_count, 0)

        # Phase 2: 禁用
        loop.update_config(intent_enabled=False)
        asyncio.run(loop._llm_match_gene("测试"))
        self.assertEqual(self.separate_llm._call_count, 1)  # 没变
        self.assertEqual(self.main_llm._call_count, 0)

        # Phase 3: 重新启用（传入主模型实例）
        loop.update_config(intent_llm=self.main_llm, intent_enabled=True)
        self.assertIs(loop._intent_llm, self.main_llm)
        asyncio.run(loop._llm_match_gene("测试"))
        self.assertEqual(self.main_llm._call_count, 1)


if __name__ == "__main__":
    unittest.main()
