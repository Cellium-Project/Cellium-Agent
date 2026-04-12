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

    @classmethod
    def get_instance(cls) -> 'AgentLoopManager':
        if cls._instance is None:
            cls._instance = AgentLoopManager()
        return cls._instance

    def initialize(self, llm_engine, shell, three_layer_memory, tools: Dict = None, global_config: Dict = None):
        if not llm_engine:
            raise ValueError("llm_engine is required")
        if not shell:
            raise ValueError("shell is required")
        if not three_layer_memory:
            raise ValueError("three_layer_memory is required")
        self._llm_engine = llm_engine
        self._shell = shell
        self._three_layer_memory = three_layer_memory
        self._tools = tools or {}
        self._global_config = global_config or {}
        logger.info("[AgentLoopManager] Initialized")

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

        max_iterations = self._global_config.get("max_iterations", float('inf'))
        flash_mode = self._global_config.get("flash_mode", False)
        enable_heuristics = self._global_config.get("enable_heuristics", True)
        enable_learning = self._global_config.get("enable_learning", True)

        return AgentLoop(
            llm_engine=self._llm_engine,
            shell=self._shell,
            memory=None,
            three_layer_memory=self._three_layer_memory,
            tools=self._tools,
            max_iterations=max_iterations,
            session_id=session_id,
            event_bus_instance=None,
            loop_detection_threshold=3,
            enable_heuristics=enable_heuristics,
            flash_mode=flash_mode,
            enable_learning=enable_learning,
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
