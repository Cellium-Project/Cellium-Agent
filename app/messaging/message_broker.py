# -*- coding: utf-8 -*-
"""按 session 隔离的消息广播层"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class MessageBroker:

    MAX_HISTORY_SIZE = 500  # 事件历史上限，防内存溢出

    def __init__(self, ws_publish: Optional[Callable] = None):
        self._queues: Dict[str, asyncio.Queue] = {}      # WebUI 主队列
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}   # 多端订阅队列
        self._event_history: Dict[str, list] = {}
        self._event_counters: Dict[str, int] = {}
        self._ws_publish = ws_publish
        if ws_publish is None:
            try:
                from app.server.routes.ws_event_manager import ws_publish_event
                self._ws_publish = ws_publish_event
            except ImportError:
                self._ws_publish = None

    def set_ws_publish(self, fn: Optional[Callable]):
        """注入/替换 ws 推送函数"""
        self._ws_publish = fn

    @property
    def ws_publish(self) -> Optional[Callable]:
        return self._ws_publish

    def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        try:
            loop = asyncio.get_running_loop()
            queue._loop = loop
            logger.debug("[Broker] 订阅创建 | session=%s | queue_loop=%s", session_id, loop)
        except RuntimeError:
            logger.warning("[Broker] 订阅创建时无运行中的 loop | session=%s", session_id)
        if session_id not in self._subscribers:
            self._subscribers[session_id] = set()
        self._subscribers[session_id].add(queue)
        logger.info("[Broker] 订阅已注册 | session=%s | 订阅者数=%d", session_id, len(self._subscribers[session_id]))
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue):
        subs = self._subscribers.get(session_id)
        if subs:
            subs.discard(queue)
            if not subs:
                del self._subscribers[session_id]

    def get_queue(self, session_id: str) -> Optional[asyncio.Queue]:
        """WebUI 主队列（start_task 时创建）"""
        return self._queues.get(session_id)

    def ensure_queue(self, session_id: str) -> asyncio.Queue:
        """不存在则创建 WebUI 主队列"""
        queue = self._queues.get(session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[session_id] = queue
        return queue

    def publish(self, session_id: str, event: Dict[str, Any]):
        decorated = self.decorate_event(session_id, event)
        self.append_history(session_id, decorated)

        main_queue = self._queues.get(session_id)
        if main_queue is not None:
            self._put_to_queue(main_queue, decorated)

        subs = self._subscribers.get(session_id)
        if subs:
            for q in list(subs):
                self._put_to_queue(q, decorated)
        else:
            logger.warning("[Broker] 无订阅者 | session=%s | event=%s", session_id, event.get("type"))

        if self._ws_publish is not None:
            try:
                self._ws_publish("chat_event", decorated, session_id)
            except Exception as e:
                logger.debug("[Broker] ws 推送失败: %s", e)

    def publish_terminal(self, session_id: str):
        """向订阅队列广播终止哨兵（None），通知消费者收尾。

        仅推送到 subscribe() 订阅队列（channel/TUI 消费端），
        不写入历史、不触发 ws 推送；WebUI 主队列的收尾由调用方自行 put(None)。
        """
        subs = self._subscribers.get(session_id)
        if subs:
            for q in list(subs):
                self._put_to_queue(q, None)

    def get_event_history(self, session_id: str, after_id: Optional[int] = None) -> List[Dict]:
        history = self._event_history.get(session_id, [])
        if after_id is None:
            return list(history)
        return [e for e in history if (e.get("event_id") or 0) > after_id]

    def get_latest_event_id(self, session_id: str) -> int:
        history = self._event_history.get(session_id)
        if not history:
            return 0
        return history[-1].get("event_id", 0)

    def decorate_event(self, session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """补 session_id / 单调递增 event_id"""
        decorated = dict(event)
        decorated["session_id"] = session_id
        decorated["event_id"] = self._event_counters.get(session_id, 0) + 1
        self._event_counters[session_id] = decorated["event_id"]
        return decorated

    def append_history(self, session_id: str, event: Dict[str, Any]):
        if session_id not in self._event_history:
            self._event_history[session_id] = []
        history = self._event_history[session_id]
        history.append(event)
        if len(history) > self.MAX_HISTORY_SIZE:
            history[:] = history[-self.MAX_HISTORY_SIZE:]

    def clear_session(self, session_id: str, clear_subscribers: bool = False):
        """清理任务态数据（历史/计数/主队列），默认保留订阅者"""
        self._queues.pop(session_id, None)
        if clear_subscribers:
            self._subscribers.pop(session_id, None)
        self._event_history.pop(session_id, None)
        self._event_counters.pop(session_id, None)

    def is_subscribed(self, session_id: str, queue: asyncio.Queue) -> bool:
        """检测队列是否仍在该 session 订阅集合中（供订阅者自检）"""
        subs = self._subscribers.get(session_id)
        return subs is not None and queue in subs

    def _put_to_queue(self, queue: asyncio.Queue, event: Dict[str, Any]):
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        target_loop = getattr(queue, "_loop", None)
        if target_loop is None:
            try:
                target_loop = queue._get_loop()
            except (RuntimeError, AttributeError):
                target_loop = None

        if current_loop is not None and target_loop is not None and current_loop is not target_loop:
            try:
                asyncio.run_coroutine_threadsafe(queue.put(event), target_loop)
                logger.debug("[Broker] 跨 loop 投递成功 | current=%s | target=%s", current_loop, target_loop)
            except Exception as e:
                logger.warning("[Broker] 跨 loop 投递失败: %s | current=%s | target=%s", e, current_loop, target_loop)
                try:
                    queue.put_nowait(event)
                except Exception as e2:
                    logger.error("[Broker] 回退 put_nowait 失败: %s", e2)
            return
        try:
            queue.put_nowait(event)
        except Exception as e:
            logger.error("[Broker] put_nowait 失败: %s", e)


_broker: Optional[MessageBroker] = None


def get_message_broker() -> MessageBroker:
    """模块级单例"""
    global _broker
    if _broker is None:
        _broker = MessageBroker()
    return _broker
