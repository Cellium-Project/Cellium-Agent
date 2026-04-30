# -*- coding: utf-8 -*-
"""
Scheduler - 定时任务调度组件

你可以使用以下命令创建和管理定时任务：

创建任务：
  scheduler.create_interval(name, minutes, prompt)     # 间隔执行，如每30分钟
  scheduler.create_daily(name, time, prompt)           # 每天执行，如每天9:00
  scheduler.create_weekly(name, weekday, time, prompt) # 每周执行，weekday: 0=周一, 6=周日

管理任务：
  scheduler.list()           # 查看所有任务
  scheduler.delete(task_id)  # 删除任务
  scheduler.enable(task_id)  # 启用任务
  scheduler.disable(task_id) # 禁用任务
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from app.core.interface.base_cell import BaseCell
from app.core.scheduler import get_scheduler_manager, TaskConfig

logger = logging.getLogger(__name__)


class Scheduler(BaseCell):
    """
    定时任务调度器 - 管理入口

    Agent可用命令：
      scheduler.create_interval(name, minutes, prompt)     # 间隔任务，minutes为分钟数
      scheduler.create_daily(name, time, prompt)           # 每日任务，time格式HH:MM
      scheduler.create_weekly(name, weekday, time, prompt) # 每周任务，weekday:0-6(0=周一)
      scheduler.list()                                     # 列出任务
      scheduler.delete(task_id)                            # 删除任务
      scheduler.enable(task_id)                            # 启用任务
      scheduler.disable(task_id)                           # 禁用任务
    """

    def __init__(self):
        super().__init__()
        self._manager = get_scheduler_manager()
        self._id_counter = 0

    @property
    def cell_name(self) -> str:
        return "scheduler"

    def _generate_task_id(self) -> str:
        """生成任务ID（递增计数器，避免删除后碰撞）"""
        self._id_counter += 1
        return f"t{self._id_counter:04d}"

    def _weekday_name(self, n: int) -> str:
        """星期数字转名称"""
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return names[n] if 0 <= n <= 6 else "未知"

    def _cmd_create_interval(self, name: str, minutes: int, prompt: str, session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """创建间隔任务: scheduler.create_interval(name, minutes, prompt)"""
        task_id = self._generate_task_id()
        next_run_dt = datetime.now() + timedelta(minutes=minutes)
        next_run = next_run_dt.isoformat()

        task = TaskConfig(
            id=task_id,
            name=name,
            type="interval",
            config={"minutes": minutes},
            prompt=prompt,
            created_at=datetime.now().isoformat(),
            next_run=next_run,
            enabled=True,
            session_id=session_id,
            platform_context=platform_context,
        )
        self._manager.add_task(task)

        return {
            "success": True,
            "task_id": task_id,
            "type": "interval",
            "next_run": next_run_dt.strftime("%Y-%m-%d %H:%M"),
            "message": f"已创建间隔任务: {name} (每{minutes}分钟)，下次执行: {next_run_dt.strftime('%Y-%m-%d %H:%M')}"
        }

    def _cmd_create_daily(self, name: str, time: str, prompt: str, session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """创建每日任务: scheduler.create_daily(name, time, prompt) - time格式HH:MM"""
        try:
            hour, minute = map(int, time.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return {"error": "时间格式错误，小时: 0-23, 分钟: 0-59"}
        except ValueError:
            return {"error": "时间格式错误，请使用 HH:MM 格式，如 09:00"}

        task_id = self._generate_task_id()

        now = datetime.now()
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        task = TaskConfig(
            id=task_id,
            name=name,
            type="daily",
            config={"hour": hour, "minute": minute},
            prompt=prompt,
            created_at=datetime.now().isoformat(),
            next_run=next_run.isoformat(),
            enabled=True,
            session_id=session_id,
            platform_context=platform_context,
        )
        self._manager.add_task(task)

        return {
            "success": True,
            "task_id": task_id,
            "type": "daily",
            "time": time,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M"),
            "message": f"已创建每日任务: {name} (每天 {time})，下次执行: {next_run.strftime('%Y-%m-%d %H:%M')}"
        }

    def _cmd_create_weekly(self, name: str, weekday, time: str, prompt: str, session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """创建每周任务: scheduler.create_weekly(name, weekday, time, prompt) - weekday:0-6或[0,1,2]数组(0=周一)"""
        if isinstance(weekday, list):
            weekdays = [int(w) for w in weekday]
            if not all(0 <= w <= 6 for w in weekdays):
                return {"error": "星期格式错误，请使用 0-6 (0=周一, 6=周日)"}
            if not weekdays:
                return {"error": "至少选择一个星期"}
        else:
            try:
                w = int(weekday)
                if not (0 <= w <= 6):
                    return {"error": "星期格式错误，请使用 0-6 (0=周一, 6=周日)"}
                weekdays = [w]
            except ValueError:
                return {"error": "星期必须是数字 0-6 或数组"}

        try:
            hour, minute = map(int, time.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return {"error": "时间格式错误，小时: 0-23, 分钟: 0-59"}
        except ValueError:
            return {"error": "时间格式错误，请使用 HH:MM 格式，如 09:00"}

        task_id = self._generate_task_id()

        now = datetime.now()
        next_run = self._calculate_next_weekday(now, weekdays, hour, minute)

        task = TaskConfig(
            id=task_id,
            name=name,
            type="weekly",
            config={"weekdays": sorted(weekdays), "hour": hour, "minute": minute},
            prompt=prompt,
            created_at=datetime.now().isoformat(),
            next_run=next_run.isoformat(),
            enabled=True,
            session_id=session_id,
            platform_context=platform_context,
        )
        self._manager.add_task(task)

        weekday_names = [self._weekday_name(w) for w in sorted(weekdays)]
        return {
            "success": True,
            "task_id": task_id,
            "type": "weekly",
            "weekdays": weekday_names,
            "time": time,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M"),
            "message": f"已创建每周任务: {name} (每{'、'.join(weekday_names)} {time})，下次执行: {next_run.strftime('%Y-%m-%d %H:%M')}"
        }

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
            else:
                next_date = now + timedelta(days=days_ahead)
                return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        next_date = now + timedelta(days=(7 - current_weekday + sorted_weekdays[0]))
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _cmd_list(self) -> Dict[str, Any]:
        """列出所有任务: scheduler.list()"""
        tasks = []
        for t in self._manager.get_all_tasks().values():
            tasks.append({
                "id": t.id,
                "name": t.name,
                "type": t.type,
                "enabled": t.enabled,
                "next_run": t.next_run,
                "last_run": t.last_run,
                "run_count": t.run_count
            })

        return {
            "tasks": tasks,
            "count": len(tasks)
        }

    def _cmd_delete(self, task_id: str) -> Dict[str, Any]:
        """删除任务: scheduler.delete(task_id)"""
        if self._manager.remove_task(task_id):
            return {"success": True, "message": f"已删除任务: {task_id}"}
        return {"error": f"任务不存在: {task_id}"}

    def _cmd_enable(self, task_id: str) -> Dict[str, Any]:
        """启用任务: scheduler.enable(task_id)"""
        if self._manager.update_task_enabled(task_id, True):
            return {"success": True, "message": f"已启用任务: {task_id}"}
        return {"error": f"任务不存在: {task_id}"}

    def _cmd_disable(self, task_id: str) -> Dict[str, Any]:
        """禁用任务: scheduler.disable(task_id)"""
        if self._manager.update_task_enabled(task_id, False):
            return {"success": True, "message": f"已禁用任务: {task_id}"}
        return {"error": f"任务不存在: {task_id}"}

    def _cmd_run_now(self, task_id: str) -> Dict[str, Any]:
        """立即执行任务: scheduler.run_now(task_id)"""
        task = self._manager.get_task(task_id)
        if not task:
            return {"error": f"任务不存在: {task_id}"}

        import asyncio
        asyncio.create_task(self._manager._trigger_task(task))

        return {"success": True, "message": f"已立即执行任务: {task.name}"}
