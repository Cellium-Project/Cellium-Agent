# -*- coding: utf-8 -*-
"""
定时任务管理 API 路由
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import uuid

from app.core.scheduler.manager import get_scheduler_manager, TaskConfig

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class TaskCreateRequest(BaseModel):
    name: str = Field(..., description="任务名称")
    task_type: str = Field(..., description="任务类型: interval/daily/weekly")
    prompt: str = Field(..., description="任务提示词")
    config: Dict[str, Any] = Field(default_factory=dict, description="任务配置")
    enabled: bool = Field(default=True, description="是否启用")
    session_id: Optional[str] = Field(default=None, description="关联的会话ID")


class TaskUpdateRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class TaskResponse(BaseModel):
    id: str
    name: str
    type: str
    config: Dict[str, Any]
    prompt: str
    created_at: str
    next_run: str
    last_run: Optional[str]
    run_count: int
    enabled: bool
    session_id: Optional[str]


class TaskListResponse(BaseModel):
    items: List[TaskResponse]
    total: int


class TaskStatsResponse(BaseModel):
    total_tasks: int
    enabled_tasks: int
    pending_count: int
    processing_count: int
    history_count: int


def _task_to_response(task: TaskConfig) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        name=task.name,
        type=task.type,
        config=task.config,
        prompt=task.prompt,
        created_at=task.created_at,
        next_run=task.next_run,
        last_run=task.last_run,
        run_count=task.run_count,
        enabled=task.enabled,
        session_id=task.session_id,
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks():
    manager = get_scheduler_manager()
    tasks = manager.get_all_tasks()
    items = [_task_to_response(t) for t in tasks.values()]
    return TaskListResponse(items=items, total=len(items))


@router.get("/stats", response_model=TaskStatsResponse)
async def get_task_stats():
    manager = get_scheduler_manager()
    tasks = manager.get_all_tasks()
    status = manager.get_queue_status()
    
    total = len(tasks)
    enabled = sum(1 for t in tasks.values() if t.enabled)
    
    return TaskStatsResponse(
        total_tasks=total,
        enabled_tasks=enabled,
        pending_count=status["pending"],
        processing_count=status["processing"],
        history_count=status["history"],
    )


@router.post("", response_model=TaskResponse)
async def create_task(request: TaskCreateRequest):
    manager = get_scheduler_manager()
    
    valid_types = ["interval", "daily", "weekly"]
    if request.task_type not in valid_types:
        raise HTTPException(
            status_code=400, 
            detail=f"无效的任务类型: {request.task_type}，有效类型: {', '.join(valid_types)}"
        )
    
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    now = datetime.now()
    
    next_run = now
    if request.task_type == "interval":
        minutes = request.config.get("minutes", 60)
        next_run = now + timedelta(minutes=minutes)
    elif request.task_type == "daily":
        hour = request.config.get("hour", 9)
        minute = request.config.get("minute", 0)
        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_time > now:
            next_run = target_time
        else:
            next_run = target_time + timedelta(days=1)
    elif request.task_type == "weekly":
        weekdays = request.config.get("weekdays", [request.config.get("weekday", 0)])
        hour = request.config.get("hour", 9)
        minute = request.config.get("minute", 0)
        next_run = manager._calculate_next_weekday(now, weekdays, hour, minute)
    
    task = TaskConfig(
        id=task_id,
        name=request.name,
        type=request.task_type,
        config=request.config,
        prompt=request.prompt,
        created_at=now.isoformat(),
        next_run=next_run.isoformat(),
        enabled=request.enabled,
        session_id=request.session_id,
    )
    
    manager.add_task(task)
    return _task_to_response(task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    manager = get_scheduler_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_to_response(task)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, request: TaskUpdateRequest):
    manager = get_scheduler_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if request.name is not None:
        task.name = request.name
    if request.prompt is not None:
        task.prompt = request.prompt
    if request.config is not None:
        task.config = request.config
    if request.enabled is not None:
        task.enabled = request.enabled
    
    manager.add_task(task)
    return _task_to_response(task)


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    manager = get_scheduler_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    manager.remove_task(task_id)
    return {"success": True, "deleted_id": task_id}


@router.patch("/{task_id}/toggle")
async def toggle_task(task_id: str):
    manager = get_scheduler_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    new_enabled = not task.enabled
    manager.update_task_enabled(task_id, new_enabled)
    
    updated = manager.get_task(task_id)
    return _task_to_response(updated)


@router.get("/history/list")
async def get_history(limit: int = Query(default=20, ge=1, le=100)):
    manager = get_scheduler_manager()
    history = manager.get_history(limit=limit)
    return {"items": history}
