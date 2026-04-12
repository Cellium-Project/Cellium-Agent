# -*- coding: utf-8 -*-
"""
Session 事件广播器 - 使用 SSE 推送会话更新到前端
"""

import asyncio
import logging
from typing import Dict, Set, Optional
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse
import json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/session-events", tags=["sessions"])

_active_subscribers: Set[asyncio.Queue] = set()
_broadcast_queue: asyncio.Queue = asyncio.Queue()
_broadcaster_task: Optional[asyncio.Task] = None
_broadcast_lock: asyncio.Lock = asyncio.Lock()


async def _broadcaster():
    """广播协调器（确保并发安全）"""
    while True:
        try:
            event_type, payload = await _broadcast_queue.get()
            async with _broadcast_lock:
                if not _active_subscribers:
                    logger.debug(f"[SessionEvents] 无订阅者，跳过事件: {event_type}")
                    continue

                logger.info(f"[SessionEvents] 推送事件: {event_type} 到 {len(_active_subscribers)} 个订阅者")
                for queue in _active_subscribers:
                    try:
                        queue.put_nowait(payload)
                    except Exception as e:
                        logger.warning(f"[SessionEvents] 发送事件失败: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[SessionEvents] Broadcaster error: {e}")


async def _start_broadcaster():
    """启动广播协调器"""
    global _broadcaster_task
    if _broadcaster_task is None or _broadcaster_task.done():
        _broadcaster_task = asyncio.create_task(_broadcaster())
        logger.info("[SessionEvents] Broadcaster started")


def publish_session_event(event_type: str, data: Dict):
    """发布会话事件到所有订阅者"""
    event = {
        "type": event_type,
        "data": data,
    }
    payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    _broadcast_queue.put_nowait((event_type, payload))


@router.get("/events")
async def session_events(request: Request):
    """
    Session 事件流（SSE）

    前端可通过 EventSource 订阅此端点，实时接收会话更新：
    - session_created: 新会话创建
    - session_updated: 会话更新（消息数、活跃时间等）
    - session_deleted: 会话删除
    """
    if not _broadcaster_task or _broadcaster_task.done():
        await _start_broadcaster()

    queue = asyncio.Queue()
    _active_subscribers.add(queue)
    logger.info(f"[SessionEvents] 新订阅者接入，当前订阅数: {len(_active_subscribers)}")

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30)
                    yield payload
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.warning(f"[SessionEvents] 订阅者断开: {e}")
        finally:
            _active_subscribers.discard(queue)
            logger.info(f"[SessionEvents] 订阅者断开，剩余: {len(_active_subscribers)}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def get_subscriber_count() -> int:
    """获取当前订阅者数量（用于调试）"""
    return len(_active_subscribers)
