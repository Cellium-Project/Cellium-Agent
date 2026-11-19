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


def _file_mtime() -> float:
    try:
        return os.path.getmtime(_path())
    except OSError:
        return 0.0


def _refresh() -> None:
    """落盘文件变化时重载内存映射，保证多进程（TUI / cellium-web）读到同一份数据"""
    global _OVERRIDES, _STORE_MTIME
    mtime = _file_mtime()
    if mtime == _STORE_MTIME:
        return
    with _LOCK:
        if mtime == _STORE_MTIME:
            return
        _OVERRIDES = _load()
        _STORE_MTIME = mtime


def _save() -> None:
    global _STORE_MTIME
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
        _STORE_MTIME = _file_mtime()
    except Exception:
        logger.warning("[SessionLLM] 保存会话模型配置失败", exc_info=True)


_OVERRIDES = _load()
_STORE_MTIME = _file_mtime()


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
    _refresh()
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


def _broadcast(payload: Dict[str, Any], session_id: str = "") -> None:
    """模型变更统一广播：broker（TUI/内部订阅）+ WebSocket（WebUI）"""
    try:
        from app.messaging.message_broker import get_message_broker
        get_message_broker().publish(session_id or "_models", dict(payload))
    except Exception:
        logger.debug("[SessionLLM] broker 广播失败 | %s", payload.get("type"), exc_info=True)
    try:
        from app.server.routes.ws_event_manager import ws_publish_event
        ws_publish_event("chat_event", dict(payload), session_id=session_id or None)
    except Exception:
        logger.debug("[SessionLLM] ws 广播失败 | %s", payload.get("type"), exc_info=True)


def model_usage(name: str) -> Dict[str, Any]:
    """查询某模型被谁占用：全局默认 / 会话覆盖列表"""
    _refresh()
    target = (name or "").strip()
    is_global = bool(target) and target == global_model()
    sessions = []
    with _LOCK:
        for sid, model_name in _OVERRIDES.items():
            if model_name == target:
                sessions.append(sid)
    return {
        "name": target,
        "is_global": is_global,
        "sessions": sessions,
        "in_use": is_global or bool(sessions),
    }


def _close_engine_quietly(engine) -> None:
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
        logger.debug("[SessionLLM] 关闭旧引擎失败", exc_info=True)


def reload_engines() -> bool:
    """按当前 llm 配置重建全局引擎并推给所有未覆盖会话（同步，可在线程中调用）"""
    try:
        from app.core.di.container import get_container
        from app.agent.llm.engine import BaseLLMEngine, create_llm_engine
        from app.agent.loop.agent_loop_manager import AgentLoopManager
        from app.core.util.agent_config import get_config

        llm_config = get_config().get_section("llm") or {}
        if not llm_config:
            logger.warning("[SessionLLM] 引擎重建跳过：llm 配置为空")
            return False
        new_engine = create_llm_engine(llm_config)
        if new_engine is None:
            logger.warning("[SessionLLM] 引擎重建失败：create_llm_engine 返回 None")
            return False

        container = get_container()
        old_engine = container.resolve(BaseLLMEngine) if container.has(BaseLLMEngine) else None
        container.register(BaseLLMEngine, new_engine, singleton=True)
        updated = AgentLoopManager.get_instance().update_llm_engine(new_engine)
        if old_engine is not None and old_engine is not new_engine:
            _close_engine_quietly(old_engine)
        logger.info(
            "[SessionLLM] 引擎已重建 | model=%s | 推送会话=%d",
            getattr(new_engine, "model", ""), updated,
        )
        return True
    except Exception as e:
        logger.warning("[SessionLLM] 引擎重建失败 | error=%s", e, exc_info=True)
        return False


def set_global(model_name: str) -> Dict[str, Any]:
    """切换全局默认模型（TUI / WebUI / config 工具统一入口）"""
    name = (model_name or "").strip()
    if not name:
        return {"success": False, "error": "model_name 为空"}
    if name not in _ready_names():
        return {"success": False, "error": f"模型不存在或配置不完整（需 api_key / base_url / model）: {name}"}
    try:
        from app.core.util.agent_config import get_config
        cfg = get_config()
        llm = cfg.get_section("llm") or {}
        previous = llm.get("current_model") or ""
        if previous == name:
            return {
                "success": True,
                "model": name,
                "previous": previous,
                "changed": False,
            }
        cfg.set("llm.current_model", name, persist=True, notify=False)
        cfg.reload_section("llm")
    except Exception as e:
        logger.warning("[SessionLLM] 切换全局模型失败 | model=%s | error=%s", name, e)
        return {"success": False, "error": str(e)}
    reloaded = reload_engines()
    _broadcast({"type": "global_model_changed", "model": name, "previous": previous})
    logger.info("[SessionLLM] 全局默认模型已切换 | %s -> %s | 引擎重建=%s", previous, name, reloaded)
    return {
        "success": True,
        "model": name,
        "previous": previous,
        "changed": True,
        "engine_reloaded": reloaded,
    }


def delete_model(model_name: str) -> Dict[str, Any]:
    """删除模型配置：全局默认拒绝删除，会话覆盖自动释放"""
    name = (model_name or "").strip()
    if not name:
        return {"success": False, "reason": "empty_name", "error": "模型名称不能为空"}
    usage = model_usage(name)
    if usage["is_global"]:
        return {
            "success": False,
            "reason": "global_default",
            "error": f"「{name}」是全局默认模型，请先切换到其他模型再删除",
            "usage": usage,
        }
    try:
        from app.core.util.agent_config import get_config
        cfg = get_config()
        llm = cfg.get_section("llm") or {}
        models = llm.get("models") or []
        if not any(m.get("name") == name for m in models):
            return {"success": False, "reason": "not_found", "error": f"模型不存在: {name}"}
        new_models = [m for m in models if m.get("name") != name]
        cfg.set("llm.models", new_models, persist=True, notify=False)
        cfg.reload_section("llm")
    except Exception as e:
        logger.warning("[SessionLLM] 删除模型失败 | model=%s | error=%s", name, e)
        return {"success": False, "error": str(e)}

    released = list(usage["sessions"])
    if released:
        _refresh()
        with _LOCK:
            for sid in released:
                _OVERRIDES.pop(str(sid), None)
        _save()
        for sid in released:
            drop_engine(sid)
            try:
                apply_to_loop(sid)
            except Exception:
                logger.debug("[SessionLLM] 释放会话引擎失败 | session=%s", sid, exc_info=True)

    _broadcast({
        "type": "models_changed",
        "action": "deleted",
        "model": name,
        "released_sessions": released,
    })
    logger.info("[SessionLLM] 模型已删除 | model=%s | 释放会话=%s", name, released)
    return {
        "success": True,
        "deleted": name,
        "released_sessions": released,
        "message": f"已删除模型: {name}"
        + (f"（{len(released)} 个会话已重置为跟随全局）" if released else ""),
    }


def set_model(session_id: str, model_name: Optional[str]) -> Dict[str, Any]:
    """设置会话模型；传空表示跟随全局"""
    sid = str(session_id or "")
    name = (model_name or "").strip()
    _refresh()
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
    result = {
        "success": True,
        "session_id": sid,
        "model": name or global_model(),
        "override": bool(name),
        "global_model": global_model(),
    }
    _broadcast({"type": "session_model_changed", **result}, sid)
    return result


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
    _refresh()
    with _LOCK:
        _OVERRIDES.pop(str(session_id or ""), None)
    _save()
    if not _session_busy(str(session_id or "")):
        drop_engine(session_id)
