# -*- coding: utf-8 -*-
"""
WebSocket 事件管理器
"""

import asyncio
import json
import logging
from typing import Dict, Set, Optional, Any
from dataclasses import dataclass
import weakref

logger = logging.getLogger(__name__)


@dataclass
class WSMessage:
    """WebSocket 消息结构"""
    type: str
    data: Dict[str, Any]
    session_id: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type,
            "data": self.data,
            "session_id": self.session_id,
        }, ensure_ascii=False)


class WSClient:
    """单个 WebSocket 客户端连接"""
    def __init__(self, websocket, client_id: str, session_id: Optional[str] = None):
        self.websocket = websocket
        self.client_id = client_id
        self.session_id = session_id
        self._closed = False
        self.published_ids: set = set()
        self.replaying = False
        self.replay_backlog: list = []  

    async def send(self, message: WSMessage):
        """发送消息到客户端"""
        if self._closed:
            return
        try:
            await self.websocket.send_text(message.to_json())
        except Exception as e:
            logger.warning(f"[WSClient] 发送消息失败: {e}")
            self._closed = True

    async def close(self):
        """关闭连接"""
        self._closed = True
        try:
            await self.websocket.close()
        except Exception:
            pass


class WSConnectionManager:
    """
    WebSocket 连接管理器

    支持：
    - 全局广播（所有连接）
    - 按 session_id 定向推送
    - 客户端分组
    - 事件去重（同一 event_id 只推送一次）
    """
    _instance: Optional["WSConnectionManager"] = None
    _lock: Optional[asyncio.Lock] = None
    _main_loop: Optional[asyncio.AbstractEventLoop] = None

    _SEND_TIMEOUT: float = 5.0
    _SESSION_QUEUE_LIMIT: int = 2000
    _WORKER_IDLE_EXIT: float = 60.0
    _TERMINAL_EVENT_TYPES = frozenset({
        "done", "error", "tool_result", "subagent_done", "delegation_done", "messages_rewound",
    })

    def __init__(self):
        self._clients: Dict[str, WSClient] = {}
        self._session_clients: Dict[str, Set[str]] = {}
        self._client_counter = 0
        self._cleanup_task: Optional[asyncio.Task] = None
        self._max_published_ids: int = 10000
        self._session_queues: Dict[str, asyncio.Queue] = {}
        self._session_workers: Dict[str, asyncio.Task] = {}
        self._dropped_events: int = 0

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    def set_main_loop(cls, loop: asyncio.AbstractEventLoop):
        cls._main_loop = loop

    @classmethod
    def get_main_loop(cls) -> Optional[asyncio.AbstractEventLoop]:
        return cls._main_loop

    @classmethod
    async def get_instance(cls) -> "WSConnectionManager":
        if cls._instance is None:
            async with cls._get_lock():
                if cls._instance is None:
                    cls._instance = cls()
                    try:
                        cls._main_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        pass
                    cls._instance._start_cleanup_task()
        return cls._instance

    @classmethod
    def get_instance_sync(cls) -> "WSConnectionManager":
        """同步获取单例（仅用于非异步上下文）"""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._start_cleanup_task()
        return cls._instance

    def _start_cleanup_task(self):
        """启动清理任务"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        """定期清理关闭的连接"""
        while True:
            try:
                await asyncio.sleep(30)
                await self._cleanup_closed_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[WSManager] 清理任务异常: {e}")

    async def _cleanup_closed_connections(self):
        """清理已关闭的连接"""
        closed_ids = [cid for cid, client in self._clients.items() if client._closed]
        for cid in closed_ids:
            await self.remove_client(cid)

    async def add_client(self, websocket, client_id: Optional[str] = None, session_id: Optional[str] = None) -> str:
        """添加客户端连接"""
        if client_id is None:
            self._client_counter += 1
            client_id = f"ws_{self._client_counter}"

        client = WSClient(websocket, client_id, session_id)
        self._clients[client_id] = client

        if session_id:
            if session_id not in self._session_clients:
                self._session_clients[session_id] = set()
            self._session_clients[session_id].add(client_id)

        logger.info(f"[WSManager] 客户端接入: {client_id}, session={session_id}, 当前连接数: {len(self._clients)}")
        return client_id

    async def remove_client(self, client_id: str):
        """移除客户端连接"""
        if client_id not in self._clients:
            return

        client = self._clients[client_id]
        session_id = client.session_id

        del self._clients[client_id]

        if session_id and session_id in self._session_clients:
            self._session_clients[session_id].discard(client_id)
            if not self._session_clients[session_id]:
                del self._session_clients[session_id]

        logger.info(f"[WSManager] 客户端断开: {client_id}, 剩余: {len(self._clients)}")

    async def update_session(self, client_id: str, session_id: str):
        """更新客户端关联的 session"""
        if client_id not in self._clients:
            return

        client = self._clients[client_id]
        old_session = client.session_id

        if old_session and old_session in self._session_clients:
            self._session_clients[old_session].discard(client_id)

        client.session_id = session_id

        if session_id not in self._session_clients:
            self._session_clients[session_id] = set()
        self._session_clients[session_id].add(client_id)

        logger.debug(f"[WSManager] 客户端 {client_id} 关联 session: {old_session} -> {session_id}")

    async def _safe_send(self, client: WSClient, message: WSMessage) -> bool:
        """发送单条消息，带超时；超时视为客户端失效并断开"""
        try:
            await asyncio.wait_for(client.send(message), timeout=self._SEND_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"[WSManager] 发送超时 {self._SEND_TIMEOUT}s，断开客户端 {client.client_id}")
            await client.close()
            return False
        except Exception as e:
            logger.warning(f"[WSManager] 发送到 {client.client_id} 失败: {e}")
            client._closed = True
            return False

    def _is_terminal(self, message: WSMessage) -> bool:
        return bool(message and message.type in self._TERMINAL_EVENT_TYPES)

    def _evict_incremental(self, queue: asyncio.Queue) -> bool:
        inner = getattr(queue, "_queue", None)
        if inner is None:
            return False
        for index, item in enumerate(inner):
            if not self._is_terminal(item):
                del inner[index]
                if getattr(queue, "_unfinished_tasks", 0) > 0:
                    queue._unfinished_tasks -= 1
                return True
        return False

    def enqueue_to_session(self, session_id: str, message: WSMessage) -> bool:
        """把事件投递到 session 队列，由单消费者按序发送"""
        if not self._session_clients.get(session_id):
            return False

        queue = self._session_queues.get(session_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=self._SESSION_QUEUE_LIMIT)
            self._session_queues[session_id] = queue

        worker = self._session_workers.get(session_id)
        if worker is None or worker.done():
            try:
                self._session_workers[session_id] = asyncio.create_task(
                    self._session_worker(session_id)
                )
            except RuntimeError:
                return False

        try:
            queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            if self._is_terminal(message):
                if self._evict_incremental(queue):
                    queue.put_nowait(message)
                    self._dropped_events += 1
                    logger.warning(
                        f"[WSManager] session {session_id} 队列已满，挤掉增量事件以投递终态 {message.type}"
                    )
                    return True
                try:
                    queue.get_nowait()
                    if getattr(queue, "_unfinished_tasks", 0) > 0:
                        queue._unfinished_tasks -= 1
                    queue.put_nowait(message)
                    self._dropped_events += 1
                    logger.warning(
                        f"[WSManager] session {session_id} 队列已满，挤掉最旧事件以投递终态 {message.type}"
                    )
                    return True
                except Exception:
                    pass
            self._dropped_events += 1
            if self._dropped_events % 100 == 1:
                logger.warning(
                    f"[WSManager] session {session_id} 队列已满({self._SESSION_QUEUE_LIMIT})，"
                    f"丢弃事件 {message.type}，累计丢弃 {self._dropped_events}"
                )
            return False

    async def _session_worker(self, session_id: str):
        """session 队列消费者，保证同一 session 事件顺序"""
        queue = self._session_queues.get(session_id)
        if queue is None:
            return

        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=self._WORKER_IDLE_EXIT)
            except asyncio.TimeoutError:
                if queue.empty() and not self._session_clients.get(session_id):
                    if self._session_queues.get(session_id) is queue:
                        self._session_queues.pop(session_id, None)
                    self._session_workers.pop(session_id, None)
                    return
                continue
            except asyncio.CancelledError:
                raise

            try:
                await self.send_to_session(session_id, message)
            except Exception as e:
                logger.warning(f"[WSManager] session {session_id} 推送异常: {e}")
            finally:
                queue.task_done()

    async def broadcast(self, message: WSMessage):
        """广播到所有客户端，各连接并发发送，慢连接只影响自己"""
        if not self._clients:
            logger.debug("[WSManager] 无客户端，跳过广播")
            return

        clients = list(self._clients.items())
        results = await asyncio.gather(
            *[self._safe_send(client, message) for _, client in clients],
            return_exceptions=True,
        )

        disconnected = [
            client_id for (client_id, _), ok in zip(clients, results) if ok is not True
        ]
        for client_id in disconnected:
            await self.remove_client(client_id)

        logger.debug(f"[WSManager] 广播完成: {message.type}, 客户端数: {len(self._clients)}")

    def _client_seen(self, client: WSClient, event_data: Dict[str, Any]) -> bool:
        """检查该客户端是否已收到过此事件（去重按客户端隔离，回放与实时推送共用）"""
        event_id = event_data.get("event_id")
        if event_id is None:
            return False

        session_id = event_data.get("session_id", "global")
        event_key = f"{session_id}:{event_id}"
        if event_key in client.published_ids:
            return True

        client.published_ids.add(event_key)
        if len(client.published_ids) > self._max_published_ids:
            client.published_ids = set(list(client.published_ids)[-self._max_published_ids:])
        return False

    def clear_session_events(self, session_id: str):
        """清理指定 session 的已推送事件记录（含所有客户端，新任务 event_id 从 1 重启）"""
        prefix = f"{session_id}:"
        for client in self._clients.values():
            for key in [k for k in client.published_ids if k.startswith(prefix)]:
                client.published_ids.discard(key)

    async def send_to_session(self, session_id: str, message: WSMessage):
        """发送到特定 session 的所有客户端"""
        if session_id not in self._session_clients:
            logger.debug(f"[WSManager] session {session_id} 无关联客户端")
            return

        disconnected = []
        for client_id in list(self._session_clients[session_id]):
            client = self._clients.get(client_id)
            if client:
                if client.replaying:
                    client.replay_backlog.append(message)
                    continue
                if self._client_seen(client, message.data):
                    continue
                if not await self._safe_send(client, message):
                    disconnected.append(client_id)

        for client_id in disconnected:
            await self.remove_client(client_id)

        logger.debug(f"[WSManager] session 推送完成: {session_id}, {message.type}")

    async def begin_replay(self, client_id: str) -> None:
        client = self._clients.get(client_id)
        if client is None:
            return
        client.replaying = True
        client.replay_backlog = []

    async def finish_replay(self, client_id: str) -> None:
        """回放结束后按 event_id 补发期间收到的实时事件"""
        client = self._clients.get(client_id)
        if client is None:
            return
        backlog = client.replay_backlog
        client.replay_backlog = []
        client.replaying = False
        if not backlog:
            return
        backlog.sort(key=lambda m: (m.data or {}).get("event_id") or 0)
        for message in backlog:
            if self._client_seen(client, message.data):
                continue
            if not await self._safe_send(client, message):
                return

    async def send_to_client(self, client_id: str, message: WSMessage):
        """发送到特定客户端"""
        client = self._clients.get(client_id)
        if not client:
            logger.warning(f"[WSManager] 客户端不存在: {client_id}")
            return

        if self._client_seen(client, message.data):
            return

        if not await self._safe_send(client, message):
            await self.remove_client(client_id)

    def get_client_count(self) -> int:
        """获取客户端数量"""
        return len(self._clients)

    def get_session_count(self) -> int:
        """获取关联的 session 数量"""
        return len(self._session_clients)


def ws_publish_event(event_type: str, data: Dict[str, Any], session_id: Optional[str] = None):
    """
    发布事件（同步接口，供外部调用）

    支持从任意线程调用：
    - 主线程：使用 asyncio.create_task
    - 线程池：使用 run_coroutine_threadsafe 调度到主循环
    """
    message = WSMessage(type=event_type, data=data, session_id=session_id)

    async def _do_publish():
        manager = WSConnectionManager.get_instance_sync()

        if session_id:
            manager.enqueue_to_session(session_id, message)
        else:
            await manager.broadcast(message)

    loop = WSConnectionManager.get_main_loop()
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                pass

    if loop is None or not loop.is_running():
        logger.debug("[WSManager] 无可用事件循环，跳过事件推送")
        return

    current = None
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None

    if current is loop:
        try:
            asyncio.create_task(_do_publish())
        except RuntimeError:
            pass
    else:
        try:
            asyncio.run_coroutine_threadsafe(_do_publish(), loop)
        except Exception as e:
            logger.debug("[WSManager] 跨线程调度失败: %s", e)