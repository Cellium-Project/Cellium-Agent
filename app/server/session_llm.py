# -*- coding: utf-8 -*-
"""会话级 LLM 模型覆盖：不同会话可绑定不同 API 配置，互不共享连接池"""

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STORE_PATH = None

_OVERRIDES: Dict[str, str] = {}
_ENGINES: Dict[str, tuple] = {}
_LOCK = threading.RLock()


def _path() -> str:
    if STORE_PATH:
        return STORE_PATH
    from app.core.util.runtime_paths import resolve_dir_writable
    return os.path.join(resolve_dir_writable("workspace"), ".cache", "session_llm.json")


def _load() -> Dict[str, str]:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception:
        return {}


def _save() -> None:
    path = _path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with _LOCK:
            payload = dict(_OVERRIDES)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        logger.warning("[SessionLLM] 保存会话模型配置失败", exc_info=True)


_OVERRIDES = _load()


def _raw_models() -> list:
    try:
        from app.core.util.agent_config import get_config
        llm = get_config().get_section("llm") or {}
        return [m for m in (llm.get("models") or []) if isinstance(m, dict) and m.get("name")]
    except Exception:
        return []


def _entry_ready(entry: dict) -> bool:
    return bool((entry.get("api_key") or "").strip()) and bool(
        (entry.get("base_url") or "").strip()
    ) and bool((entry.get("model") or "").strip())


def list_models() -> list:
    """可用的模型列表（缺 api_key/base_url/model 的占位项不计入）"""
    return [
        {
            "name": m.get("name") or "",
            "model": m.get("model") or "",
            "base_url": m.get("base_url") or "",
        }
        for m in _raw_models()
        if _entry_ready(m)
    ]


def _ready_names() -> set:
    return {m["name"] for m in list_models()}


def global_model() -> str:
    try:
        from app.core.util.agent_config import get_config
        llm = get_config().get_section("llm") or {}
        current = llm.get("current_model") or ""
        models = llm.get("models") or []
        ready = _ready_names()
        if current and current in ready:
            return current
        if ready:
            for item in models:
                if isinstance(item, dict) and item.get("name") in ready:
                    return item["name"]
        if current:
            return current
        return (models[0].get("name") or "") if models else ""
    except Exception:
        return ""


def get_model(session_id: str) -> str:
    with _LOCK:
        name = _OVERRIDES.get(str(session_id or ""), "")
    if not name:
        return ""
    if name not in _ready_names():
        return ""
    return name


def has_override(session_id: str) -> bool:
    return bool(get_model(session_id))


def _session_busy(session_id: str) -> bool:
    try:
        from app.server.task_manager import get_task_manager
        return bool(get_task_manager().has_running_task(session_id))
    except Exception:
        return False


def set_model(session_id: str, model_name: Optional[str]) -> Dict[str, Any]:
    """设置会话模型；传空表示跟随全局"""
    sid = str(session_id or "")
    name = (model_name or "").strip()
    if name and name not in _ready_names():
        return {"success": False, "error": f"模型不存在或配置不完整（需 api_key / base_url / model）: {name}"}
    with _LOCK:
        if name:
            _OVERRIDES[sid] = name
        else:
            _OVERRIDES.pop(sid, None)
    _save()
    # 正在运行时不立刻关闭旧引擎，避免打断当前流
    if not _session_busy(sid):
        drop_engine(sid)
    return {
        "success": True,
        "session_id": sid,
        "model": name or global_model(),
        "override": bool(name),
    }


def resolve_engine(session_id: str):
    """会话有独立模型时返回它的引擎，否则 None（回落全局引擎）"""
    name = get_model(session_id)
    if not name:
        return None
    with _LOCK:
        cached = _ENGINES.get(session_id)
        if cached and cached[0] == name:
            return cached[1]
    engine = _build_engine(name)
    if engine is None:
        return None
    with _LOCK:
        _ENGINES[session_id] = (name, engine)
    return engine


def _build_engine(name: str):
    try:
        if not any(m.get("name") == name for m in _raw_models()):
            logger.warning("[SessionLLM] 模型已不存在，跳过建引擎 | model=%s", name)
            return None
        from app.agent.llm.engine import create_llm_engine
        from app.core.util.agent_config import get_config
        cfg = dict(get_config().get_section("llm") or {})
        cfg["current_model"] = name
        engine = create_llm_engine(cfg)
        logger.info("[SessionLLM] 会话独立引擎已创建 | model=%s", getattr(engine, "model", name))
        return engine
    except Exception as e:
        logger.warning("[SessionLLM] 创建会话独立引擎失败 | model=%s | error=%s", name, e)
        return None


def drop_engine(session_id: str) -> None:
    with _LOCK:
        cached = _ENGINES.pop(str(session_id or ""), None)
    if not cached:
        return
    engine = cached[1]
    close = getattr(engine, "close", None)
    if not close:
        return
    try:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(close())
        else:
            asyncio.run(close())
    except Exception:
        logger.debug("[SessionLLM] 关闭会话引擎失败", exc_info=True)


def apply_to_loop(session_id: str) -> None:
    """把当前会话的引擎推给已存在的 AgentLoop"""
    try:
        from app.agent.loop.agent_loop_manager import AgentLoopManager
        AgentLoopManager.get_instance().apply_session_engine(
            session_id, resolve_engine(session_id)
        )
    except Exception:
        logger.debug("[SessionLLM] 推送会话引擎失败", exc_info=True)


def clear_session(session_id: str) -> None:
    with _LOCK:
        _OVERRIDES.pop(str(session_id or ""), None)
    _save()
    if not _session_busy(str(session_id or "")):
        drop_engine(session_id)
