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
    _lock: Optional[asyncio.Lock] = None  # 延迟创建，避免无事件循环时初始化

    def __init__(self):
        self._clients: Dict[str, WSClient] = {}
        self._session_clients: Dict[str, Set[str]] = {}  # session_id -> set of clientids
        self._client_counter = 0
        self._cleanup_task: Optional[asyncio.Task] = None
        self._published_event_ids: set = set()
        self._max_published_ids: int = 10000

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """延迟创建锁，确保在事件循环内初始化"""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def get_instance(cls) -> "WSConnectionManager":
        """获取单例（异步）"""
        if cls._instance is None:
            async with cls._get_lock():
                if cls._instance is None:
                    cls._instance = cls()
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

    async def broadcast(self, message: WSMessage):
        """广播到所有客户端"""
        if self._is_duplicate_event(message.data):
            logger.debug(f"[WSManager] 事件已推送过，跳过广播 | event_id={message.data.get('event_id')}")
            return

        if not self._clients:
            logger.debug("[WSManager] 无客户端，跳过广播")
            return

        disconnected = []
        for client_id, client in self._clients.items():
            try:
                await client.send(message)
            except Exception as e:
                logger.warning(f"[WSManager] 广播到 {client_id} 失败: {e}")
                disconnected.append(client_id)

        for client_id in disconnected:
            await self.remove_client(client_id)

        logger.debug(f"[WSManager] 广播完成: {message.type}, 客户端数: {len(self._clients)}")

    def _is_duplicate_event(self, event_data: Dict[str, Any]) -> bool:
        """检查事件是否已经推送过（去重）"""
        event_id = event_data.get("event_id")
        if event_id is None:
            return False

        session_id = event_data.get("session_id", "global")
        event_key = f"{session_id}:{event_id}"
        if event_key in self._published_event_ids:
            return True

        self._published_event_ids.add(event_key)
        if len(self._published_event_ids) > self._max_published_ids:
            self._published_event_ids = set(list(self._published_event_ids)[-self._max_published_ids:])
        return False

    def clear_session_events(self, session_id: str):
        """清理指定 session 的已推送事件记录"""
        prefix = f"{session_id}:"
        to_remove = [key for key in self._published_event_ids if key.startswith(prefix)]
        for key in to_remove:
            self._published_event_ids.discard(key)

    async def send_to_session(self, session_id: str, message: WSMessage):
        """发送到特定 session 的所有客户端"""
        if self._is_duplicate_event(message.data):
            logger.debug(f"[WSManager] 事件已推送过，跳过 | session={session_id} | event_id={message.data.get('event_id')}")
            return

        if session_id not in self._session_clients:
            logger.debug(f"[WSManager] session {session_id} 无关联客户端")
            return

        disconnected = []
        for client_id in list(self._session_clients[session_id]):
            client = self._clients.get(client_id)
            if client:
                try:
                    await client.send(message)
                except Exception as e:
                    logger.warning(f"[WSManager] 发送到 {client_id} 失败: {e}")
                    disconnected.append(client_id)

        for client_id in disconnected:
            await self.remove_client(client_id)

        logger.debug(f"[WSManager] session 推送完成: {session_id}, {message.type}")

    async def send_to_client(self, client_id: str, message: WSMessage):
        """发送到特定客户端"""
        client = self._clients.get(client_id)
        if not client:
            logger.warning(f"[WSManager] 客户端不存在: {client_id}")
            return

        try:
            await client.send(message)
        except Exception as e:
            logger.warning(f"[WSManager] 发送到 {client_id} 失败: {e}")
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

    注意：这是同步函数，会在新的 asyncio task 中执行发送
    """
    message = WSMessage(type=event_type, data=data, session_id=session_id)

    async def _do_publish():
        manager = WSConnectionManager.get_instance_sync()
        if session_id:
            await manager.send_to_session(session_id, message)
        else:
            await manager.broadcast(message)
        logger.debug("[WSManager] 推送事件 | type=%s | session=%s", event_type, session_id)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_do_publish())
        else:
            logger.warning("[WSManager] 事件循环未运行，无法推送事件")
    except Exception as e:
        logger.error(f"[WSManager] 发布事件失败: {e}")