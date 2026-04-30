# -*- coding: utf-8 -*-
"""
SchedulerExecutor - 定时任务执行器（全局单例）

职责:
  1. 从 SchedulerManager 获取任务
  2. 通过 AgentLoopManager 获取正确的 session AgentLoop
  3. 使用 per-session 锁保证并发安全
  4. 调用 AgentLoop.run_scheduler_task 执行任务
"""

import asyncio
import logging
from typing import Optional, Any, Dict

from .manager import get_scheduler_manager

logger = logging.getLogger(__name__)


class SchedulerExecutor:
    """定时任务执行器 - 全局单例，通过 AgentLoopManager 路由"""

    _instance: Optional['SchedulerExecutor'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._manager = get_scheduler_manager()
        self._loop_manager: Optional[Any] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._initialized = True

    def start(self, loop_manager: Any):
        """启动执行器

        Args:
            loop_manager: AgentLoopManager 实例，用于获取正确的 session AgentLoop
        """
        if self._running:
            return

        if not loop_manager:
            logger.error("[SchedulerExecutor] 启动失败: loop_manager 未提供")
            return

        self._loop_manager = loop_manager
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[SchedulerExecutor] 已启动（全局单例）")

    async def stop(self):
        """停止执行器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[SchedulerExecutor] 已停止")

    async def _loop(self):
        """主循环 - 检查并执行任务"""
        loop_count = 0
        retry_delay = 0
        while self._running:
            loop_count += 1
            if loop_count == 1 or loop_count % 60 == 0:
                logger.debug(f"[SchedulerExecutor] 执行循环运行中 (第{loop_count}次)")

            try:
                if self._manager.has_pending_task():
                    result = await self._execute_next()
                    if not result and self._manager.has_pending_task():
                        retry_delay = min(retry_delay + 1, 5)
                    else:
                        retry_delay = 0
            except Exception as e:
                logger.error(f"[SchedulerExecutor] 执行出错: {e}")
                import traceback
                logger.error(traceback.format_exc())

            await asyncio.sleep(1 + retry_delay)

        logger.info("[SchedulerExecutor] 执行循环已停止")

    def _build_scheduler_context(self, task) -> str:
        """构建定时任务上下文"""
        return f"""[定时任务触发]
任务ID: {task.task_id}
任务名称: {task.task_name}
触发时间: {task.triggered_at}
执行次数: {task.run_count}

任务内容:
{task.prompt}

---
请执行上述定时任务。"""

    async def _execute_via_platform_channel(self, task, session_id: str, agent_loop, platform_context: dict) -> bool:
        """
        通过外部平台通道执行定时任务
        
        Returns:
            True 表示成功启动，False 表示失败
        """
        try:
            from app.server.task_manager import get_task_manager
            from app.channels import ChannelManager
            from app.agent.loop.session_manager import get_session_manager
            
            task_mgr = get_task_manager()
            channel_mgr = ChannelManager.get_instance()
            session_mgr = get_session_manager()
            session_info = session_mgr.get_or_create(session_id)
            
            scheduler_context = self._build_scheduler_context(task)
            
            started = await task_mgr.start_task(
                session_id=session_id,
                agent_loop=agent_loop,
                user_input=scheduler_context,
                memory=session_info.memory,
            )
            
            if not started:
                raise RuntimeError("无法启动任务")
            
            queue = task_mgr.get_queue(session_id)
            if queue is None:
                raise RuntimeError("任务队列不可用")
            
            task_info = {
                "task_id": task.task_id,
                "task_name": task.task_name,
            }
            channel_mgr.start_scheduler_task_queue_consumer(platform_context, session_id, queue, task_info)
            
            logger.info(f"[SchedulerExecutor] 定时任务已通过外部平台启动 | session={session_id} | platform={platform_context.get('platform')}")
            
            self._manager.mark_completed(task.task_id, {"status": "started"})
            return True
            
        except Exception as e:
            logger.error(f"[SchedulerExecutor] 外部平台执行失败 | session={session_id} | error={e}")
            self._manager.mark_failed(task.task_id, f"外部平台执行失败: {e}")
            return False

    async def _execute_via_websocket(self, task, session_id: str, agent_loop) -> bool:
        """通过 WebSocket 执行定时任务"""
        try:
            lock = await self._loop_manager.get_lock(session_id)
            async with lock:
                result = await agent_loop.run_scheduler_task({
                    "task_id": task.task_id,
                    "task_name": task.task_name,
                    "prompt": task.prompt,
                    "triggered_at": task.triggered_at,
                    "run_count": task.run_count,
                }, session_id=session_id)

            self._manager.mark_completed(task.task_id, result)
            logger.debug(f"[SchedulerExecutor] 任务完成: {task.task_name}")
            return True
            
        except Exception as e:
            error_msg = str(e)
            if "session_busy:" in error_msg:
                logger.debug(f"[SchedulerExecutor] 任务 {task.task_name} 等待 session 空闲")
                self._manager.requeue_task(task)
                return False
            logger.error(f"[SchedulerExecutor] 任务执行失败: {e}")
            self._manager.mark_failed(task.task_id, error_msg)
            return False

    async def _execute_next(self) -> bool:
        """执行下一个待处理任务（路由到正确的 session AgentLoop）"""
        task = self._manager.get_next_task()
        if not task:
            return False

        task_config = self._manager.get_task(task.task_id)
        session_id = task_config.session_id if task_config else None
        platform_context = task_config.platform_context if task_config else None

        if not session_id:
            session_id = "default"

        if not self._loop_manager:
            logger.error("[SchedulerExecutor] loop_manager 未设置")
            self._manager.mark_failed(task.task_id, "loop_manager 未设置")
            return False

        if not platform_context:
            try:
                from app.agent.loop.session_manager import get_session_manager
                session_mgr = get_session_manager()
                session_info = session_mgr.get_or_create(session_id)
                if hasattr(session_info, "platform_context") and session_info.platform_context:
                    platform_context = session_info.platform_context
                    logger.debug(f"[SchedulerExecutor] 从 session 恢复 platform_context | session={session_id}")
                else:
                    platform_context = self._extract_platform_context_from_session_id(session_id)
                    if platform_context:
                        session_info.platform_context = platform_context
                        logger.debug(f"[SchedulerExecutor] 从 session_id 构建 platform_context | session={session_id}")
            except Exception as e:
                logger.warning(f"[SchedulerExecutor] 恢复 platform_context 失败: {e}")

        logger.debug(f"[SchedulerExecutor] 执行任务: {task.task_name} ({session_id})")

        try:
            agent_loop = await self._loop_manager.get_loop(session_id)
            if not agent_loop:
                logger.error(f"[SchedulerExecutor] 无法获取 AgentLoop 实例 | session={session_id}")
                self._manager.mark_failed(task.task_id, "无法获取 AgentLoop 实例")
                return False

            if platform_context:
                return await self._execute_via_platform_channel(task, session_id, agent_loop, platform_context)
            else:
                return await self._execute_via_websocket(task, session_id, agent_loop)

        except Exception as e:
            error_msg = str(e)
            if "session_busy:" in error_msg:
                logger.debug(f"[SchedulerExecutor] 任务 {task.task_name} 等待 session 空闲")
                self._manager.requeue_task(task)
                return False
            logger.error(f"[SchedulerExecutor] 任务执行失败: {e}")
            self._manager.mark_failed(task.task_id, error_msg)
            return False

    def _extract_platform_context_from_session_id(self, session_id: str) -> Optional[Dict[str, Any]]:
        """从 session_id 中提取平台上下文
        
        session_id 格式:
        - qq:xxx (QQ 私聊)
        - qq:group:xxx (QQ 群聊)
        - telegram:xxx (Telegram 私聊)
        """
        if not session_id or ":" not in session_id:
            return None
        
        parts = session_id.split(":")
        platform = parts[0]
        
        if platform == "qq":
            if len(parts) >= 3 and parts[1] == "group":
                return {
                    "platform": "qq",
                    "user_id": parts[2] if len(parts) > 2 else "",
                    "group_id": parts[2] if len(parts) > 2 else "",
                    "message_type": "group",
                }
            else:
                return {
                    "platform": "qq",
                    "user_id": parts[1] if len(parts) > 1 else "",
                    "message_type": "c2c",
                }
        elif platform == "telegram":
            return {
                "platform": "telegram",
                "user_id": parts[1] if len(parts) > 1 else "",
                "message_type": "c2c",
            }
        
        return None


_executor: Optional[SchedulerExecutor] = None


def start_executor(loop_manager: Any):
    """启动全局 SchedulerExecutor

    Args:
        loop_manager: AgentLoopManager 实例
    """
    global _executor
    if _executor is None:
        _executor = SchedulerExecutor()
    _executor.start(loop_manager)


def get_executor() -> Optional[SchedulerExecutor]:
    """获取全局 SchedulerExecutor 实例"""
    return _executor
