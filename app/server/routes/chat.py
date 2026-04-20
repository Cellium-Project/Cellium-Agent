# -*- coding: utf-8 -*-
"""
聊天 API 路由 — 会话感知 + EventBus 事件 + DI 容器
"""

import logging
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from typing import Optional, List
import json

from app.core.bus.event_bus import event_bus
from app.core.di.container import get_container
from app.agent.events.event_types import AgentEventType
from app.agent.events.event_models import (
    MessageReceivedEvent,
    AgentErrorEvent,
)
from app.agent.loop.session_manager import get_session_manager
from app.agent.loop.session_store import get_session_store
from app.server.task_manager import get_task_manager

router = APIRouter(prefix="/api", tags=["chat"])

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default"
    last_event_id: int = 0


class SupplementRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default"


class ChatResponse(BaseModel):
    content: str
    iterations: int
    success: bool
    session_id: str = "default"
    tool_traces: List[dict] = []


class SessionCreateRequest(BaseModel):
    session_id: Optional[str] = None


class RenameSessionRequest(BaseModel):
    title: str


class SaveMessageRequest(BaseModel):
    """保存消息请求（用于手动停止后持久化）"""
    user_message: str
    assistant_message: str
    tool_traces: List[dict] = []
    timeline: List[dict] = []


class StopRequest(BaseModel):
    """停止任务请求"""
    session_id: Optional[str] = "default"


class SessionResponse(BaseModel):
    sessions: List[dict]
    total: int


def _get_agent_loop():
    """从 DI 容器获取 AgentLoop"""
    from app.agent.loop.agent_loop import AgentLoop
    container = get_container()
    try:
        return container.resolve(AgentLoop)
    except ValueError:
        return None


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """聊话接口（会话感知）"""
    session_id = request.session_id or "default"
    agent_loop = _get_agent_loop()

    if agent_loop is None:
        return ChatResponse(
            content="Agent 未初始化",
            iterations=0,
            success=False,
            session_id=session_id,
        )

    session_mgr = get_session_manager()
    session_info = session_mgr.get_or_create(session_id)
    session_memory = session_info.memory

    try:
        event_bus.publish(
            AgentEventType.MESSAGE_RECEIVED,
            MessageReceivedEvent(
                event_type=AgentEventType.MESSAGE_RECEIVED,
                data={"message": request.message, "source": "api"},
                session_id=session_id,
                message=request.message,
            )
        )

        result = await agent_loop.run(
            request.message,
            memory=session_memory,
            session_id=session_id,
        )

        session_info.message_count += 1

        return ChatResponse(
            content=result.get("content", ""),
            iterations=result.get("iterations", 0),
            success=result.get("type") == "response",
            session_id=session_id,
            tool_traces=result.get("tool_traces", []),
        )
    except Exception as e:
        import traceback as tb_module
        event_bus.publish(
            AgentEventType.ERROR,
            AgentErrorEvent(
                event_type=AgentEventType.ERROR,
                data={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": tb_module.format_exc(),
                    "source": "api",
                },
                session_id=session_id,
                error_type=type(e).__name__,
                error_message=str(e),
                traceback=tb_module.format_exc(),
            ),
        )

        return ChatResponse(
            content=f"错误: {str(e)}",
            iterations=0,
            success=False,
            session_id=session_id,
        )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request):
    """
    启动聊天任务（WebSocket 推送事件）

    POST 只负责启动任务，事件通过 WebSocket 实时推送
    """
    session_id = request.session_id or "default"
    agent_loop = _get_agent_loop()
    task_mgr = get_task_manager()

    if agent_loop is None:
        return {"status": "error", "error": "Agent 未初始化", "session_id": session_id}

    session_mgr = get_session_manager()
    session_info = session_mgr.get_or_create(session_id)
    session_memory = session_info.memory

    if task_mgr.has_running_task(session_id):
        history = task_mgr.get_event_history(session_id, after_event_id=request.last_event_id)
        logger.info("[chat_stream] 重新连接到运行中的任务 | session=%s | replay_from=%d | replay_count=%d",
                   session_id, request.last_event_id, len(history))
        pending_input = task_mgr.get_pending_input(session_id)

        from app.server.routes.ws_event_manager import ws_publish_event
        if pending_input:
            ws_publish_event("chat_event", {"type": "message_received", "session_id": session_id, "message": pending_input}, session_id=session_id)
        for event in history:
            ws_publish_event("chat_event", event, session_id=session_id)

        return {"status": "reconnecting", "session_id": session_id, "history_count": len(history)}

    if not request.message or not request.message.strip():
        return {"status": "error", "error": "没有运行中的任务", "session_id": session_id}

    started = await task_mgr.start_task(
        session_id=session_id,
        agent_loop=agent_loop,
        user_input=request.message,
        memory=session_memory,
    )

    if not started:
        return {"status": "error", "error": "无法启动任务", "session_id": session_id}

    return {"status": "started", "session_id": session_id}


@router.get("/chat/status")
async def get_chat_status(session_id: str = Query("default", description="会话 ID")):
    """查询聊天任务状态"""
    task_mgr = get_task_manager()
    info = task_mgr.get_task_info(session_id)

    if info:
        return {
            "session_id": session_id,
            "has_running_task": task_mgr.has_running_task(session_id),
            "task_status": info.status.value,
            "iteration": info.iteration,
            "event_count": info.event_count,
            "started_at": info.started_at,
            "error_message": info.error_message,
            "last_event_type": info.last_event_type,
            "last_event_id": info.last_event_id,
        }

    return {
        "session_id": session_id,
        "has_running_task": False,
        "task_status": None,
        "last_event_id": task_mgr.get_latest_event_id(session_id),
    }


@router.post("/chat/supplement")
async def supplement_chat(request: SupplementRequest):
    """向运行中的任务追加补充消息"""
    session_id = request.session_id or "default"
    message = (request.message or "").strip()
    if not message:
        return {"status": "ignored", "reason": "empty_message", "session_id": session_id}

    task_mgr = get_task_manager()
    queued = task_mgr.enqueue_supplement_message(session_id, {
        "content": message,
        "source": "webui",
        "msg_id": None,
        "received_at": __import__('time').time(),
        "platform": None,
        "message_type": None,
    })
    if not queued:
        return {"status": "not_running", "session_id": session_id}
    return {"status": "queued", "session_id": session_id}


@router.post("/chat/stop")
async def stop_chat(request: StopRequest):
    """停止运行中的后台任务"""
    session_id = request.session_id or "default"
    task_mgr = get_task_manager()

    if task_mgr.cancel_task(session_id):
        return {"status": "stopped", "session_id": session_id}

    return {"status": "not_found", "session_id": session_id}


# ── 会话管理 API ───────────────────────────────────────────


@router.get("/sessions", response_model=SessionResponse)
async def list_sessions(active_only: bool = Query(True, description="只返回活跃会话")):
    """列出所有会话"""
    mgr = get_session_manager()
    sessions = mgr.list_sessions(active_only=active_only)
    return SessionResponse(sessions=sessions, total=len(sessions))


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """查询单个会话详情"""
    mgr = get_session_manager()
    info = mgr.get(session_id)
    if not info:
        return {"error": "Session not found", "session_id": session_id}
    return info.to_dict()


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str):
    """关闭指定会话"""
    mgr = get_session_manager()
    ok = mgr.close_session(session_id)
    if ok:
        return {"status": "closed", "session_id": session_id}
    return {"error": "Session not found", "session_id": session_id}


@router.delete("/sessions")
async def cleanup_sessions():
    """清理所有超时会话"""
    mgr = get_session_manager()
    cleaned = mgr.cleanup_expired()
    return {"status": "cleaned", "expired_count": cleaned}


@router.get("/health")
async def health():
    """健康检查"""
    from app.agent.loop.agent_loop import AgentLoop
    container = get_container()
    session_mgr = get_session_manager()

    di_status = {
        "event_bus": True,
        "agent_loop": container.has(AgentLoop),
        "shell": False,
        "memory": False,
        "security": False,
    }

    try:
        from app.agent.shell.cellium_shell import CelliumShell
        di_status["shell"] = container.has(CelliumShell)
    except ImportError:
        pass

    try:
        from app.agent.memory.three_layer import ThreeLayerMemory
        di_status["memory"] = container.has(ThreeLayerMemory)
    except ImportError:
        pass

    try:
        from app.agent.security.policy import SecurityPolicy
        di_status["security"] = container.has(SecurityPolicy)
    except ImportError:
        pass

    return {
        "status": "ok",
        "di": di_status,
        "sessions": {
            "total": session_mgr.total_sessions,
            "active": len(session_mgr.list_sessions(active_only=True)),
        },
    }


# ── 历史消息接口 ───────────────────────────────────────────


def _is_json_thinking(content: str) -> bool:
    """检测内容是否是纯 JSON thinking 格式"""
    if not content:
        return False
    content = content.strip()
    if content.startswith("{") and content.endswith("}"):
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "reasoning" in data and "action" in data:
                return True
        except:
            pass
    return False


def _messages_to_renderable(raw_messages: list) -> list:
    """将 MemoryManager 的内部消息格式转换为前端可渲染格式"""
    renderable = []
    i = 0
    while i < len(raw_messages):
        msg = raw_messages[i]
        role = msg.get("role", "")

        if role == "user":
            renderable.append({
                "role": "user",
                "content": msg.get("content") or "",
                "timeline": None,
            })
            i += 1

        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            content = msg.get("content")

            if tool_calls:
                tool_traces = []
                timeline = []
                tc_map = {}

                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {})
                    tc_map[tc_id] = {
                        "tool": fn.get("name", "unknown"),
                        "arguments": json.loads(fn.get("arguments", "{}")) if fn.get("arguments") else {},
                    }

                j = i + 1
                final_text = content
                while j < len(raw_messages):
                    sub = raw_messages[j]
                    if sub.get("role") == "tool":
                        tc_id = sub.get("tool_call_id", "")
                        if tc_id in tc_map:
                            try:
                                result = json.loads(sub.get("content", "{}"))
                            except Exception:
                                result = {"output": sub.get("content", "")}
                            tc_map[tc_id]["result"] = result
                        j += 1
                    elif sub.get("role") == "assistant" and sub.get("tool_calls"):
                        break
                    elif sub.get("role") == "assistant" and sub.get("content") and not sub.get("tool_calls"):
                        final_text = sub.get("content", "")
                        j += 1
                        break
                    elif sub.get("role") == "user":
                        break
                    else:
                        j += 1

                for tc_info in tc_map.values():
                    args = tc_info["arguments"]
                    result = tc_info.get("result")
                    duration_ms = 0
                    if result and isinstance(result, dict):
                        duration_ms = result.get("elapsed_ms", 0) or result.get("duration_ms", 0) or 0
                        try:
                            duration_ms = round(float(duration_ms)) if duration_ms else 0
                        except (TypeError, ValueError):
                            duration_ms = 0

                    trace = {
                        "tool": tc_info["tool"],
                        "arguments": args,
                        "result": result,
                        "duration_ms": duration_ms,
                    }

                    intent = (args.get("_intent") or "") if isinstance(args, dict) else ""
                    if intent and intent.strip():
                        trace["description"] = intent.strip()
                    elif trace["tool"] == "shell":
                        cmd = args.get("command", "")[:80] if isinstance(args, dict) else ""
                        trace["description"] = f"正在执行：{cmd}..." if cmd else "正在执行命令"
                    elif trace["tool"] == "file":
                        action = args.get("action", "") if isinstance(args, dict) else ""
                        trace["description"] = f"正在查看：{action}" if action else "正在操作文件"
                    else:
                        trace["description"] = f"正在调用 {trace['tool']}"
                    tool_traces.append(trace)
                    timeline.append({
                        "kind": "tool",
                        "tool": trace["tool"],
                        "arguments": trace["arguments"],
                        "result": trace["result"],
                        "duration_ms": trace["duration_ms"],
                        "description": trace.get("description"),
                        "status": "done",
                    })

                if final_text:
                    if _is_json_thinking(final_text):
                        try:
                            reasoning_data = json.loads(final_text)
                            reasoning = reasoning_data.get("reasoning", "")
                            if reasoning:
                                timeline.append({"kind": "thinking", "content": reasoning})
                        except:
                            pass
                    else:
                        timeline.append({"kind": "text", "content": final_text})

                renderable.append({
                    "role": "assistant",
                    "content": final_text or "",
                    "toolTraces": tool_traces,
                    "timeline": timeline,
                })
                i = j

            else:
                timeline = []
                if content:
                    if _is_json_thinking(content):
                        try:
                            reasoning_data = json.loads(content)
                            reasoning = reasoning_data.get("reasoning", "")
                            if reasoning:
                                timeline.append({"kind": "thinking", "content": reasoning})
                        except:
                            pass
                    else:
                        timeline.append({"kind": "text", "content": content})
                
                renderable.append({
                    "role": "assistant",
                    "content": content or "",
                    "toolTraces": [],
                    "timeline": timeline,
                })
                i += 1
        else:
            i += 1

    return renderable


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    limit: int = Query(150, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取会话的历史消息"""
    import logging as _logging
    _hist_log = _logging.getLogger(__name__)

    mgr = get_session_manager()
    raw_msgs = []

    try:
        if mgr.three_layer_memory and mgr.three_layer_memory.archive:
            records = mgr.three_layer_memory.archive.get_by_session(session_id, limit=500)
            if records:
                all_messages = []
                seen = set()

                for rec in reversed(records):
                    msgs = rec.get("messages")
                    if isinstance(msgs, list):
                        filtered = [m for m in msgs if not (m.get("role") == "user" and m.get("_is_compacted_notes"))]
                        for msg in reversed(filtered):
    
                            msg_key = json.dumps(msg, sort_keys=True, ensure_ascii=False)
                            if msg_key not in seen:
                                seen.add(msg_key)
                                all_messages.append(msg)
                
                raw_msgs = list(reversed(all_messages))
                
                _hist_log.info(
                    "[History] 从 archive 恢复 | session=%s | %d 条记录 | 去重后 %d 条消息",
                    session_id, len(records), len(raw_msgs),
                )
    except Exception as e:
        _hist_log.warning("[History] archive 读取失败，回退到内存: %s", e)

    if not raw_msgs:
        info = mgr.get_or_create(session_id)
        raw_msgs = info.memory.get_messages()
        _hist_log.info(
            "[History] 从内存读取 | session=%s | raw=%d 条",
            session_id, len(raw_msgs),
        )

    if not raw_msgs:
        return {"session_id": session_id, "messages": [], "count": 0, "total": 0, "has_more": False}

    total = len(raw_msgs)
    end_idx = total - offset
    start_idx = max(0, end_idx - limit)
    paged_raw = raw_msgs[start_idx:end_idx]

    _hist_log.info(
        "[History] session=%s | paged=%d 条 (slice[%d:%d]) | total=%d",
        session_id, len(paged_raw), start_idx, end_idx, total,
    )

    renderable = _messages_to_renderable(paged_raw) if paged_raw else []

    return {
        "session_id": session_id,
        "messages": renderable,
        "count": len(renderable),
        "total": total,
        "has_more": start_idx > 0,
    }


# ── 会话持久化 API ───────────────────────────────────────────


@router.get("/session/last")
async def get_last_session():
    """获取最后活跃的 session_id"""
    store = get_session_store()
    last_id = store.get_last_active_session()

    if last_id:
        return {"session_id": last_id, "exists": True}

    return {"session_id": None, "exists": False}


@router.post("/session/create")
async def create_new_session():
    """创建新会话"""
    store = get_session_store()
    meta = store.get_or_create_session()

    session_mgr = get_session_manager()
    session_mgr.get_or_create(meta.session_id)

    return {"session_id": meta.session_id, "created_at": meta.created_at}


@router.get("/session/list")
async def list_all_sessions():
    """列出所有会话"""
    store = get_session_store()
    sessions = store.list_sessions()

    return {"sessions": [s.to_dict() for s in sessions], "total": len(sessions)}


@router.delete("/session/{session_id}")
async def delete_session_permanent(session_id: str):
    """永久删除会话"""
    store = get_session_store()
    deleted = store.delete_session(session_id)

    session_mgr = get_session_manager()
    session_mgr.close_session(session_id)

    if deleted:
        return {"status": "deleted", "session_id": session_id}
    return {"status": "not_found", "session_id": session_id}


@router.patch("/session/{session_id}/title")
async def rename_session(session_id: str, req: RenameSessionRequest):
    """重命名会话标题"""
    store = get_session_store()
    if not store.session_exists(session_id):
        return {"error": "Session not found", "session_id": session_id}
    store.set_session_title(session_id, req.title)
    return {"status": "ok", "session_id": session_id, "title": req.title}


@router.post("/session/{session_id}/save-message")
async def save_session_message(session_id: str, req: SaveMessageRequest):
    """保存消息到会话"""
    session_mgr = get_session_manager()
    session_info = session_mgr.get_or_create(session_id)
    memory = session_info.memory

    if req.user_message:
        memory.add_user_message(req.user_message)

    tool_call_ids = {}
    if req.tool_traces:
        for trace in req.tool_traces:
            tool_name = trace.get("tool", "")
            arguments = trace.get("arguments", {})
            result = trace.get("result", {})
            duration_ms = trace.get("duration_ms", 0)

            if tool_name:
                tool_call_id = memory.add_tool_call(tool_name, arguments)
                tool_call_ids[tool_name] = tool_call_id
                if result:
                    if isinstance(result, dict):
                        result["elapsed_ms"] = duration_ms
                    memory.add_tool_result(tool_call_id, result)

    if req.assistant_message:
        memory.add_assistant_message(req.assistant_message)

    if session_mgr.three_layer_memory:
        try:
            session_mgr.three_layer_memory.persist_session(
                user_input=req.user_message,
                response=req.assistant_message,
                session_id=session_id,
                messages=memory.get_messages(),
            )

        except Exception as e:
            logger.error("[save_message] 持久化失败: %s", e)
            return {"status": "error", "error": str(e)}

    session_info.message_count += 1
    store = get_session_store()
    store.update_message_count(session_id, delta=1)

    return {"status": "ok", "session_id": session_id, "message_count": session_info.message_count}
