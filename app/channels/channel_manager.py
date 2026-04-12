# -*- coding: utf-8 -*-
"""
ChannelManager - 多平台通道协调器
统一管理所有 ChannelAdapter，处理消息路由
支持异步 handler、消息队列、背压控制
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Callable, Any
from collections import defaultdict
from .base import ChannelAdapter, UnifiedMessage

logger = logging.getLogger(__name__)


class MessageQueue:
    def __init__(self, max_size: int = 1000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._running = False
        self._workers: List[asyncio.Task] = []
        self._rate_limiter: Dict[str, List[float]] = defaultdict(list)
        self._rate_limit = 20  # 每用户每分钟最多20条

    async def put(self, message: UnifiedMessage) -> bool:
        if self._check_rate_limit(message.user_id):
            try:
                self._queue.put_nowait(message)
                return True
            except asyncio.QueueFull:
                logger.warning(f"[Queue] Full, dropping message from {message.user_id}")
                return False
        else:
            logger.warning(f"[Queue] Rate limited: {message.user_id}")
            return False

    def _check_rate_limit(self, user_id: str) -> bool:
        now = time.time()
        self._rate_limiter[user_id] = [
            t for t in self._rate_limiter[user_id] if now - t < 60
        ]
        if len(self._rate_limiter[user_id]) >= self._rate_limit:
            return False
        self._rate_limiter[user_id].append(now)
        return True

    async def start(self, handler: Callable, num_workers: int = 4):
        self._running = True
        for i in range(num_workers):
            task = asyncio.create_task(self._worker(handler, i))
            self._workers.append(task)
        logger.info(f"[Queue] Started {num_workers} workers")

    async def _worker(self, handler: Callable, worker_id: int):
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                asyncio.create_task(self._process(handler, message, worker_id))
            except asyncio.TimeoutError:
                continue

    async def _process(self, handler: Callable, message: UnifiedMessage, worker_id: int):
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(message)
            else:
                handler(message)
        except Exception as e:
            logger.error(f"[Queue] Worker {worker_id} error: {e}")

    async def stop(self):
        self._running = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("[Queue] Stopped")


class ChannelManager:
    _instance: Optional['ChannelManager'] = None

    def __init__(self):
        self._adapters: Dict[str, ChannelAdapter] = {}
        self._global_handler: Optional[Callable[[UnifiedMessage], None]] = None
        self._running = False
        self._message_queue: Optional[MessageQueue] = None
        self._agent_loop_manager = None

    @classmethod
    def get_instance(cls) -> 'ChannelManager':
        if cls._instance is None:
            cls._instance = ChannelManager()
        return cls._instance

    def register_adapter(self, adapter: ChannelAdapter):
        adapter.set_message_handler(self._on_message)
        self._adapters[adapter.platform_name] = adapter
        logger.info(f"[ChannelManager] Registered adapter: {adapter.platform_name}")

    def set_global_handler(self, handler: Callable[[UnifiedMessage], None]):
        self._global_handler = handler

    def set_agent_loop_manager(self, manager):
        self._agent_loop_manager = manager
        logger.info(f"[ChannelManager] AgentLoopManager set")

    async def start_all(self, with_queue: bool = True, queue_workers: int = 4):
        self._running = True
        if with_queue and self._global_handler:
            self._message_queue = MessageQueue()
            await self._message_queue.start(self._global_handler, queue_workers)
        for adapter in self._adapters.values():
            asyncio.create_task(self._run_adapter(adapter))
        logger.info(f"[ChannelManager] Started {len(self._adapters)} adapters")

    async def _run_adapter(self, adapter: ChannelAdapter):
        while self._running:
            try:
                await adapter.connect()
            except Exception as e:
                logger.error(f"[ChannelManager] {adapter.platform_name} error: {e}")
                await asyncio.sleep(5)

    async def stop_all(self):
        self._running = False
        if self._message_queue:
            await self._message_queue.stop()
        for adapter in self._adapters.values():
            await adapter.disconnect()
        logger.info("[ChannelManager] Stopped all adapters")

    async def _on_message(self, message: UnifiedMessage):
        logger.info(f"[ChannelManager] Message from {message.platform}: {message.content[:50]}...")

        if message.content.strip() == "/stop":
            session_id = message.session_id
            try:
                if not self._agent_loop_manager.has_session(session_id):
                    await self.send_message(
                        message.platform,
                        message.user_id,
                        "⏹ 当前没有正在运行的 Agent 会话",
                        message.message_type,
                    )
                    return
                loop = await self._agent_loop_manager.get_loop(session_id)
                loop.stop()
                await self.send_message(
                    message.platform,
                    message.user_id,
                    "⏹ 已发送停止请求，Agent 将在当前迭代结束后停止",
                    message.message_type,
                )
                logger.info(f"[ChannelManager] /stop 请求已发送至 session={session_id}")
            except Exception as e:
                logger.error(f"[ChannelManager] /stop 处理失败: {e}")
                await self.send_message(
                    message.platform,
                    message.user_id,
                    f"停止失败: {e}",
                    message.message_type,
                )
            return

        if self._agent_loop_manager:
            session_id = message.session_id
            adapter = self._adapters.get(message.platform)
            system_injection = adapter.build_inject_content(message, message.content) if adapter else None
            content_to_agent = message.content
            try:
                # 从 SessionManager 获取 memory（会自动从归档恢复历史）
                from app.agent.loop.session_manager import get_session_manager
                session_mgr = get_session_manager()
                session_info = session_mgr.get_or_create(session_id)
                session_memory = session_info.memory

                lock = await self._agent_loop_manager.get_lock(session_id)
                async with lock:
                    loop = await self._agent_loop_manager.get_loop(session_id)
                    sent_any = False
                    pending = ""

                    MAX_MSG_LEN = 1000

                    async def safe_send(content: str):
                        if not content:
                            return
                        chunks = [content[i:i+MAX_MSG_LEN] for i in range(0, len(content), MAX_MSG_LEN)]
                        for chunk in chunks:
                            try:
                                await self.send_message(
                                    message.platform,
                                    message.user_id,
                                    chunk,
                                    message.message_type,
                                    markdown=True,
                                )
                            except Exception as e:
                                logger.warning(f"[ChannelManager] Failed to send message chunk: {e}")

                    try:
                        async for event in loop.run_stream(content_to_agent, memory=session_memory, session_id=session_id, system_injection=system_injection):
                            try:
                                event_type = event.get("type")
                                if event_type == "thinking":
                                    thinking_content = event.get("content", "Thinking...")
                                    if thinking_content:
                                        await safe_send(f"> 💭 **Thinking**: {thinking_content}")
                                elif event_type == "error":
                                    error_msg = event.get("error", "未知错误")
                                    await safe_send(f"> ❌ **错误**: `{error_msg}`")
                                elif event_type == "tool_start":
                                    tool_name = event.get("tool", "unknown")
                                    desc = event.get("description", "")
                                    tool_info = f"### 🔧 正在调用 {tool_name}"
                                    if desc:
                                        tool_info += f"\n\n> {desc}"
                                    await safe_send(tool_info)
                                elif event_type == "tool_result":
                                    tool_name = event.get("tool", "unknown")
                                    result = event.get("result", {})
                                    duration = event.get("duration_ms", 0)
                                    content_str = result.get("content", "") if isinstance(result, dict) else str(result)
                                    if len(content_str) > 300:
                                        content_str = content_str[:300] + "..."
                                    await safe_send(f"> ## ✅ **{tool_name}** 耗时 {duration}ms")
                                elif event_type == "content_chunk":
                                    await safe_send(event["content"])
                                    sent_any = True
                                elif event_type == "done":
                                    if sent_any and pending:
                                        await safe_send(pending)
                                        pending = ""
                                    elif not sent_any:
                                        await safe_send(event.get("content", "..."))
                            except Exception as e:
                                logger.warning(f"[ChannelManager] Failed to handle stream event {event.get('type')}: {e}")
                    except Exception as e:
                        logger.error(f"[ChannelManager] Stream broken for session {session_id}: {e}")
                        if pending:
                            await safe_send(pending)
                            pending = ""

                self._update_session_message_count(session_id)
            except Exception as e:
                logger.error(f"[ChannelManager] Agent loop error for session {session_id}: {e}")
                error_content = (await self._handle_message_failure(session_id, e)).get("content", "处理消息时发生错误，请稍后重试")
                await self.send_message(
                    message.platform,
                    message.user_id,
                    error_content,
                    message.message_type,
                    msg_id=message.msg_id,
                )
            return

        if self._message_queue:
            await self._message_queue.put(message)
        elif self._global_handler:
            if asyncio.iscoroutinefunction(self._global_handler):
                await self._global_handler(message)
            else:
                self._global_handler(message)

    async def _execute_agent_with_retry(self, session_id: str, content: str, max_retries: int = 3):
        last_exception = None
        for attempt in range(max_retries):
            try:
                loop = await self._agent_loop_manager.get_loop(session_id)
                result = await loop.run(content)
                logger.info(f"[ChannelManager] Agent response: {str(result)[:100]}...")
                return result
            except Exception as e:
                last_exception = e
                logger.warning(f"[ChannelManager] Agent execution failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        logger.error(f"[ChannelManager] Agent execution failed after {max_retries} attempts: {last_exception}")
        return await self._handle_message_failure(session_id, last_exception)

    async def _handle_message_failure(self, session_id: str, error: Exception) -> Dict[str, Any]:
        logger.error(f"[ChannelManager] Handling message failure for session {session_id}: {error}")
        return {"content": "处理消息时发生错误，请稍后重试"}

    def _update_session_message_count(self, session_id: str, delta: int = 2):
        """更新会话消息计数（用户+助手各一条）"""
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            store.update_message_count(session_id, delta=delta)
            logger.debug(f"[ChannelManager] Updated message count for session {session_id}")
        except Exception as e:
            logger.warning(f"[ChannelManager] Failed to update message count: {e}")

    async def send_message(self, platform: str, user_id: str, content: str,
                          message_type: str = "c2c", **kwargs) -> bool:
        adapter = self._adapters.get(platform)
        if not adapter:
            logger.error(f"[ChannelManager] No adapter for platform: {platform}")
            return False
        return await adapter.send_message(user_id, content, message_type, **kwargs)

    def get_adapter(self, platform: str) -> Optional[ChannelAdapter]:
        return self._adapters.get(platform)

    def list_platforms(self) -> List[str]:
        return list(self._adapters.keys())

    async def reload_channel(self, platform: str, new_config: Dict[str, Any]) -> bool:
        adapter = self._adapters.get(platform)
        if not adapter:
            logger.warning(f"[ChannelManager] No adapter to reload: {platform}")
            return False

        try:
            if hasattr(adapter, 'update_config'):
                await adapter.update_config(**new_config)
                logger.info(f"[ChannelManager] Channel reloaded: {platform}")
                return True
            else:
                logger.warning(f"[ChannelManager] Adapter {platform} does not support reload")
                return False
        except Exception as e:
            logger.error(f"[ChannelManager] Reload failed for {platform}: {e}")
            return False

    @property
    def is_running(self) -> bool:
        return self._running
