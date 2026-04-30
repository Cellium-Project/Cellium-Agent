# -*- coding: utf-8 -*-
"""
定时任务模块测试
"""

import pytest
import asyncio
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.core.scheduler.manager import SchedulerManager, TaskConfig, ScheduledTask, TaskStatus


class TestSchedulerManager:
    """SchedulerManager 测试"""

    def setup_method(self):
        """每个测试前重置单例"""
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        self._temp_dir = tempfile.mkdtemp()
        
        self.manager = SchedulerManager()
        self.manager._tasks_storage = Path(self._temp_dir) / "scheduler.json"
        self.manager._storage_path = Path(self._temp_dir) / "scheduler_history.json"

    def teardown_method(self):
        """每个测试后清理临时目录"""
        import shutil
        if hasattr(self, '_temp_dir') and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_singleton(self):
        """测试单例模式"""
        m1 = SchedulerManager()
        m2 = SchedulerManager()
        assert m1 is m2

    def test_add_task(self):
        """测试添加任务"""
        task = TaskConfig(
            id="t0001",
            name="测试任务",
            type="interval",
            config={"minutes": 30},
            prompt="测试提示",
            created_at=datetime.now().isoformat(),
            next_run=(datetime.now() + timedelta(minutes=30)).isoformat(),
            enabled=True,
        )
        
        result = self.manager.add_task(task)
        assert result is True
        assert "t0001" in self.manager.get_all_tasks()

    def test_remove_task(self):
        """测试删除任务"""
        task = TaskConfig(
            id="t0002",
            name="待删除任务",
            type="interval",
            config={"minutes": 10},
            prompt="删除测试",
            created_at=datetime.now().isoformat(),
            next_run=datetime.now().isoformat(),
            enabled=True,
        )
        self.manager.add_task(task)
        
        result = self.manager.remove_task("t0002")
        assert result is True
        assert "t0002" not in self.manager.get_all_tasks()

    def test_enable_disable_task(self):
        """测试启用/禁用任务"""
        task = TaskConfig(
            id="t0003",
            name="启用禁用测试",
            type="interval",
            config={"minutes": 5},
            prompt="测试",
            created_at=datetime.now().isoformat(),
            next_run=datetime.now().isoformat(),
            enabled=True,
        )
        self.manager.add_task(task)
        
        self.manager.update_task_enabled("t0003", False)
        assert self.manager.get_task("t0003").enabled is False
        
        self.manager.update_task_enabled("t0003", True)
        assert self.manager.get_task("t0003").enabled is True

    def test_get_next_task(self):
        """测试获取下一个待处理任务"""
        scheduled = ScheduledTask(
            task_id="t0004",
            task_name="待执行任务",
            prompt="执行测试",
            triggered_at=datetime.now().isoformat(),
            run_count=1,
        )
        self.manager._pending_queue.append(scheduled)
        
        result = self.manager.get_next_task()
        assert result is not None
        assert result.task_id == "t0004"
        assert result.status == TaskStatus.PROCESSING.value

    def test_has_pending_task(self):
        """测试是否有待处理任务"""
        assert self.manager.has_pending_task() is False
        
        scheduled = ScheduledTask(
            task_id="t0005",
            task_name="测试",
            prompt="测试",
            triggered_at=datetime.now().isoformat(),
            run_count=1,
        )
        self.manager._pending_queue.append(scheduled)
        assert self.manager.has_pending_task() is True

    def test_mark_completed(self):
        """测试标记任务完成"""
        scheduled = ScheduledTask(
            task_id="t0006",
            task_name="完成测试",
            prompt="测试",
            triggered_at=datetime.now().isoformat(),
            run_count=1,
        )
        self.manager._processing["t0006"] = scheduled
        
        self.manager.mark_completed("t0006", {"result": "success"})
        assert "t0006" not in self.manager._processing
        assert len(self.manager._history) == 1

    def test_mark_failed(self):
        """测试标记任务失败"""
        scheduled = ScheduledTask(
            task_id="t0007",
            task_name="失败测试",
            prompt="测试",
            triggered_at=datetime.now().isoformat(),
            run_count=1,
        )
        self.manager._processing["t0007"] = scheduled
        
        self.manager.mark_failed("t0007", "测试错误")
        assert "t0007" not in self.manager._processing
        assert self.manager._history[0].error == "测试错误"

    def test_requeue_task(self):
        """测试重新排队任务"""
        scheduled = ScheduledTask(
            task_id="t0008",
            task_name="重排队测试",
            prompt="测试",
            triggered_at=datetime.now().isoformat(),
            run_count=1,
            status=TaskStatus.PROCESSING.value,
        )
        self.manager._processing["t0008"] = scheduled
        
        self.manager.requeue_task(scheduled)
        assert "t0008" not in self.manager._processing
        assert len(self.manager._pending_queue) == 1
        assert self.manager._pending_queue[0].status == TaskStatus.PENDING.value

    def test_get_queue_status(self):
        """测试获取队列状态"""
        status = self.manager.get_queue_status()
        assert status["pending"] == 0
        assert status["processing"] == 0
        assert status["history"] == 0


class TestTaskConfig:
    """TaskConfig 测试"""

    def test_create_interval_task(self):
        """测试创建间隔任务配置"""
        task = TaskConfig(
            id="t001",
            name="间隔任务",
            type="interval",
            config={"minutes": 30},
            prompt="每30分钟执行",
            created_at=datetime.now().isoformat(),
            next_run=datetime.now().isoformat(),
        )
        
        assert task.type == "interval"
        assert task.config["minutes"] == 30
        assert task.enabled is True

    def test_create_daily_task(self):
        """测试创建每日任务配置"""
        task = TaskConfig(
            id="t002",
            name="每日任务",
            type="daily",
            config={"hour": 9, "minute": 0},
            prompt="每天9点执行",
            created_at=datetime.now().isoformat(),
            next_run=datetime.now().isoformat(),
        )
        
        assert task.type == "daily"
        assert task.config["hour"] == 9

    def test_create_weekly_task(self):
        """测试创建每周任务配置"""
        task = TaskConfig(
            id="t003",
            name="每周任务",
            type="weekly",
            config={"weekday": 0, "hour": 10, "minute": 30},
            prompt="每周一10:30执行",
            created_at=datetime.now().isoformat(),
            next_run=datetime.now().isoformat(),
        )
        
        assert task.type == "weekly"
        assert task.config["weekday"] == 0


class TestScheduledTask:
    """ScheduledTask 测试"""

    def test_create_scheduled_task(self):
        """测试创建调度任务"""
        task = ScheduledTask(
            task_id="s001",
            task_name="调度任务",
            prompt="执行内容",
            triggered_at=datetime.now().isoformat(),
            run_count=1,
        )
        
        assert task.status == "pending"
        assert task.created_at is not None

    def test_task_status_flow(self):
        """测试任务状态流转"""
        task = ScheduledTask(
            task_id="s002",
            task_name="状态测试",
            prompt="测试",
            triggered_at=datetime.now().isoformat(),
            run_count=1,
        )
        
        assert task.status == "pending"
        
        task.status = TaskStatus.PROCESSING.value
        assert task.status == "processing"
        
        task.status = TaskStatus.COMPLETED.value
        assert task.status == "completed"


class TestSchedulerComponent:
    """Scheduler 组件测试"""

    def test_cell_name(self):
        """测试组件名称"""
        from components.scheduler import Scheduler
        
        scheduler = Scheduler()
        assert scheduler.cell_name == "scheduler"

    def test_get_commands(self):
        """测试获取命令列表"""
        from components.scheduler import Scheduler
        
        scheduler = Scheduler()
        commands = scheduler.get_commands()
        
        assert "create_interval" in commands
        assert "create_daily" in commands
        assert "create_weekly" in commands
        assert "list" in commands
        assert "delete" in commands
        assert "enable" in commands
        assert "disable" in commands

    def test_create_interval_command(self):
        """测试创建间隔任务命令"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        result = scheduler.execute("create_interval", name="测试任务", minutes=30, prompt="测试内容")
        
        assert result.get("success") is True
        assert "task_id" in result
        assert result["type"] == "interval"

    def test_create_daily_command(self):
        """测试创建每日任务命令"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        result = scheduler.execute("create_daily", name="每日任务", time="09:00", prompt="早安")
        
        assert result.get("success") is True
        assert result["type"] == "daily"

    def test_create_weekly_command(self):
        """测试创建每周任务命令"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        result = scheduler.execute("create_weekly", name="周报", weekday=4, time="18:00", prompt="生成周报")
        
        assert result.get("success") is True
        assert result["type"] == "weekly"

    def test_create_weekly_invalid_weekday(self):
        """测试创建每周任务 - 无效星期"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        result = scheduler.execute("create_weekly", name="测试", weekday=7, time="10:00", prompt="测试")
        
        assert "error" in result

    def test_create_daily_invalid_time(self):
        """测试创建每日任务 - 无效时间"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        result = scheduler.execute("create_daily", name="测试", time="25:00", prompt="测试")
        
        assert "error" in result

    def test_list_command(self):
        """测试列出任务命令"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        scheduler.execute("create_interval", name="任务1", minutes=10, prompt="测试")
        scheduler.execute("create_interval", name="任务2", minutes=20, prompt="测试")
        
        result = scheduler.execute("list")
        
        assert result["count"] == 2
        assert len(result["tasks"]) == 2

    def test_delete_command(self):
        """测试删除任务命令"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        create_result = scheduler.execute("create_interval", name="待删除", minutes=5, prompt="测试")
        task_id = create_result["task_id"]
        
        result = scheduler.execute("delete", task_id=task_id)
        assert result.get("success") is True
        
        result = scheduler.execute("delete", task_id="nonexistent")
        assert "error" in result

    def test_enable_disable_command(self):
        """测试启用/禁用任务命令"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        create_result = scheduler.execute("create_interval", name="测试", minutes=5, prompt="测试")
        task_id = create_result["task_id"]
        
        result = scheduler.execute("disable", task_id=task_id)
        assert result.get("success") is True
        
        result = scheduler.execute("enable", task_id=task_id)
        assert result.get("success") is True

    def test_session_id_passed_to_task(self):
        """测试 session_id 正确传递到任务配置"""
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        result = scheduler.execute("create_interval", name="会话任务", minutes=10, prompt="测试", session_id="test_session_123")
        
        assert result.get("success") is True
        task_id = result["task_id"]
        
        task = scheduler._manager.get_task(task_id)
        assert task.session_id == "test_session_123"


class TestCellToolAdapterContext:
    """CellToolAdapter 上下文传递测试"""

    def test_execute_with_context_passes_session_id(self):
        """测试 execute_with_context 正确传递 session_id"""
        from app.core.util.cell_tool_adapter import CellToolAdapter
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        adapter = CellToolAdapter(scheduler)
        
        result = adapter.execute_with_context(
            {"command": "create_interval", "name": "上下文任务", "minutes": 15, "prompt": "测试"},
            session_id="adapter_session_456"
        )
        
        assert result.get("success") is True
        task_id = result["task_id"]
        
        task = scheduler._manager.get_task(task_id)
        assert task.session_id == "adapter_session_456"

    def test_execute_with_context_passes_platform_context(self):
        """测试 execute_with_context 正确传递 platform_context"""
        from app.core.util.cell_tool_adapter import CellToolAdapter
        from components.scheduler import Scheduler
        
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        scheduler = Scheduler()
        adapter = CellToolAdapter(scheduler)
        
        platform_ctx = {
            "platform": "telegram",
            "user_id": "12345",
            "group_id": "67890",
            "target_id": "67890",
        }
        
        result = adapter.execute_with_context(
            {"command": "create_daily", "name": "平台任务", "time": "09:00", "prompt": "测试"},
            session_id="telegram:12345",
            platform_context=platform_ctx
        )
        
        assert result.get("success") is True
        task_id = result["task_id"]
        
        task = scheduler._manager.get_task(task_id)
        assert task.session_id == "telegram:12345"
        assert task.platform_context == platform_ctx


class TestNextRunCalculation:
    """下次执行时间计算测试"""

    def setup_method(self):
        """每个测试前重置单例"""
        SchedulerManager._instance = None
        SchedulerManager._initialized = False
        
        self._temp_dir = tempfile.mkdtemp()
        self.manager = SchedulerManager()
        self.manager._tasks_storage = Path(self._temp_dir) / "scheduler.json"
        self.manager._storage_path = Path(self._temp_dir) / "scheduler_history.json"

    def teardown_method(self):
        """每个测试后清理临时目录"""
        import shutil
        if hasattr(self, '_temp_dir') and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_calculate_next_daily_same_day(self):
        """测试每日任务：当天时间未到，应返回今天"""
        now = datetime(2024, 1, 15, 8, 0, 0)
        result = self.manager._calculate_next_daily(now, 9, 30)
        assert result.hour == 9
        assert result.minute == 30
        assert result.day == 15

    def test_calculate_next_daily_next_day(self):
        """测试每日任务：当天时间已过，应返回明天"""
        now = datetime(2024, 1, 15, 10, 0, 0)
        result = self.manager._calculate_next_daily(now, 9, 30)
        assert result.hour == 9
        assert result.minute == 30
        assert result.day == 16

    def test_calculate_next_daily_exact_time(self):
        """测试每日任务：正好是目标时间，应返回明天"""
        now = datetime(2024, 1, 15, 9, 30, 0)
        result = self.manager._calculate_next_daily(now, 9, 30)
        assert result.hour == 9
        assert result.minute == 30
        assert result.day == 16

    def test_calculate_next_weekday_same_week(self):
        """测试每周任务：本周还有目标星期"""
        now = datetime(2024, 1, 15, 8, 0, 0)
        result = self.manager._calculate_next_weekday(now, [0, 2, 4], 9, 30)
        assert result.weekday() in [0, 2, 4]
        assert result.hour == 9
        assert result.minute == 30

    def test_calculate_next_weekday_next_week(self):
        """测试每周任务：本周已过所有目标星期，应返回下周"""
        now = datetime(2024, 1, 19, 10, 0, 0)
        result = self.manager._calculate_next_weekday(now, [0], 9, 30)
        assert result.weekday() == 0
        assert result.hour == 9
        assert result.minute == 30
        assert result.day > 19

    def test_calculate_next_weekday_multiple_days(self):
        """测试每周任务：多个目标星期"""
        now = datetime(2024, 1, 15, 10, 0, 0)
        result = self.manager._calculate_next_weekday(now, [0, 2, 4], 9, 30)
        assert result.weekday() == 2
        assert result.hour == 9
        assert result.minute == 30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
