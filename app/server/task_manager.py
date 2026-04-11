# -*- coding: utf-8 -*-
"""
后台任务管理器

将 Agent 循环与 HTTP 请求解耦，支持：
  - 后台运行 agent 任务
  - 事件队列缓冲 + 事件历史保存
  - 页面刷新后重新连接（不丢失历史事件）
  - 任务状态查询和取消
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Any, List
from datetime import datetime


logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"       # 等待启动
    RUNNING = "running"       # 正在运行
    COMPLETED = "completed"   # 已完成
    CANCELLED = "cancelled"   # 已取消
    ERROR = "error"          # 出错


@dataclass
class TaskInfo:
    """任务信息"""
    session_id: str
    status: TaskStatus
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error_message: Optional[str] = None
    iteration: int = 0
    event_count: int = 0  # 已产生的事件数量
    user_input: str = ""  # 用户输入


class BackgroundTaskManager:
    """后台任务管理器"""

    # 最大事件历史长度（防止内存溢出）
    MAX_HISTORY_SIZE = 500

    # 任务超时时间（秒）- 30 分钟
    TASK_TIMEOUT = 1800

    def __init__(self):
        # session_id -> asyncio.Task
        self._tasks: Dict[str, asyncio.Task] = {}
        # session_id -> asyncio.Queue (实时事件队列)
        self._queues: Dict[str, asyncio.Queue] = {}
        # session_id -> TaskInfo
        self._info: Dict[str, TaskInfo] = {}
        # session_id -> 事件历史列表（用于重新连接时恢复）
        self._event_history: Dict[str, List[dict]] = {}
        # session_id -> 当前用户输入
        self._pending_inputs: Dict[str, str] = {}

    def has_running_task(self, session_id: str) -> bool:
        """检查是否有运行中的任务"""
        info = self._info.get(session_id)
        return info is not None and info.status == TaskStatus.RUNNING

    def get_task_info(self, session_id: str) -> Optional[TaskInfo]:
        """获取任务信息"""
        return self._info.get(session_id)

    def get_queue(self, session_id: str) -> Optional[asyncio.Queue]:
        """获取事件队列"""
        return self._queues.get(session_id)

    def get_event_history(self, session_id: str) -> List[dict]:
        """获取事件历史"""
        return self._event_history.get(session_id, [])

    async def start_task(
        self,
        session_id: str,
        agent_loop,
        user_input: str,
        memory,
        three_layer_memory=None,
    ) -> bool:
        """
        启动后台任务

        Args:
            session_id: 会话 ID
            agent_loop: AgentLoop 实例
            user_input: 用户输入
            memory: MemoryManager 实例
            three_layer_memory: 三层记忆系统

        Returns:
            是否成功启动（如果已有运行中的任务则返回 False）
        """
        if self.has_running_task(session_id):
            logger.warning("[TaskManager] session=%s 已有运行中的任务", session_id)
            return False

        # 创建事件队列
        queue = asyncio.Queue(maxsize=100)
        self._queues[session_id] = queue

        # 创建事件历史列表
        self._event_history[session_id] = []

        # 创建任务信息
        info = TaskInfo(
            session_id=session_id,
            status=TaskStatus.PENDING,
            created_at=time.time(),
            user_input=user_input,
        )
        self._info[session_id] = info

        # 保存用户输入
        self._pending_inputs[session_id] = user_input

        # 创建后台任务
        task = asyncio.create_task(
            self._run_agent_loop(
                session_id=session_id,
                agent_loop=agent_loop,
                user_input=user_input,
                memory=memory,
                queue=queue,
            )
        )
        self._tasks[session_id] = task

        logger.info("[TaskManager] 后台任务已启动 | session=%s | input=%s", session_id, user_input[:50] if user_input else "(空)")
        return True

    async def _run_agent_loop(
        self,
        session_id: str,
        agent_loop,
        user_input: str,
        memory,
        queue: asyncio.Queue,
    ):
        """
        运行 Agent 循环（后台任务）
        """
        # 使用 setdefault 避免 race condition，无需 WARNING
        info = self._info.setdefault(
            session_id,
            TaskInfo(
                session_id=session_id,
                status=TaskStatus.PENDING,
                created_at=time.time(),
                user_input=user_input,
            )
        )
        history = self._event_history.setdefault(session_id, [])

        info.status = TaskStatus.RUNNING
        info.started_at = time.time()
        
        logger.info("[TaskManager] 开始执行 Agent 循环 | session=%s | input=%s", session_id, user_input[:50] if user_input else "(空)")

        try:
            async for event in agent_loop.run_stream(
                user_input,
                memory=memory,
                session_id=session_id,
            ):
                # 更新迭代计数
                if event.get("type") == "tool_start":
                    info.iteration += 1
                    logger.debug("[TaskManager] 工具调用 | session=%s | iteration=%d", session_id, info.iteration)

                # ★ 保存到事件历史
                history.append(event)
                # 限制历史长度
                if len(history) > self.MAX_HISTORY_SIZE:
                    # 保留最后 400 条
                    history[:] = history[-400:]

                info.event_count = len(history)

                # 放入实时队列（供当前连接的客户端消费）
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # 队列满，丢弃最旧的
                    try:
                        queue.get_nowait()
                        queue.put_nowait(event)
                    except Exception:
                        pass

                # 检查是否完成
                if event.get("type") in ("done", "error", "stopped"):
                    logger.info("[TaskManager] 收到完成事件 | session=%s | type=%s", session_id, event.get("type"))
                    break

            info.status = TaskStatus.COMPLETED
            info.finished_at = time.time()
            logger.info(
                "[TaskManager] 任务完成 | session=%s | iterations=%d | events=%d",
                session_id, info.iteration, info.event_count
            )

        except asyncio.CancelledError:
            info.status = TaskStatus.CANCELLED
            info.finished_at = time.time()
            # 发送取消事件
            cancel_event = {"type": "stopped", "reason": "user_cancelled"}
            history.append(cancel_event)
            await queue.put(cancel_event)
            logger.info("[TaskManager] 任务已取消 | session=%s", session_id)

        except Exception as e:
            info.status = TaskStatus.ERROR
            info.finished_at = time.time()
            info.error_message = str(e)
            # 发送错误事件
            error_event = {"type": "error", "error": str(e)}
            history.append(error_event)
            await queue.put(error_event)
            logger.error("[TaskManager] 任务出错 | session=%s | error=%s", session_id, e, exc_info=True)

        finally:
            # 发送结束标记
            await queue.put(None)
            # 清理用户输入
            self._pending_inputs.pop(session_id, None)

    def cancel_task(self, session_id: str) -> bool:
        """
        取消运行中的任务

        Returns:
            是否成功取消
        """
        task = self._tasks.get(session_id)
        info = self._info.get(session_id)

        if not task or not info:
            return False

        if info.status != TaskStatus.RUNNING:
            return False

        task.cancel()
        logger.info("[TaskManager] 任务取消请求已发送 | session=%s", session_id)
        return True

    def cleanup_task(self, session_id: str):
        """
        清理已完成的任务资源
        """
        self._tasks.pop(session_id, None)
        self._queues.pop(session_id, None)
        self._pending_inputs.pop(session_id, None)
        # 清理事件历史
        self._event_history.pop(session_id, None)
        # 保留 info 一段时间（用于状态查询）
        logger.debug("[TaskManager] 任务资源已清理 | session=%s", session_id)

    def cleanup_all_completed(self):
        """清理所有已完成的任务"""
        to_cleanup = []
        for session_id, info in self._info.items():
            if info.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR):
                # 任务完成超过 5 分钟后清理
                if info.finished_at and (time.time() - info.finished_at) > 300:
                    to_cleanup.append(session_id)

        for session_id in to_cleanup:
            self._tasks.pop(session_id, None)
            self._queues.pop(session_id, None)
            self._info.pop(session_id, None)
            self._pending_inputs.pop(session_id, None)
            self._event_history.pop(session_id, None)

        if to_cleanup:
            logger.info("[TaskManager] 清理了 %d 个已完成任务", len(to_cleanup))


# 全局单例
_task_manager: Optional[BackgroundTaskManager] = None


def get_task_manager() -> BackgroundTaskManager:
    """获取全局任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = BackgroundTaskManager()
    return _task_manager
