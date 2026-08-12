# -*- coding: utf-8 -*-
"""
AgentLoopManager - 多 Session 并发 AgentLoop 管理器

职责：
  - 管理多个 AgentLoop 实例，每个 session 独立
  - 提供 per-session 锁，实现真正的并发隔离
  - 限制最大实例数，防止 OOM
  - LRU 淘汰空闲过长的实例
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LoopMetadata:
    session_id: str
    created_at: float
    last_active: float
    agent_loop: Any


class AgentLoopManager:
    _instance: Optional['AgentLoopManager'] = None

    def __init__(self):
        self._loops: Dict[str, LoopMetadata] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._max_loops = 50
        self._session_timeout = 3600

        self._llm_engine = None
        self._shell = None
        self._three_layer_memory = None
        self._global_config = {}
        self._intent_enabled: Optional[bool] = None
        self._intent_llm = None

    @classmethod
    def get_instance(cls) -> 'AgentLoopManager':
        if cls._instance is None:
            cls._instance = AgentLoopManager()
        return cls._instance

    def initialize(self, llm_engine, shell, three_layer_memory, tools: Dict = None, global_config: Dict = None):
        if not shell:
            raise ValueError("shell is required")
        if not three_layer_memory:
            raise ValueError("three_layer_memory is required")
        self._llm_engine = llm_engine  
        self._three_layer_memory = three_layer_memory
        self._tools = tools or {}
        self._global_config = global_config or {}

    @property
    def is_initialized(self) -> bool:
        return self._llm_engine is not None

    @property
    def active_session_count(self) -> int:
        return len(self._loops)

    def update_llm_engine(self, new_engine) -> int:
        if not new_engine:
            return 0
        self._llm_engine = new_engine
        updated = 0
        for session_id, meta in list(self._loops.items()):
            loop = meta.agent_loop
            try:
                if hasattr(loop, 'llm'):
                    loop.llm = new_engine
                compactor = getattr(loop, '_session_compactor', None)
                if compactor is not None and hasattr(compactor, 'llm'):
                    compactor.llm = new_engine
                updated += 1
            except Exception as e:
                logger.warning(f"[AgentLoopManager] LLM 推送失败 session={session_id}: {e}")
        logger.info(f"[AgentLoopManager] LLM 引擎已推送到 {updated} 个活跃 loop")
        return updated

    async def get_loop(self, session_id: str) -> Any:
        if session_id in self._loops:
            meta = self._loops[session_id]
            meta.last_active = time.time()
            return meta.agent_loop

        if len(self._loops) >= self._max_loops:
            await self._evict_oldest()

        loop = self._create_loop(session_id)
        self._loops[session_id] = LoopMetadata(
            session_id=session_id,
            created_at=time.time(),
            last_active=time.time(),
            agent_loop=loop,
        )

        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            store.get_or_create_session(session_id)
        except Exception as e:
            logger.warning(f"[AgentLoopManager] Failed to create session record: {e}")

        logger.info(f"[AgentLoopManager] Created new loop for session: {session_id}")
        return loop

    def has_session(self, session_id: str) -> bool:
        return session_id in self._loops

    def get_loop_sync(self, session_id: str) -> Any:
        """同步获取已存在的 loop"""
        meta = self._loops.get(session_id)
        return meta.agent_loop if meta else None

    def get_loop_sync_or_create(self, session_id: str) -> Any:
        meta = self._loops.get(session_id)
        if meta is not None:
            meta.last_active = time.time()
            return meta.agent_loop

        if len(self._loops) >= self._max_loops:
            oldest_session = min(self._loops.items(), key=lambda x: x[1].last_active)
            del self._loops[oldest_session[0]]

        loop = self._create_loop(session_id)
        self._loops[session_id] = LoopMetadata(
            session_id=session_id,
            created_at=time.time(),
            last_active=time.time(),
            agent_loop=loop,
        )
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            store.get_or_create_session(session_id)
        except Exception as e:
            logger.warning(f"[AgentLoopManager] Failed to create session record: {e}")
        return loop

    async def _evict_oldest(self):
        if not self._loops:
            return

        oldest_session = min(self._loops.items(), key=lambda x: x[1].last_active)
        session_id, meta = oldest_session
        loop = meta.agent_loop
        try:
            if hasattr(loop, 'stop'):
                loop.stop()
            if hasattr(loop, 'cleanup'):
                await loop.cleanup()
        except Exception as e:
            logger.warning(f"[AgentLoopManager] Cleanup error for {session_id}: {e}")

        del self._loops[session_id]
        if session_id in self._locks:
            del self._locks[session_id]
        logger.info(f"[AgentLoopManager] Evicted oldest session: {session_id}")

    async def get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def run_with_lock(self, session_id: str, user_input: str) -> Dict[str, Any]:
        # 降级模式：LLM 未配置时直接返回友好提示，不创建 AgentLoop
        if self._llm_engine is None:
            return {
                "error": "llm_not_configured",
                "content": "LLM 引擎未配置。请打开 WebUI 的 Settings → 模型，填写 API Key / Base URL / 模型名 后保存，再发送消息。",
                "success": False,
            }
        lock = await self.get_lock(session_id)
        async with lock:
            loop = await self.get_loop(session_id)
            try:
                result = await loop.run(user_input)
                return result
            except Exception as e:
                logger.error(f"[AgentLoopManager] Session {session_id} run error: {e}")
                return {"error": str(e), "content": "执行出错，请稍后重试"}

    def _create_loop(self, session_id: str) -> Any:
        from app.agent.loop.agent_loop import AgentLoop
        from app.agent.tools.shell_tool import ShellTool

        max_iterations = self._global_config.get("max_iterations", float('inf'))
        flash_mode = self._global_config.get("flash_mode", False)
        enable_heuristics = self._global_config.get("enable_heuristics", True)
        enable_learning = self._global_config.get("enable_learning", True)

        intent_enabled = self._intent_enabled
        intent_llm = self._intent_llm
        if intent_enabled is None:
            try:
                from app.core.util.agent_config import get_config
                _cfg = get_config()
                h_cfg = _cfg.get_section("heuristics") or {}
                intent_cfg = h_cfg.get("intent", {})
                intent_enabled = intent_cfg.get("enabled", True)
                if intent_enabled:
                    model_cfg = intent_cfg.get("model", {})
                    if model_cfg.get("api_key") and model_cfg.get("model"):
                        from app.agent.llm.engine import OpenAICompatibleEngine
                        intent_llm = OpenAICompatibleEngine(
                            api_key=model_cfg["api_key"],
                            base_url=model_cfg.get("base_url", "https://api.openai.com/v1"),
                            model=model_cfg["model"],
                            temperature=float(model_cfg.get("temperature", 0.3)),
                            max_tokens=10,
                            timeout=int(model_cfg.get("timeout", 30)),
                            verify_model=False,
                        )
                self._intent_enabled = intent_enabled
                self._intent_llm = intent_llm
            except Exception as e:
                logger.warning("[AgentLoopManager] 读取意图配置失败，默认禁用: %s", e)
                intent_enabled = False

        session_shell = ShellTool()
        session_tools = dict(self._tools) if self._tools else {}

        return AgentLoop(
            llm_engine=self._llm_engine,
            shell=session_shell,
            memory=None,
            three_layer_memory=self._three_layer_memory,
            tools=session_tools,
            max_iterations=max_iterations,
            session_id=session_id,
            event_bus_instance=None,
            loop_detection_threshold=3,
            enable_heuristics=enable_heuristics,
            flash_mode=flash_mode,
            enable_learning=enable_learning,
            intent_llm_engine=intent_llm,
            intent_enabled=intent_enabled,
        )

    async def cleanup_all(self):
        for session_id in list(self._loops.keys()):
            meta = self._loops[session_id]
            loop = meta.agent_loop
            try:
                if hasattr(loop, 'stop'):
                    loop.stop()
                if hasattr(loop, 'cleanup'):
                    await loop.cleanup()
            except Exception as e:
                logger.warning(f"[AgentLoopManager] Cleanup error for {session_id}: {e}")
        self._loops.clear()
        self._locks.clear()
        logger.info("[AgentLoopManager] All sessions cleaned up")

    def update_all_loops(self, flash_mode: bool = None, max_iterations: int = None, enable_learning: bool = None, intent_llm=None, intent_enabled=None, builtin_tools: Dict = None, refresh_profile: bool = False):
        if intent_enabled is not None:
            self._intent_enabled = intent_enabled
            if intent_enabled and intent_llm is not None:
                self._intent_llm = intent_llm
            elif not intent_enabled:
                self._intent_llm = None
        elif intent_llm is not None:
            self._intent_llm = intent_llm
        if builtin_tools is not None:
            self._tools = dict(builtin_tools)
        if not self._loops:
            return
        updated = 0
        for session_id, meta in list(self._loops.items()):
            loop = meta.agent_loop
            try:
                if hasattr(loop, 'update_config'):
                    loop.update_config(flash_mode=flash_mode, max_iterations=max_iterations, enable_learning=enable_learning, intent_llm=intent_llm, intent_enabled=intent_enabled)
                if hasattr(loop, 'update_tools') and builtin_tools is not None:
                    loop.update_tools(builtin_tools=builtin_tools)
                if refresh_profile and hasattr(loop, 'rebuild_prompt_builder'):
                    loop.rebuild_prompt_builder()
                updated += 1
            except Exception as e:
                logger.warning(f"[AgentLoopManager] Update error for {session_id}: {e}")
        logger.info(f"[AgentLoopManager] 已热更新 {updated} 个 loop")
