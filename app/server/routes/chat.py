# -*- coding: utf-8 -*-
"""
聊天 API 路由 — 会话感知 + EventBus 事件 + DI 容器
"""

import asyncio
import logging
import os
import re
import threading
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
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
from app.agent.loop.rewind import RewindError, preview_rewind, rewind_session
from app.server.task_manager import get_task_manager

router = APIRouter(prefix="/api", tags=["chat"])

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default"
    last_event_id: int = 0
    attachments: Optional[List[dict]] = []
    mode: Optional[str] = None


class ModeRequest(BaseModel):
    mode: str


class SupplementRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default"
    attachments: Optional[List[dict]] = []


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


class RewindRequest(BaseModel):
    session_id: str = "default"
    message_id: str


class ComponentEventRequest(BaseModel):
    """组件事件推送请求"""
    session_id: str
    message: str
    source: Optional[str] = "component"
    event_type: Optional[str] = "notification"
    event_data: Optional[Dict[str, Any]] = None
    platform_context: Optional[Dict[str, Any]] = None


class SessionResponse(BaseModel):
    sessions: List[dict]
    total: int


async def _get_session_loop(session_id: str):
    from app.agent.loop import AgentLoopManager
    mgr = AgentLoopManager.get_instance()
    if not mgr.is_initialized:
        return None
    return await mgr.get_loop(session_id)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """聊话接口（会话感知）"""
    session_id = request.session_id or "default"
    agent_loop = await _get_session_loop(session_id)

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

        from app.core.util.error_text import describe_error
        return ChatResponse(
            content=f"错误: {describe_error(e)}",
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
    agent_loop = await _get_session_loop(session_id)
    task_mgr = get_task_manager()

    if agent_loop is None:
        return {"status": "error", "error": "Agent 未初始化", "session_id": session_id}

    session_mgr = get_session_manager()
    session_info = session_mgr.get_or_create(session_id)
    session_memory = session_info.memory

    if task_mgr.has_running_task(session_id):
        from app.server.routes.session_events import collect_replay_events

        history = collect_replay_events(session_id, request.last_event_id)
        logger.info("[chat_stream] 重新连接到运行中的任务 | session=%s | replay_from=%d | replay_count=%d",
                   session_id, request.last_event_id, len(history))
        pending_input = task_mgr.get_pending_input(session_id)

        from app.server.routes.ws_event_manager import ws_publish_event
        if pending_input and not any(e.get("type") == "message_received" for e in history):
            ws_publish_event("chat_event", {"type": "message_received", "session_id": session_id, "message": pending_input}, session_id=session_id)
        for event in history:
            ws_publish_event("chat_event", event, session_id=session_id)

        if request.message and request.message.strip():
            if task_mgr.enqueue_supplement_message(session_id, {
                "content": request.message.strip(),
                "source": "webui",
                "msg_id": None,
                "received_at": __import__('time').time(),
                "platform": None,
                "message_type": None,
                "attachments": request.attachments or [],
            }):
                return {"status": "queued", "session_id": session_id}

        return {"status": "reconnecting", "session_id": session_id, "history_count": len(history)}
        
    if not request.message or not request.message.strip():
        if not request.attachments or len(request.attachments) == 0:
            return {"status": "error", "error": "消息内容不能为空", "session_id": session_id}

    user_input = request.message or ""
    if request.attachments:
        attachment_info = "\n\n[附件信息]\n"
        for att in request.attachments:
            attachment_info += f"- 文件: {att.get('filename', 'unknown')} (类型: {att.get('file_type', 'unknown')}, 大小: {att.get('file_size', 0)} bytes)\n"
            local_path = att.get('local_path', '')
            if local_path:
                attachment_info += f"  本地路径: {local_path}\n"
            url = att.get('url', '')
            if url:
                attachment_info += f"  URL: {url}\n"
        user_input = user_input + attachment_info

    from app.server.session_mode import get_mode, set_mode
    mode = set_mode(session_id, request.mode) if request.mode else get_mode(session_id)
    started = await task_mgr.start_task(
        session_id=session_id,
        agent_loop=agent_loop,
        user_input=user_input,
        memory=session_memory,
        mode=mode,
        attachments=request.attachments,
    )

    if not started:
        re_queued = task_mgr.enqueue_supplement_message(session_id, {
            "content": user_input,
            "source": "webui",
            "msg_id": None,
            "received_at": __import__('time').time(),
            "platform": None,
            "message_type": None,
            "attachments": request.attachments or [],
        })
        if re_queued:
            return {"status": "queued", "session_id": session_id}
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
        "attachments": request.attachments or [],
    })
    if not queued:
        agent_loop = await _get_session_loop(session_id)
        if agent_loop is None:
            return {"status": "error", "error": "Agent 未初始化", "session_id": session_id}
        session_info = get_session_manager().get_or_create(session_id)
        from app.server.session_mode import get_mode
        started = await task_mgr.start_task(
            session_id=session_id,
            agent_loop=agent_loop,
            user_input=message,
            memory=session_info.memory,
            mode=get_mode(session_id),
            attachments=request.attachments,
        )
        if not started:
            re_queued = task_mgr.enqueue_supplement_message(session_id, {
                "content": message,
                "source": "webui",
                "msg_id": None,
                "received_at": __import__('time').time(),
                "platform": None,
                "message_type": None,
                "attachments": request.attachments or [],
            })
            if re_queued:
                return {"status": "queued", "session_id": session_id, "mode": "queued_running"}
            return {"status": "error", "error": "无法启动任务", "session_id": session_id}
        logger.info(f"[supplement_chat] 任务已结束，兜底启动新任务 | session={session_id}")
        return {"status": "started", "session_id": session_id, "mode": "new_task"}
    return {"status": "queued", "session_id": session_id}


@router.post("/chat/stop")
async def stop_chat(request: StopRequest):
    """停止运行中的后台任务"""
    session_id = request.session_id or "default"
    task_mgr = get_task_manager()

    if task_mgr.cancel_task(session_id):
        return {"status": "stopped", "session_id": session_id}

    return {"status": "not_found", "session_id": session_id}


@router.post("/chat/rewind")
async def rewind_chat(request: RewindRequest):
    """撤回指定用户消息及其之后的所有消息"""
    session_id = request.session_id or "default"
    task_mgr = get_task_manager()
    if task_mgr.has_running_task(session_id):
        task_mgr.cancel_task(session_id)
        for _ in range(30):
            if not task_mgr.has_running_task(session_id):
                break
            await asyncio.sleep(0.1)

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: rewind_session(session_id, request.message_id)
        )
    except RewindError as e:
        if e.code == "not_found":
            status_code = 404
        elif e.code == "compacted":
            status_code = 409
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=str(e))


@router.post("/chat/rewind/preview")
async def preview_rewind_chat(request: RewindRequest):
    """预览撤回将回滚的文件及行数差异"""
    try:
        # 预览算 diff 属于同步 IO/CPU，放线程池执行，避免卡住 WebSocket 推送
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: preview_rewind(request.session_id or "default", request.message_id),
        )
    except RewindError as e:
        if e.code == "not_found":
            status_code = 404
        elif e.code == "compacted":
            status_code = 409
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=str(e))


@router.get("/chat/subagents")
async def get_subagents_status(session_id: str = Query("default")):
    """返回当前会话的子 Agent 运行状态（用于前端兜底收尾）"""
    try:
        from components import sub_agent as sa
        prefix = sa._sub_scope(session_id)
        items = []
        for key, info in sa._SUBAGENTS.items():
            if not key.startswith(prefix):
                continue
            items.append({
                "name": info.get("name", key[len(prefix):]),
                "status": info.get("status"),
                "tool_calls": info.get("tool_calls", 0),
                "current_tool": info.get("current_tool"),
                "last_tool": info.get("last_tool"),
                "tools_used": info.get("tools_used", []),
                "error": info.get("error"),
                "elapsed_s": sa._elapsed_seconds(info),
            })
        return {"session_id": session_id, "subagents": items}
    except Exception as e:
        return {"session_id": session_id, "subagents": [], "error": str(e)}


@router.get("/chat/delegations")
async def get_delegations_status(session_id: str = Query("default")):
    """当前会话发起的会话委托状态（前端状态条兜底）"""
    try:
        from components import session_agent as sa
        prefix = f"{session_id}::"
        items = []
        for key, info in sa._DELEGATIONS.items():
            if not key.startswith(prefix):
                continue
            status = info.get("status")
            if status in ("done", "error", "timeout", "cancelled", "unknown"):
                continue
            items.append({
                "task_id": info.get("task_id"),
                "target": info.get("target"),
                "target_title": info.get("target_title"),
                "purpose": info.get("purpose", ""),
                "status": status,
                "current_tool": info.get("current_tool"),
                "last_tool": info.get("last_tool"),
                "last_reasoning": info.get("last_reasoning"),
                "tool_calls": info.get("tool_calls", 0),
                "elapsed_s": sa._elapsed_seconds(info),
                "error": info.get("error"),
            })
        return {"session_id": session_id, "delegations": items}
    except Exception as e:
        return {"session_id": session_id, "delegations": [], "error": str(e)}


@router.post("/component/event")
async def component_event(request: ComponentEventRequest):
    """
    组件事件推送 — 后台组件主动触发 Agent
    
    组件后台运行时检测到事件，通过此 API 推送消息到指定 session 的 Agent 对话。
    复用定时任务的入口逻辑，自动路由到正确的通道（WebSocket 或外部平台）。
    
    使用场景:
      - 实时监控检测到变化时通知 Agent
      - 后台任务完成时通知 Agent
      - 外部事件触发 Agent 执行
    """
    from app.agent.loop.agent_loop_manager import AgentLoopManager
    from app.channels import ChannelManager
    import time
    
    session_id = request.session_id
    message = request.message.strip()
    
    if not message:
        return {"status": "ignored", "reason": "empty_message", "session_id": session_id}
    
    task_mgr = get_task_manager()
    session_mgr = get_session_manager()
    session_info = session_mgr.get_or_create(session_id)
    
    # 构建组件事件上下文
    component_context = f"""[组件事件触发]
来源: {request.source}
事件类型: {request.event_type}

事件内容:
{message}

---
请处理上述组件事件。"""
    
    # 检查是否有运行中的任务
    if task_mgr.has_running_task(session_id):
        queued = task_mgr.enqueue_supplement_message(session_id, {
            "content": message,
            "source": request.source,
            "msg_id": f"component_{int(time.time())}",
            "received_at": time.time(),
            "platform": "component",
            "message_type": "event",
        })
        if queued:
            logger.info(f"[component_event] 追加到运行中任务 | session={session_id} | source={request.source}")
            return {"status": "queued", "session_id": session_id, "mode": "supplement"}
        else:
            return {"status": "not_running", "session_id": session_id}
    
    # 获取 AgentLoop
    agent_loop = await _get_session_loop(session_id)
    if agent_loop is None:
        return {"status": "error", "error": "Agent 未初始化", "session_id": session_id}
    
    # 提取 platform_context（优先使用请求中的，否则从 session 恢复或解析）
    platform_context = None

    if request.platform_context:
        platform_context = request.platform_context
        session_info.platform_context = platform_context
        logger.debug(f"[component_event] 使用请求中的 platform_context | session={session_id} | tool_allowed={platform_context.get('tool_allowed')}")
    elif hasattr(session_info, "platform_context") and session_info.platform_context:
        platform_context = session_info.platform_context
        logger.debug(f"[component_event] 从 session 恢复 platform_context | session={session_id}")
    elif ":" in session_id:
        parts = session_id.split(":", 2)
        if len(parts) >= 2:
            platform = parts[0]
            user_id = parts[1]
            platform_context = {
                "platform": platform,
                "user_id": user_id,
                "group_id": parts[2] if len(parts) > 2 else None,
                "message_type": "group" if len(parts) > 2 else "c2c",
            }
            session_info.platform_context = {k: v for k, v in platform_context.items()
                                           if k not in ("tool_allowed", "trigger_component")}
            logger.debug(f"[component_event] 从 session_id 构建 platform_context | session={session_id}")
    
    # 根据是否有 platform_context 选择执行路径
    if platform_context:
        # 组件声明的跨组件黑名单（TRIGGER_TOOL_POLICY）自动并入
        from app.core.interface.tool_policy import apply_component_tool_policy
        platform_context = apply_component_tool_policy(platform_context, request.source)
        session_info.platform_context = platform_context
        # 外部平台通道（Telegram/QQ等）
        try:
            channel_mgr = ChannelManager.get_instance()
            
            started = await task_mgr.start_task(
                session_id=session_id,
                agent_loop=agent_loop,
                user_input=component_context,
                memory=session_info.memory,
            )
            
            if not started:
                return {"status": "error", "error": "无法启动任务", "session_id": session_id}
            
            queue = task_mgr.get_queue(session_id)
            if queue is None:
                return {"status": "error", "error": "任务队列不可用", "session_id": session_id}
            
            task_info = {
                "source": request.source,
                "event_type": request.event_type,
            }
            channel_mgr.start_scheduler_task_queue_consumer(platform_context, session_id, queue, task_info, trigger_type="component")
            
            logger.info(f"[component_event] 通过外部平台启动 | session={session_id} | platform={platform_context.get('platform')}")
            return {"status": "started", "session_id": session_id, "mode": "platform_channel", "platform": platform_context.get("platform")}
            
        except Exception as e:
            logger.error(f"[component_event] 外部平台执行失败 | session={session_id} | error={e}")
            return {"status": "error", "error": str(e), "session_id": session_id}
    else:
        # WebSocket 通道（WebUI）
        try:
            result = await agent_loop.run_scheduler_task({
                "task_id": f"component_{request.source}",
                "task_name": f"组件事件: {request.source}",
                "prompt": message,
                "triggered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "run_count": 1,
            }, session_id=session_id)
            
            logger.info(f"[component_event] 通过 WebSocket 完成 | session={session_id} | source={request.source}")
            return {"status": "completed", "session_id": session_id, "mode": "websocket", "result": result}
        except Exception as e:
            logger.error(f"[component_event] WebSocket 执行失败 | session={session_id} | error={e}")
            return {"status": "error", "error": str(e), "session_id": session_id}


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
    from app.agent.loop import AgentLoopManager
    container = get_container()
    session_mgr = get_session_manager()

    loop_mgr = AgentLoopManager.get_instance()
    di_status = {
        "event_bus": True,
        "agent_loop": loop_mgr.is_initialized,
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


def _extract_json_thinking(content: str) -> dict:

    if not content:
        return {}
    
    import re
    json_block_pattern = re.compile(r'```json\s*([\s\S]*?)\s*```', re.IGNORECASE)
    match = json_block_pattern.search(content)
    if match:
        json_str = match.group(1).strip()
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "reasoning" in data and "action" in data:
                return data
        except:
            pass
    
    brace_start = content.find('{')
    if brace_start != -1:
        depth = 0
        for i in range(brace_start, len(content)):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    json_str = content[brace_start:i + 1]
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict) and "reasoning" in data and "action" in data:
                            return data
                    except:
                        pass
                    break
    
    json_str = content.strip()
    if json_str.startswith("{") and json_str.endswith("}"):
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "reasoning" in data and "action" in data:
                return data
        except:
            pass
    
    return {}


def _split_content_with_thinking(content: str) -> list:
    """
    将内容按 thinking 标签分割为片段列表，保持顺序
    支持: <think>...</think> 和 ```json {reasoning, action}```
    返回: [{"type": "text/thinking", "content": "..."}]
    """
    if not content:
        return []
    
    import re
    result = []
    last_end = 0
    
    # 先处理 <think>...</think> 标签
    think_pat = re.compile(r'<think>\s*(.*?)\s*</think>', re.DOTALL)
    for match in think_pat.finditer(content):
        if match.start() > last_end:
            text_part = content[last_end:match.start()].strip()
            if text_part:
                result.extend(_split_inline_json(text_part))
        result.append({"type": "thinking", "content": match.group(1).strip()})
        last_end = match.end()
    
    rest = content[last_end:] if last_end > 0 else content
    
    # 再处理剩余文本中的 JSON thinking
    json_block_pattern = re.compile(r'```json\s*([\s\S]*?)\s*```', re.IGNORECASE)
    last_end = 0
    
    for match in json_block_pattern.finditer(rest):
        if match.start() > last_end:
            text_part = rest[last_end:match.start()].strip()
            if text_part:
                result.extend(_split_inline_json(text_part))
        
        json_str = match.group(1).strip()
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "reasoning" in data and "action" in data:
                result.append({"type": "thinking", "content": json.dumps(data, ensure_ascii=False, indent=2)})
            else:
                result.append({"type": "text", "content": match.group(0)})
        except:
            result.append({"type": "text", "content": match.group(0)})
        
        last_end = match.end()
    
    if last_end < len(rest):
        text_part = rest[last_end:].strip()
        if text_part:
            result.extend(_split_inline_json(text_part))
    
    return result


_FENCE_LINE_RE = re.compile(r'^[ \t]*(```|~~~)')
_INLINE_CODE_RE = re.compile(r'`[^`\n]+`')


def _code_spans(content: str):
    """代码块/行内代码区间，避免把引用内容里的 JSON 当成 thinking"""
    spans = []
    start = None
    pos = 0
    for line in content.splitlines(keepends=True):
        if _FENCE_LINE_RE.match(line):
            if start is None:
                start = pos
            else:
                spans.append((start, pos + len(line)))
                start = None
        pos += len(line)
    if start is not None:
        spans.append((start, len(content)))
    for match in _INLINE_CODE_RE.finditer(content):
        spans.append((match.start(), match.end()))
    return spans


def _split_inline_json(content: str) -> list:
    """检测并分割内联 JSON thinking，保持顺序"""
    spans = _code_spans(content)

    def _in_code(idx):
        return any(s <= idx < e for s, e in spans)

    search_from = 0
    while True:
        brace_start = content.find('{', search_from)
        if brace_start == -1:
            break
        if _in_code(brace_start):
            search_from = brace_start + 1
            continue

        depth = 0
        json_end = -1
        for i in range(brace_start, len(content)):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    json_end = i
                    break
        if json_end == -1:
            break

        json_str = content[brace_start:json_end + 1]
        try:
            data = json.loads(json_str)
        except Exception:
            data = None
        if isinstance(data, dict) and "reasoning" in data and "action" in data:
            result = []
            before = content[:brace_start].strip()
            if before:
                result.append({"type": "text", "content": before})
            result.append({"type": "thinking", "content": json.dumps(data, ensure_ascii=False, indent=2)})
            after = content[json_end + 1:].strip()
            if after:
                result.extend(_split_inline_json(after))
            return result

        search_from = brace_start + 1

    if content.strip():
        return [{"type": "text", "content": content}]
    return []


def _is_json_thinking(content: str) -> bool:
    return bool(_extract_json_thinking(content))

def _extract_final_text(content: str) -> str:
    if not content:
        return ""
    
    import re
    text = re.sub(r'<think>\s*.*?\s*</think>', '', content, flags=re.DOTALL)
    json_block_pattern = re.compile(r'```json\s*[\s\S]*?\s*```', re.IGNORECASE)
    text = json_block_pattern.sub('', text)
    text = re.sub(r'^\s*---\s*', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def _is_internal_user_message(msg: dict) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    return isinstance(content, str) and content.lstrip().startswith("[系统")


def _is_gene_phase_message(msg: dict) -> bool:
    """Gene 评估/写入阶段是内部流程，不应展示在历史里"""
    return (
        msg.get("role") == "system"
        and isinstance(msg.get("content"), str)
        and msg["content"].lstrip().startswith("[Gene")
    )


def _messages_to_renderable(raw_messages: list, tracked_ids=None) -> list:
    renderable = []
    i = 0
    while i < len(raw_messages):
        msg = raw_messages[i]
        role = msg.get("role", "")

        if role == "user":
            if _is_internal_user_message(msg):
                i += 1
                continue
            renderable.append({
                "id": msg.get("id"),
                "role": "user",
                "content": msg.get("content") or "",
                "timeline": None,
                "can_rewind": bool(
                    msg.get("id")
                    and not msg.get("_is_compacted_notes")
                    and msg.get("id") in (tracked_ids or set())
                ),
            })
            i += 1

        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            content = msg.get("content")

            if tool_calls:
                tool_traces = []
                timeline = []
                tc_map = {}
                final_text = ""
                final_reasoning = ""
                final_reasoning_ms = 0
                j = i

                while j < len(raw_messages):
                    sub = raw_messages[j]
                    role = sub.get("role", "")

                    if role == "tool":
                        seg = tc_map.get(sub.get("tool_call_id", ""))
                        if seg is not None:
                            try:
                                seg["result"] = json.loads(sub.get("content", "{}"))
                            except Exception:
                                seg["result"] = {"output": sub.get("content", "")}
                            ms = seg["result"].get("elapsed_ms") if isinstance(seg["result"], dict) else None
                            if ms:
                                try:
                                    seg["duration_ms"] = round(float(ms))
                                except (TypeError, ValueError):
                                    pass
                        j += 1
                        continue

                    if role == "system":
                        if _is_gene_phase_message(sub):
                            break
                        j += 1
                        continue

                    if _is_internal_user_message(sub):
                        j += 1
                        continue

                    if role == "assistant" and not sub.get("content") and not sub.get("tool_calls"):
                        j += 1
                        continue

                    if role == "assistant" and sub.get("tool_calls"):
                        sub_reasoning = sub.get("reasoning_content")
                        if sub_reasoning:
                            try:
                                sub_r_ms = round(float(sub.get("reasoning_duration_ms") or 0))
                            except (TypeError, ValueError):
                                sub_r_ms = 0
                            timeline.append({"kind": "reasoning", "content": sub_reasoning, "durationMs": sub_r_ms})
                        sub_content = sub.get("content")
                        if sub_content:
                            for seg_ in _split_content_with_thinking(sub_content):
                                if seg_["type"] == "thinking":
                                    timeline.append({"kind": "thinking", "content": seg_["content"]})
                                elif seg_["type"] == "text":
                                    timeline.append({"kind": "text", "content": seg_["content"]})
                        for tc in sub["tool_calls"]:
                            fn = tc.get("function", {})
                            try:
                                args = json.loads(fn.get("arguments", "{}")) if fn.get("arguments") else {}
                            except Exception:
                                args = {}
                            seg = {
                                "kind": "tool",
                                "tool": fn.get("name", "unknown"),
                                "arguments": args,
                                "result": None,
                                "duration_ms": 0,
                                "status": "done",
                            }
                            timeline.append(seg)
                            tc_map[tc.get("id", "")] = seg
                            tool_traces.append(seg)
                        j += 1
                        continue

                    if role == "assistant" and sub.get("content"):
                        final_text = sub.get("content", "")
                        final_reasoning = sub.get("reasoning_content") or ""
                        try:
                            final_reasoning_ms = round(float(sub.get("reasoning_duration_ms") or 0))
                        except (TypeError, ValueError):
                            final_reasoning_ms = 0
                        j += 1
                    break

                for seg in timeline:
                    if seg["kind"] != "tool":
                        continue
                    args = seg["arguments"]
                    result = seg.get("result")
                    if isinstance(result, dict):
                        if result.get("error"):
                            seg["description"] = str(result["error"])
                            continue
                        ms = result.get("elapsed_ms") or result.get("duration_ms") or 0
                        if not seg["duration_ms"]:
                            try:
                                seg["duration_ms"] = round(float(ms)) if ms else 0
                            except (TypeError, ValueError):
                                seg["duration_ms"] = 0

                    intent = (args.get("_intent") or "") if isinstance(args, dict) else ""
                    if intent.strip():
                        seg["description"] = intent.strip()
                    elif seg["tool"] == "shell":
                        cmd = args.get("command", "")[:80] if isinstance(args, dict) else ""
                        if isinstance(cmd, list):
                            cmd = " ".join(str(x) for x in cmd)[:80]
                        seg["description"] = f"正在执行：{cmd}..." if cmd else "正在执行命令"
                    elif seg["tool"] in ("shell_task", "shell_session"):
                        action = (args.get("action") or "") if isinstance(args, dict) else ""
                        label = "后台任务" if seg["tool"] == "shell_task" else "持久会话"
                        seg["description"] = f"正在操作{label}：{action}" if action else f"正在操作{label}"
                    elif seg["tool"] == "file":
                        action = args.get("action", "") if isinstance(args, dict) else ""
                        seg["description"] = f"正在查看：{action}" if action else "正在操作文件"
                    else:
                        seg["description"] = f"正在调用 {seg['tool']}"

                if final_reasoning:
                    timeline.append({"kind": "reasoning", "content": final_reasoning, "durationMs": final_reasoning_ms})

                if final_text:
                    for seg_ in _split_content_with_thinking(final_text):
                        if seg_["type"] == "thinking":
                            timeline.append({"kind": "thinking", "content": seg_["content"]})
                        elif seg_["type"] == "text":
                            timeline.append({"kind": "text", "content": seg_["content"]})

                collected_attachments = []
                seen = set()
                for seg in timeline:
                    if seg["kind"] != "tool":
                        continue
                    result = seg.get("result")
                    if not isinstance(result, dict):
                        continue
                    for att in result.get("attachments") or []:
                        if not isinstance(att, dict):
                            continue
                        key = att.get("url") or att.get("file_id")
                        if key and key not in seen:
                            seen.add(key)
                            collected_attachments.append(att)

                renderable.append({
                    "id": msg.get("id"),
                    "role": "assistant",
                    "content": final_text or "",
                    "toolTraces": tool_traces,
                    "timeline": timeline,
                    "attachments": collected_attachments,
                })
                i = j

            else:
                collected_attachments = []
                timeline = []
                reasoning = msg.get("reasoning_content")
                if reasoning:
                    try:
                        r_ms = round(float(msg.get("reasoning_duration_ms") or 0))
                    except (TypeError, ValueError):
                        r_ms = 0
                    timeline.append({"kind": "reasoning", "content": reasoning, "durationMs": r_ms})
                if content:
                    # 使用新的分割函数，保持文本和 thinking 的顺序
                    segments = _split_content_with_thinking(content)
                    for seg in segments:
                        if seg["type"] == "thinking":
                            timeline.append({"kind": "thinking", "content": seg["content"]})
                        else:
                            timeline.append({"kind": "text", "content": seg["content"]})
                
                renderable.append({
                    "id": msg.get("id"),
                    "role": "assistant",
                    "content": content or "",
                    "toolTraces": [],
                    "timeline": timeline,
                    "attachments": collected_attachments,
                })
                i += 1
        elif role == "system":
            # Gene 评估阶段属于内部流程，整段跳过（含其中的工具调用与总结）
            if _is_gene_phase_message(msg):
                i += 1
                while i < len(raw_messages):
                    nxt = raw_messages[i]
                    if nxt.get("role") == "user" and not _is_internal_user_message(nxt):
                        break
                    i += 1
            else:
                i += 1
        else:
            i += 1

    return renderable


_HISTORY_CACHE = {}
_HISTORY_CACHE_MAX = 8
_HISTORY_CACHE_LOCK = threading.Lock()
_HISTORY_MAX_MESSAGES = 4000     # 单次读取窗口上限
_HISTORY_ALIGN_HEADROOM = 200    # 分页对齐到用户消息的余量


def _archive_signature(archive):
    base = getattr(archive, "base_dir", None)
    if not base:
        return None
    try:
        files = [f for f in os.listdir(base) if f.endswith(".jsonl")]
        if not files:
            return None
        newest = max(files, key=lambda f: os.stat(os.path.join(base, f)).st_mtime_ns)
        st = os.stat(os.path.join(base, newest))
    except OSError:
        return None
    return (len(files), newest, st.st_mtime_ns, st.st_size)


def _archive_revision(archive, session_id=None):
    rev = getattr(archive, "revision", None)
    if callable(rev):
        try:
            return ("rev", int(rev(session_id) if session_id else rev()))
        except TypeError:
            try:
                return ("rev", int(rev()))
            except Exception:
                pass
        except Exception:
            pass
    return ("sig", _archive_signature(archive))


def _msg_dedupe_key(msg):
    content = msg.get("content")
    if isinstance(content, str):
        key = (
            msg.get("role"), msg.get("id"), msg.get("timestamp"),
            msg.get("tool_call_id"), len(content), hash(content),
        )
    else:
        key = (json.dumps(content, ensure_ascii=False, sort_keys=True),)
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        key += (json.dumps(tool_calls, ensure_ascii=False, sort_keys=True),)
    return key


def _page_bounds(messages: list, limit: int, offset: int):
    """分页起点回退到本轮真实用户消息，避免 assistant(tool_calls) 被切走"""
    total = len(messages)
    end = max(0, total - offset)
    start = max(0, end - limit)
    while start > 0:
        head = messages[start]
        if head.get("role") == "user" and not _is_internal_user_message(head):
            break
        start -= 1
    return start, end


def _load_archive_messages(archive, session_id, limit=None, offset=0):
    """最近 limit 条消息（正序）"""
    need = min(limit or _HISTORY_MAX_MESSAGES, _HISTORY_MAX_MESSAGES)
    revision = _archive_revision(archive, session_id)
    cache_key = (session_id, revision) if revision[1] is not None and offset == 0 else None
    if cache_key is not None:
        with _HISTORY_CACHE_LOCK:
            entry = _HISTORY_CACHE.get(cache_key)
        if isinstance(entry, dict) and entry.get("limit", 0) >= need:
            return list(entry["messages"][-need:])

    try:
        reader = getattr(archive, "get_recent_messages", None)
        if reader is not None:
            raw_msgs = reader(session_id, need, offset)
        else:
            raw_msgs = _records_messages_tail(archive, session_id, need, offset)
    except Exception as e:
        logger.warning("[History] archive 读取失败，回退到内存: %s", e)
        return []

    if cache_key is not None:
        with _HISTORY_CACHE_LOCK:
            if cache_key not in _HISTORY_CACHE and len(_HISTORY_CACHE) >= _HISTORY_CACHE_MAX:
                _HISTORY_CACHE.pop(next(iter(_HISTORY_CACHE)))
            _HISTORY_CACHE[cache_key] = {"messages": list(raw_msgs), "limit": need}
    return raw_msgs


def _records_messages_tail(archive, session_id, limit, offset):
    """非 SQLite 归档（JSONL）兼容：按记录读再取消息尾部"""
    all_messages = []
    seen = set()
    records = archive.get_by_session(session_id, limit=500)
    for rec in reversed(records):
        msgs = rec.get("messages")
        if not isinstance(msgs, list):
            continue
        filtered = [m for m in msgs if not (m.get("role") == "user" and m.get("_is_compacted_notes"))]
        for msg in reversed(filtered):
            msg_key = _msg_dedupe_key(msg)
            if msg_key not in seen:
                seen.add(msg_key)
                all_messages.append(msg)
    all_messages.reverse()
    end = len(all_messages) - max(0, offset)
    return all_messages[max(0, end - limit):end]


def _history_total(session_id, fallback=0):
    try:
        mgr = get_session_manager()
        archive = mgr.three_layer_memory.archive if mgr.three_layer_memory else None
        counter = getattr(archive, "count_messages", None) if archive else None
        if counter is not None:
            return max(int(counter(session_id)), fallback)
    except Exception:
        pass
    return fallback


def _load_raw_history(session_id, limit=None, offset=0):
    mgr = get_session_manager()
    archive = mgr.three_layer_memory.archive if mgr.three_layer_memory else None
    if archive:
        raw_msgs = _load_archive_messages(archive, session_id, limit, offset)
        if raw_msgs:
            logger.debug("[History] 从 archive 恢复 | session=%s | %d 条消息", session_id, len(raw_msgs))
            return raw_msgs
    info = mgr.get_or_create(session_id)
    raw_msgs = info.memory.get_messages()
    if limit:
        end = max(0, len(raw_msgs) - max(0, offset))
        return raw_msgs[max(0, end - limit):end]
    logger.debug("[History] 从内存读取 | session=%s | raw=%d 条", session_id, len(raw_msgs))
    return raw_msgs


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    limit: int = Query(150, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取会话的历史消息"""
    fetch_n = limit + _HISTORY_ALIGN_HEADROOM
    raw_msgs = await asyncio.to_thread(_load_raw_history, session_id, fetch_n, offset)

    if not raw_msgs:
        return {"session_id": session_id, "messages": [], "count": 0, "total": 0, "has_more": False, "next_offset": 0}

    start_idx, end_idx = _page_bounds(raw_msgs, limit, 0)
    paged_raw = raw_msgs[start_idx:end_idx]
    consumed = offset + (end_idx - start_idx)
    total = _history_total(session_id, fallback=consumed)

    logger.debug(
        "[History] session=%s | paged=%d 条 (slice[%d:%d]) | offset=%d | total=%d",
        session_id, len(paged_raw), start_idx, end_idx, offset, total,
    )

    try:
        from app.agent.loop.rewind import rewindable_user_ids
        tracked_ids = rewindable_user_ids(session_id)
    except Exception:
        tracked_ids = set()
    renderable = _messages_to_renderable(paged_raw, tracked_ids) if paged_raw else []

    return {
        "session_id": session_id,
        "messages": renderable,
        "count": len(renderable),
        "total": total,
        "has_more": consumed < total,
        "next_offset": consumed,
    }


# ── 会话持久化 API ───────────────────────────────────────────


@router.get("/session/last")
async def get_last_session(channel: str = Query("")):
    """获取最后活跃的 session_id"""
    store = get_session_store()
    last_id = store.get_client_last_session(channel) if channel else None
    if not last_id:
        last_id = store.get_last_active_session()

    if last_id:
        return {"session_id": last_id, "exists": True}

    return {"session_id": None, "exists": False}


@router.post("/session/{session_id}/activate")
async def activate_session(session_id: str, channel: str = Query("")):
    """记录指定客户端最后使用的会话"""
    store = get_session_store()
    if channel:
        store.set_client_last_session(session_id, channel)
    else:
        store.get_or_create_session(session_id)
    return {"status": "ok", "session_id": session_id, "channel": channel}


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

    from app.core.util.session_status import is_session_running
    items = []
    for s in sessions:
        data = s.to_dict()
        data["running"] = is_session_running(s.session_id)
        items.append(data)
    return {"sessions": items, "total": len(items)}


@router.delete("/session/{session_id}")
async def delete_session_permanent(session_id: str):
    """永久删除会话"""
    store = get_session_store()
    deleted = store.delete_session(session_id)

    session_mgr = get_session_manager()
    session_mgr.close_session(session_id)
    try:
        from app.core.util.undo_store import get_undo_store
        get_undo_store().drop_session(session_id)
    except Exception:
        pass

    if deleted:
        return {"status": "deleted", "session_id": session_id}
    return {"status": "not_found", "session_id": session_id}


@router.patch("/session/{session_id}/title")
async def rename_session(session_id: str, req: RenameSessionRequest):
    """重命名会话标题"""
    title = "".join(" " if ord(ch) < 32 else ch for ch in str(req.title or ""))
    title = " ".join(title.split()).strip()[:60]
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    store = get_session_store()
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    store.set_session_title(session_id, title)
    return {"status": "ok", "session_id": session_id, "title": title}


@router.get("/session/{session_id}/mode")
async def get_session_mode(session_id: str):
    from app.server.session_mode import get_mode
    return {"session_id": session_id, "mode": get_mode(session_id)}


@router.post("/session/{session_id}/mode")
async def update_session_mode(session_id: str, req: ModeRequest):
    from app.server.session_mode import set_mode
    mode = set_mode(session_id, req.mode)
    logger.info("[chat] 会话模式切换 | session=%s | mode=%s", session_id, mode)
    return {"status": "ok", "session_id": session_id, "mode": mode}


class SessionModelRequest(BaseModel):
    model: Optional[str] = ""


@router.get("/session/{session_id}/model")
async def get_session_model(session_id: str):
    """会话使用的 LLM 模型（可与其他会话隔离）"""
    from app.server import session_llm
    return {
        "session_id": session_id,
        "model": session_llm.get_model(session_id) or session_llm.global_model(),
        "override": session_llm.has_override(session_id),
        "global_model": session_llm.global_model(),
        "models": session_llm.list_models(),
    }


@router.post("/session/{session_id}/model")
async def update_session_model(session_id: str, req: SessionModelRequest):
    """给会话绑定独立模型配置；model 传空表示跟随全局"""
    from app.server import session_llm
    result = session_llm.set_model(session_id, req.model)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "设置失败")
    session_llm.apply_to_loop(session_id)
    payload = {
        "type": "session_model_changed",
        "session_id": session_id,
        "model": result["model"],
        "override": result["override"],
    }
    try:
        from app.messaging.message_broker import get_message_broker
        get_message_broker().publish(session_id, dict(payload))
    except Exception:
        pass
    try:
        from app.server.routes.ws_event_manager import ws_publish_event
        ws_publish_event("chat_event", dict(payload), session_id=session_id)
    except Exception:
        pass
    logger.info(
        "[chat] 会话模型切换 | session=%s | model=%s | override=%s",
        session_id, result["model"], result["override"],
    )
    return {"status": "ok", **result}


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
        tool_calls_data = []
        for trace in req.tool_traces:
            if trace.get("truncated") or trace.get("folded"):
                continue
            tool_name = trace.get("tool", "")
            arguments = trace.get("arguments", {})
            result = trace.get("result", {})
            duration_ms = trace.get("duration_ms", 0)

            if tool_name:
                tool_calls_data.append({
                    "tool_name": tool_name,
                    "arguments": arguments,
                })
                if result:
                    if isinstance(result, dict):
                        result["elapsed_ms"] = duration_ms

        if tool_calls_data:
            tool_call_id_list = memory.add_tool_calls_batch(tool_calls_data)
            for idx, trace in enumerate(req.tool_traces):
                tool_name = trace.get("tool", "")
                result = trace.get("result", {})
                if tool_name and idx < len(tool_call_id_list):
                    tc_id = tool_call_id_list[idx]
                    tool_call_ids[tool_name] = tc_id
                    if result:
                        memory.add_tool_result(tc_id, result)

    if req.assistant_message:
        memory.add_assistant_message(req.assistant_message)

    if session_mgr.three_layer_memory:
        try:
            round_messages = [
                {"role": "user", "content": req.user_message},
                {"role": "assistant", "content": req.assistant_message},
            ]
            session_mgr.three_layer_memory.persist_session(
                user_input=req.user_message,
                response=req.assistant_message,
                session_id=session_id,
                messages=round_messages,
            )

        except Exception as e:
            logger.error("[save_message] 持久化失败: %s", e)
            return {"status": "error", "error": str(e)}

    session_info.message_count += 1
    store = get_session_store()
    store.update_message_count(session_id, delta=1)

    return {"status": "ok", "session_id": session_id, "message_count": session_info.message_count}
