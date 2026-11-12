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
