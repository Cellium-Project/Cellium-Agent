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
        """注册一个订阅队列（TUI/channel 使用），由调用方在自身 loop 中消费"""
        queue: asyncio.Queue = asyncio.Queue()
        if session_id not in self._subscribers:
            self._subscribers[session_id] = set()
        self._subscribers[session_id].add(queue)
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
        """广播到 WebUI 主队列 + 订阅队列 + ws 推送"""
        decorated = self.decorate_event(session_id, event)
        self.append_history(session_id, decorated)

        main_queue = self._queues.get(session_id)
        if main_queue is not None:
            self._put_to_queue(main_queue, decorated)

        subs = self._subscribers.get(session_id)
        if subs:
            for q in list(subs):
                self._put_to_queue(q, decorated)

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

    def clear_session(self, session_id: str, keep_subscribers: bool = False):
        """清理该 session 的任务状态（历史/计数/主队列）。

        Args:
            keep_subscribers: 为 True 时保留订阅队列（任务重启不踢掉常驻订阅者，
                如 TUI 的常驻监听器，否则 start_task 会清空其订阅导致失联）
        """
        self._queues.pop(session_id, None)
        if not keep_subscribers:
            self._subscribers.pop(session_id, None)
        self._event_history.pop(session_id, None)
        self._event_counters.pop(session_id, None)

    def _put_to_queue(self, queue: asyncio.Queue, event: Dict[str, Any]):
        """跨 loop/线程安全投递：队列不属于当前 loop 时调度到其 loop"""
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
            except Exception as e:
                logger.debug("[Broker] 跨 loop 投递失败，回退 put_nowait: %s", e)
                try:
                    queue.put_nowait(event)
                except Exception:
                    pass
            return
        try:
            queue.put_nowait(event)
        except Exception:
            pass


_broker: Optional[MessageBroker] = None


def get_message_broker() -> MessageBroker:
    """模块级单例"""
    global _broker
    if _broker is None:
        _broker = MessageBroker()
    return _broker
