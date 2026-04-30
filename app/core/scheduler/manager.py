# -*- coding: utf-8 -*-
"""
SchedulerManager - 定时任务管理中间层

职责:
  1. 加载和保存任务
  2. 检查时间并触发任务
  3. 管理任务队列 (待处理、处理中、已完成)
  4. 调度任务执行
  5. 记录执行历史
"""

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass, asdict, field
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ScheduledTask:
    """调度任务包装类"""
    task_id: str
    task_name: str
    prompt: str
    triggered_at: str
    run_count: int
    status: str = "pending"
    created_at: str = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict] = None
    error: Optional[str] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()


@dataclass
class TaskConfig:
    """任务配置（用于调度）"""
    id: str
    name: str
    type: str
    config: Dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    created_at: str = ""
    next_run: str = ""
    last_run: Optional[str] = None
    run_count: int = 0
    enabled: bool = True
    session_id: Optional[str] = None
    platform_context: Optional[Dict[str, Any]] = None


class SchedulerManager:
    """
    调度任务管理器 - 中间层
    
    功能:
      - 加载和保存任务配置
      - 检查时间并触发任务
      - 维护任务队列
      - 提供任务给消费者 (AgentLoop)
      - 记录执行结果
    """
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if SchedulerManager._initialized:
            return
        
        self._tasks: Dict[str, TaskConfig] = {}
        self._tasks_storage = Path("data/scheduler.json")
        self._tasks_storage.parent.mkdir(parents=True, exist_ok=True)
        
        self._pending_queue: List[ScheduledTask] = []
        self._processing: Dict[str, ScheduledTask] = {}
        self._history: List[ScheduledTask] = []
        self._max_history = 100
        
        self._storage_path = Path("data/scheduler_history.json")
        
        self._running = False
        self._loop_task = None
        
        SchedulerManager._initialized = True
        
    def start(self):
        """启动管理器（同步部分）"""
        if self._running:
            logger.debug("[SchedulerManager] 已在运行中，跳过重复启动")
            return
        self._load_tasks()
        self._load_history()
        self._running = True
        logger.info(f"[SchedulerManager] 已初始化，{len(self._tasks)}个任务")
    
    async def start_loop(self):
        """启动调度循环（异步部分，需在事件循环中调用）"""
        if self._loop_task is not None:
            return
        self._loop_task = asyncio.create_task(self._scheduler_loop())
    
    def stop(self):
        """停止管理器"""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
        self._save_tasks()
        self._save_history()
        logger.info("[SchedulerManager] 已停止")
    
    def _load_tasks(self):
        """加载任务配置"""
        if self._tasks_storage.exists():
            try:
                data = json.loads(self._tasks_storage.read_text(encoding="utf-8"))
                for t in data.get("tasks", []):
                    task = TaskConfig(**t)
                    self._tasks[task.id] = task
                logger.info(f"[SchedulerManager] 已加载 {len(self._tasks)} 个任务")
            except Exception as e:
                logger.error(f"[SchedulerManager] 加载任务失败: {e}")
    
    def _save_tasks(self):
        """保存任务配置"""
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "tasks": [asdict(t) for t in self._tasks.values()]
            }
            self._tasks_storage.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"[SchedulerManager] 保存任务失败: {e}")
    
    async def _scheduler_loop(self):
        """调度循环 - 每秒检查一次任务"""
        logger.info(f"[SchedulerManager] 调度循环已启动 | 任务数:{len(self._tasks)}")
        while self._running:
            try:
                await self._check_and_trigger_tasks()
            except Exception as e:
                logger.error(f"[SchedulerManager] 检查任务出错: {e}")
                import traceback
                logger.error(traceback.format_exc())
            await asyncio.sleep(1)
        logger.info("[SchedulerManager] 调度循环已停止")
    
    async def _check_and_trigger_tasks(self):
        """检查并触发到期任务"""
        now = datetime.now()
        for task in list(self._tasks.values()):
            if not task.enabled:
                continue
            try:
                next_run = datetime.fromisoformat(task.next_run)
            except Exception as e:
                logger.error(f"[SchedulerManager] 任务 {task.name} next_run 解析失败: {task.next_run}, 错误: {e}")
                continue
            if now >= next_run:
                logger.debug(f"[SchedulerManager] 任务 {task.name} 时间到")
                await self._trigger_task(task)
    
    async def _trigger_task(self, task: TaskConfig):
        """触发单个任务"""
        logger.debug(f"[SchedulerManager] 触发任务: {task.name}")
        task.last_run = datetime.now().isoformat()
        task.run_count += 1
        
        config = task.config if isinstance(task.config, dict) else {}
        
        if task.type == "interval":
            minutes = config.get("minutes", 60)
            next_time = datetime.now() + timedelta(minutes=minutes)
            task.next_run = next_time.isoformat()
        elif task.type == "daily":
            hour = config.get("hour", 9)
            minute = config.get("minute", 0)
            next_time = self._calculate_next_daily(datetime.now(), hour, minute)
            task.next_run = next_time.isoformat()
        elif task.type == "weekly":
            weekdays = config.get("weekdays", [config.get("weekday", 0)])
            hour = config.get("hour", 9)
            minute = config.get("minute", 0)
            next_time = self._calculate_next_weekday(datetime.now(), weekdays, hour, minute)
            task.next_run = next_time.isoformat()
        else:
            logger.warning(f"[SchedulerManager] 未知任务类型: {task.type}，使用默认间隔 60 分钟")
            next_time = datetime.now() + timedelta(minutes=60)
            task.next_run = next_time.isoformat()
        
        self._save_tasks()
        
        scheduled_task = ScheduledTask(
            task_id=task.id,
            task_name=task.name,
            prompt=task.prompt,
            triggered_at=task.last_run,
            run_count=task.run_count,
        )
        self._pending_queue.append(scheduled_task)

    def _calculate_next_daily(self, now: datetime, hour: int, minute: int) -> datetime:
        """计算下一个每日执行时间"""
        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_time > now:
            return target_time
        else:
            return target_time + timedelta(days=1)

    def _calculate_next_weekday(self, now: datetime, weekdays: list, hour: int, minute: int) -> datetime:
        """计算下一个匹配的星期"""
        current_weekday = now.weekday()
        current_time = now.time()
        target_time = datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()
        
        sorted_weekdays = sorted(weekdays)
        
        for wd in sorted_weekdays:
            days_ahead = (wd - current_weekday) % 7
            if days_ahead == 0:
                if current_time < target_time:
                    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            elif days_ahead > 0:
                next_date = now + timedelta(days=days_ahead)
                return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        next_date = now + timedelta(days=(7 - current_weekday + sorted_weekdays[0]))
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    def add_task(self, task: TaskConfig) -> bool:
        """添加或更新任务"""
        self._tasks[task.id] = task
        self._save_tasks()
        return True
    
    def remove_task(self, task_id: str) -> bool:
        """删除任务"""
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save_tasks()
            return True
        return False
    
    def get_task(self, task_id: str) -> Optional[TaskConfig]:
        """获取任务"""
        return self._tasks.get(task_id)
    
    def get_all_tasks(self) -> Dict[str, TaskConfig]:
        """获取所有任务"""
        return dict(self._tasks)
    
    def update_task_enabled(self, task_id: str, enabled: bool) -> bool:
        """启用/禁用任务"""
        if task_id in self._tasks:
            self._tasks[task_id].enabled = enabled
            self._save_tasks()
            return True
        return False
    
    def get_next_task(self) -> Optional[ScheduledTask]:
        """获取下一个待处理任务"""
        if not self._pending_queue:
            return None
        
        task = self._pending_queue.pop(0)
        task.status = TaskStatus.PROCESSING.value
        task.started_at = datetime.now().isoformat()
        
        self._processing[task.task_id] = task
        
        return task
    
    def peek_next_task(self) -> Optional[ScheduledTask]:
        """查看下一个任务"""
        if self._pending_queue:
            return self._pending_queue[0]
        return None
    
    def has_pending_task(self) -> bool:
        """检查是否有待处理任务"""
        return len(self._pending_queue) > 0
    
    def requeue_task(self, task: ScheduledTask):
        """将任务重新放回队列头部"""
        if task.task_id in self._processing:
            del self._processing[task.task_id]
        task.status = TaskStatus.PENDING.value
        task.started_at = None
        self._pending_queue.insert(0, task)
        logger.info(f"[SchedulerManager] 任务 {task.task_name} 已重新加入队列，等待重试")

    def get_pending_tasks_for_session(self, session_id: str) -> List[ScheduledTask]:
        """获取指定会话的待处理任务"""
        tasks = []
        for task in list(self._pending_queue):
            config = self._tasks.get(task.task_id)
            if config and config.session_id == session_id:
                tasks.append(task)
        return tasks

    def claim_task_for_session(self, task_id: str, session_id: str) -> Optional[ScheduledTask]:
        """认领指定会话的任务"""
        for i, task in enumerate(self._pending_queue):
            if task.task_id == task_id:
                config = self._tasks.get(task.task_id)
                if config and config.session_id == session_id:
                    task = self._pending_queue.pop(i)
                    task.status = TaskStatus.PROCESSING.value
                    task.started_at = datetime.now().isoformat()
                    self._processing[task.task_id] = task
                    return task
        return None
    
    def mark_completed(self, task_id: str, result: Optional[Dict] = None):
        """标记任务完成"""
        if task_id in self._processing:
            task = self._processing.pop(task_id)
            task.status = TaskStatus.COMPLETED.value
            task.completed_at = datetime.now().isoformat()
            task.result = result
            
            self._add_to_history(task)
            logger.info(f"[SchedulerManager] 任务完成: {task.task_name}")
    
    def mark_failed(self, task_id: str, error: str):
        """标记任务失败"""
        if task_id in self._processing:
            task = self._processing.pop(task_id)
            task.status = TaskStatus.FAILED.value
            task.completed_at = datetime.now().isoformat()
            task.error = error
            
            self._add_to_history(task)
            logger.error(f"[SchedulerManager] 任务失败: {task.task_name}, 错误: {error}")
    
    def _add_to_history(self, task: ScheduledTask):
        """添加到历史记录"""
        self._history.append(task)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
    
    def get_queue_status(self) -> Dict[str, Any]:
        """获取队列状态"""
        return {
            "pending": len(self._pending_queue),
            "processing": len(self._processing),
            "history": len(self._history),
            "next_task": self.peek_next_task().task_name if self._pending_queue else None,
        }
    
    def get_pending_tasks(self) -> List[Dict]:
        """获取所有待处理任务"""
        return [asdict(t) for t in self._pending_queue]
    
    def get_processing_tasks(self) -> List[Dict]:
        """获取处理中任务"""
        return [asdict(t) for t in self._processing.values()]
    
    def get_history(self, limit: int = 10) -> List[Dict]:
        """获取历史记录"""
        return [asdict(t) for t in self._history[-limit:]]
    
    def _load_history(self):
        """加载历史记录"""
        if self._storage_path.exists():
            try:
                data = json.loads(self._storage_path.read_text(encoding="utf-8"))
                for t in data.get("history", []):
                    task = ScheduledTask(**t)
                    self._history.append(task)
            except Exception as e:
                logger.error(f"[SchedulerManager] 加载历史失败: {e}")
    
    def _save_history(self):
        """保存历史记录"""
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "history": [asdict(t) for t in self._history[-50:]]
            }
            self._storage_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"[SchedulerManager] 保存历史失败: {e}")


_manager: Optional[SchedulerManager] = None


def get_scheduler_manager() -> SchedulerManager:
    """获取全局 SchedulerManager 实例"""
    global _manager
    if _manager is None:
        _manager = SchedulerManager()
    return _manager
