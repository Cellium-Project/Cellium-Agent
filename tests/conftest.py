# -*- coding: utf-8 -*-
"""测试隔离：会话元数据统一落临时目录，避免污染真实 memory/sessions.json"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.agent.loop.session_store import SessionStore


@pytest.fixture(autouse=True)
def _isolate_session_store():
    old = SessionStore._instance
    tmp = tempfile.mkdtemp(prefix="cellium_sessions_")
    SessionStore._instance = SessionStore(
        store_path=os.path.join(tmp, "sessions.json"),
        archive_dir=os.path.join(tmp, "archive"),
    )
    try:
        yield
    finally:
        SessionStore._instance = old
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_scheduler_store(monkeypatch):
    """调度器隔离：任务与历史写临时目录，避免污染 data/scheduler.json"""
    import app.core.util.runtime_paths as runtime_paths
    from app.core.scheduler.manager import SchedulerManager

    tmp = tempfile.mkdtemp(prefix="cellium_sched_")
    real_resolve = runtime_paths.resolve_dir_writable

    def _fake_resolve(*args, **kwargs):
        if args and args[0] == "data":
            return tmp
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(runtime_paths, "resolve_dir_writable", _fake_resolve)

    old_instance = SchedulerManager._instance
    old_initialized = SchedulerManager._initialized
    SchedulerManager._instance = None
    SchedulerManager._initialized = False
    try:
        yield
    finally:
        SchedulerManager._instance = old_instance
        SchedulerManager._initialized = old_initialized
        shutil.rmtree(tmp, ignore_errors=True)
