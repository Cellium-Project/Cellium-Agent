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
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Any, List, Tuple
from datetime import datetime

from app.messaging.message_broker import get_message_broker
from app.messaging.stream_normalizer import StreamNormalizer

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
    last_event_type: Optional[str] = None
    last_event_id: int = 0
    supplement_count: int = 0


class BackgroundTaskManager:
    """后台任务管理器"""

    # 任务超时时间（秒）- 30 分钟
    TASK_TIMEOUT = 1800

    def __init__(self):
        self.broker = get_message_broker()
        self._tasks: Dict[str, asyncio.Task] = {}
        self._info: Dict[str, TaskInfo] = {}
        self._pending_inputs: Dict[str, str] = {}
        self._supplement_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._normalizers: Dict[str, StreamNormalizer] = {}

    def _ensure_cleanup_task(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            except RuntimeError:
                pass

    async def _cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(60)
                self.cleanup_all_completed()
                from app.agent.loop.session_manager import get_session_manager
                try:
                    get_session_manager().cleanup_expired()
                except Exception as e:
                    logger.debug("[TaskManager] session 清理异常: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[TaskManager] 清理循环异常: %s", e)

    def has_running_task(self, session_id: str) -> bool:
        """检查是否有运行中的任务"""
        info = self._info.get(session_id)
        return info is not None and info.status == TaskStatus.RUNNING

    def get_task_info(self, session_id: str) -> Optional[TaskInfo]:
        """获取任务信息"""
        return self._info.get(session_id)

    def get_queue(self, session_id: str) -> Optional[asyncio.Queue]:
        """获取事件队列（薄壳代理，兼容旧 API）"""
        return self.broker.get_queue(session_id)

    def get_event_history(self, session_id: str, after_event_id: int = 0) -> List[dict]:
        """获取事件历史，可按 event_id 增量过滤（薄壳代理，兼容旧 API）"""
        if after_event_id and after_event_id > 0:
            return self.broker.get_event_history(session_id, after_event_id)
        return self.broker.get_event_history(session_id)

    def get_latest_event_id(self, session_id: str) -> int:
        """获取当前 session 最新事件 ID（薄壳代理，兼容旧 API）"""
        return self.broker.get_latest_event_id(session_id)

    def _apply_terminal_status(self, info: TaskInfo, event_type: Optional[str], *, error_message: Optional[str] = None):
        info.last_event_type = event_type
        info.finished_at = time.time()
        if event_type == "done":
            info.status = TaskStatus.COMPLETED
            info.error_message = None
        elif event_type == "stopped":
            info.status = TaskStatus.CANCELLED
            info.error_message = error_message
        elif event_type == "error":
            info.status = TaskStatus.ERROR
            info.error_message = error_message
        elif error_message:
            info.error_message = error_message

    def get_pending_input(self, session_id: str) -> Optional[str]:
        """获取待处理的用户输入（用于重新连接时恢复）"""
        return self._pending_inputs.get(session_id)

    def enqueue_supplement_message(self, session_id: str, payload: Dict[str, Any]) -> bool:
        """运行中向指定 session 追加补充消息"""
        info = self._info.get(session_id)
        if not info or info.status != TaskStatus.RUNNING:
            return False
        queue = self._supplement_messages.setdefault(session_id, [])
        queue.append(payload)
        info.supplement_count += 1
        return True

    def drain_supplement_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """提取并清空补充消息队列"""
        messages = self._supplement_messages.get(session_id, [])
        self._supplement_messages[session_id] = []
        return messages

    async def start_task(
        self,
        session_id: str,
        agent_loop,
        user_input: str,
        memory,
        three_layer_memory=None,
        system_injection: Optional[str] = None,
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

        self._ensure_cleanup_task()
        # 保留订阅者（TUI 常驻监听器等），只重置任务状态
        self.broker.clear_session(session_id, keep_subscribers=True)
        queue = self.broker.ensure_queue(session_id)
        self._supplement_messages[session_id] = []

        from app.core.util.logger import clear_status_history, clear_runtime_status
        clear_status_history()
        clear_runtime_status()

        from app.server.routes.ws_event_manager import WSConnectionManager
        try:
            ws_manager = WSConnectionManager.get_instance_sync()
            ws_manager.clear_session_events(session_id)
        except Exception:
            pass

        info = TaskInfo(
            session_id=session_id,
            status=TaskStatus.PENDING,
            created_at=time.time(),
            user_input=user_input,
        )
        self._info[session_id] = info

        self._pending_inputs[session_id] = user_input

        task = asyncio.create_task(
            self._run_agent_loop(
                session_id=session_id,
                agent_loop=agent_loop,
                user_input=user_input,
                memory=memory,
                queue=queue,
                system_injection=system_injection,
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
        system_injection: Optional[str] = None,
    ):
        """
        运行 Agent 循环（后台任务）
        """
        info = self._info.setdefault(
            session_id,
            TaskInfo(
                session_id=session_id,
                status=TaskStatus.PENDING,
                created_at=time.time(),
                user_input=user_input,
            )
        )

        info.status = TaskStatus.RUNNING
        info.started_at = time.time()
        
        logger.info("[TaskManager] 开始执行 Agent 循环 | session=%s | input=%s", session_id, user_input[:50] if user_input else "(空)")

        normalizer = self._normalizers.setdefault(session_id, StreamNormalizer())

        try:
            last_terminal_event: Optional[Tuple[str, Optional[str]]] = None
            terminal_hit = False
            async for raw_event in agent_loop.run_stream(
                user_input,
                memory=memory,
                session_id=session_id,
                system_injection=system_injection,
            ):
                for norm in normalizer.feed(raw_event):
                    if terminal_hit:
                        continue
                    self.broker.publish(session_id, norm)
                    event = self.broker.get_event_history(session_id)[-1]
                    info.last_event_id = int(event.get("event_id", 0) or 0)
                    if event.get("type") == "tool_start":
                        info.iteration += 1
                        logger.debug("[TaskManager] 工具调用 | session=%s | iteration=%d", session_id, info.iteration)

                    info.event_count = len(self.broker.get_event_history(session_id))

                    if event.get("type") in ("done", "error", "stopped"):
                        logger.info("[TaskManager] 收到完成事件 | session=%s | type=%s", session_id, event.get("type"))
                        last_terminal_event = (event.get("type"), event.get("error") or event.get("reason"))
                        terminal_hit = True

            if last_terminal_event:
                event_type, error_message = last_terminal_event
                self._apply_terminal_status(info, event_type, error_message=error_message)
            else:
                self._apply_terminal_status(info, "done")
            logger.info(
                "[TaskManager] 任务完成 | session=%s | status=%s | iterations=%d | events=%d",
                session_id, info.status.value, info.iteration, info.event_count
            )

        except asyncio.CancelledError:
            self.broker.publish(session_id, {"type": "stopped", "reason": "user_cancelled"})
            cancel_event = self.broker.get_event_history(session_id)[-1]
            info.event_count = len(self.broker.get_event_history(session_id))
            info.last_event_id = int(cancel_event.get("event_id", 0) or 0)
            self._apply_terminal_status(info, "stopped", error_message="user_cancelled")
            logger.info("[TaskManager] 任务已取消 | session=%s", session_id)

        except Exception as e:
            self.broker.publish(session_id, {"type": "error", "error": str(e)})
            error_event = self.broker.get_event_history(session_id)[-1]
            info.event_count = len(self.broker.get_event_history(session_id))
            info.last_event_id = int(error_event.get("event_id", 0) or 0)
            self._apply_terminal_status(info, "error", error_message=str(e))
            logger.error("[TaskManager] 任务出错 | session=%s | error=%s", session_id, e, exc_info=True)

        finally:
            try:
                for norm in normalizer.finish():
                    self.broker.publish(session_id, norm)
            except Exception:
                pass
            try:
                self.broker.publish_terminal(session_id)
            except Exception:
                pass
            await queue.put(None)
            self._pending_inputs.pop(session_id, None)
            task_mgr = get_task_manager()
            info = task_mgr.get_task_info(session_id)
            if info and info.status.value in ("completed", "cancelled", "error"):
                from app.agent.loop.session_manager import get_session_manager
                session_mgr = get_session_manager()
                session_info = session_mgr.get_or_create(session_id)
                session_info.message_count += 1
                from app.agent.loop.session_store import get_session_store
                store = get_session_store()
                store.update_message_count(session_id, delta=1)

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

        try:
            from app.agent.loop.agent_loop_manager import AgentLoopManager
            mgr = AgentLoopManager.get_instance()
            loop = mgr.get_loop_sync(session_id)
            if loop is not None and hasattr(loop, "stop"):
                loop.stop()
        except Exception as e:
            logger.warning("[TaskManager] 触发 AgentLoop 停止失败: %s", e)

        task.cancel()
        logger.info("[TaskManager] 任务取消请求已发送 | session=%s", session_id)
        return True

    def cleanup_task(self, session_id: str):
        """
        清理已完成的任务资源
        """
        self._tasks.pop(session_id, None)
        self._pending_inputs.pop(session_id, None)
        self._supplement_messages.pop(session_id, None)
        self._normalizers.pop(session_id, None)
        self.broker.clear_session(session_id)
        logger.debug("[TaskManager] 任务资源已清理 | session=%s", session_id)

    def cleanup_all_completed(self):
        """清理所有已完成的任务"""
        to_cleanup = []
        for session_id, info in self._info.items():
            if info.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR):
                if info.finished_at and (time.time() - info.finished_at) > 300:
                    to_cleanup.append(session_id)

        for session_id in to_cleanup:
            self._tasks.pop(session_id, None)
            self._info.pop(session_id, None)
            self._pending_inputs.pop(session_id, None)
            self._supplement_messages.pop(session_id, None)
            self._normalizers.pop(session_id, None)
            self.broker.clear_session(session_id)

        if to_cleanup:
            logger.info("[TaskManager] 清理了 %d 个已完成任务", len(to_cleanup))

_task_manager: Optional[BackgroundTaskManager] = None

def get_task_manager() -> BackgroundTaskManager:
    """获取全局任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = BackgroundTaskManager()
    return _task_manager
