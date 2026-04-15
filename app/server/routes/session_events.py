# -*- coding: utf-8 -*-
"""
Session 事件路由 - WebSocket 推送
"""

import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/session-events", tags=["sessions"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: Optional[str] = Query(None),  # 忽略，由服务端生成
    session_id: Optional[str] = Query(None),
):
    """
    WebSocket 事件端点

    连接参数（query string）:
    - client_id: 客户端标识（已忽略，由服务端自动生成）
    - session_id: 关联的会话 ID（可选，用于定向推送）

    消息格式（发送给你的服务）:
    - {"type": "ping"} - 心跳检测

    消息格式（你收到的消息）:
    - {"type": "session_update", "session_id": "xxx", "data": {...}}
    - {"type": "task_result", "session_id": "xxx", "data": {...}}
    - {"type": "error", "message": "..."}
    """
    from app.server.routes.ws_event_manager import WSConnectionManager, ws_publish_event

    manager = await WSConnectionManager.get_instance()

    await websocket.accept()

    # 忽略客户端传入的 client_id，强制由服务端生成
    ws_client_id = await manager.add_client(websocket, client_id=None, session_id=session_id)

    try:
        await websocket.send_json({
            "type": "connected",
            "client_id": ws_client_id,
            "message": "WebSocket 连接已建立"
        })

        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)

                msg_type = message.get("type")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "session_update":
                    target_session = message.get("session_id")
                    event_data = message.get("data", {})
                    if target_session:
                        ws_publish_event("session_updated", event_data, session_id=target_session)
                    else:
                        ws_publish_event("session_updated", event_data)

                elif msg_type == "subscribe":
                    target_session = message.get("session_id")
                    if target_session:
                        await manager.update_session(ws_client_id, target_session)
                        await websocket.send_json({
                            "type": "subscribed",
                            "session_id": target_session
                        })

                elif msg_type == "unsubscribe":
                    await manager.update_session(ws_client_id, "")
                    await websocket.send_json({"type": "unsubscribed"})

                else:
                    logger.debug(f"[WS] 收到未知消息类型: {msg_type}")

            except json.JSONDecodeError:
                logger.warning(f"[WS] 收到无效 JSON: {data[:100]}")
            except WebSocketDisconnect:
                logger.info(f"[WS] 客户端断开: {ws_client_id}")
                break

    except WebSocketDisconnect:
        logger.info(f"[WS] WebSocket 断开: {ws_client_id}")
    except Exception as e:
        logger.error(f"[WS] 连接异常: {e}")
    finally:
        await manager.remove_client(ws_client_id)


@router.get("/status")
async def get_status():
    """获取 WebSocket 连接状态"""
    from app.server.routes.ws_event_manager import WSConnectionManager
    manager = await WSConnectionManager.get_instance()
    return {
        "connected_clients": manager.get_client_count(),
        "active_sessions": manager.get_session_count(),
        "transport": "websocket"
    }


def publish_session_event(event_type: str, data: dict, session_id: Optional[str] = None):
    """
    兼容旧 SSE 接口的发布会话事件函数

    内部已改为 WebSocket 推送
    """
    from app.server.routes.ws_event_manager import ws_publish_event
    ws_publish_event(event_type, data, session_id=session_id)