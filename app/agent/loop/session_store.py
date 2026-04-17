# -*- coding: utf-8 -*-
"""
会话持久化存储 - 在后端存储 session_id 与会话元数据
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class SessionMeta:
    """会话元数据"""
    session_id: str
    created_at: str
    last_active: str
    message_count: int
    title: Optional[str] = None  # 会话标题（可选）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SessionStore:
    """
    会话存储 - 后端持久化

    存储结构：
      memory/sessions.json - 所有会话的元数据
      {
        "sessions": [
          {"session_id": "xxx", "created_at": "...", "last_active": "...", "message_count": 5, "title": "..."},
          ...
        ],
        "last_active_session": "xxx"  // 最后活跃的 session_id
      }
    """

    _instance: "SessionStore" = None
    _lock = threading.Lock()

    def __init__(self, store_path: str = None, archive_dir: str = None):
        if store_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            store_path = os.path.join(base_dir, "memory", "sessions.json")

        self.store_path = store_path
        if archive_dir is None:
            self.archive_dir = os.path.join(os.path.dirname(store_path), "archive")
        else:
            self.archive_dir = archive_dir
        self._ensure_store()

    @classmethod
    def get_instance(cls) -> "SessionStore":
        """获取单例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_store(self):
        """确保存储文件存在，并从 archive 导入已有会话"""
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)

        store_exists = os.path.exists(self.store_path)
        if not store_exists:
            self._write_store({"sessions": [], "last_active_session": None})

        self._import_sessions_from_archive()

    def _import_sessions_from_archive(self):
        """
        从 archive 目录中扫描已有的 session_id 并导入到 sessions.json

        这确保即使 sessions.json 是新建的，也能恢复历史会话。
        只在 sessions.json 中没有会话时才导入（避免重复）。
        """
        if not os.path.exists(self.archive_dir):
            return

        store = self._read_store()
        existing_ids = {s["session_id"] for s in store.get("sessions", [])}

        if existing_ids:
            return

        discovered: Dict[str, Dict] = {}  # session_id -> {last_active, count}

        for fname in os.listdir(self.archive_dir):
            if not fname.endswith(".jsonl"):
                continue

            fpath = os.path.join(self.archive_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                            sid = rec.get("session_id")
                            if not sid or sid in existing_ids:
                                continue

                            if sid not in discovered:
                                discovered[sid] = {
                                    "last_active": rec.get("time", ""),
                                    "count": 0,
                                    "first_seen": rec.get("time", ""),
                                }

                            discovered[sid]["count"] += 1

                            # 更新最后活跃时间
                            if rec.get("time", "") > discovered[sid]["last_active"]:
                                discovered[sid]["last_active"] = rec.get("time", "")

                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning("[SessionStore] 扫描 archive 失败: %s | %s", fname, e)
                continue

        if discovered:
            sessions = store.get("sessions", [])
            for sid, info in discovered.items():
                sessions.append({
                    "session_id": sid,
                    "created_at": info.get("first_seen", ""),
                    "last_active": info.get("last_active", ""),
                    "message_count": info.get("count", 0),
                    "title": None,
                })

            store["sessions"] = sessions

            if discovered and not store.get("last_active_session"):
                latest = max(discovered.items(), key=lambda x: x[1].get("last_active", ""))
                store["last_active_session"] = latest[0]

            self._write_store(store)
            logger.info("[SessionStore] 从 archive 导入 %d 个会话", len(discovered))

    def _read_store(self) -> Dict:
        """读取存储"""
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"sessions": [], "last_active_session": None}

    def _write_store(self, data: Dict):
        """写入存储"""
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_or_create_session(self, session_id: str = None) -> SessionMeta:
        """
        获取或创建会话

        Args:
            session_id: 会话 ID，不传则创建新会话

        Returns:
            SessionMeta
        """
        with self._lock:
            store = self._read_store()
            sessions = store.get("sessions", [])
            sessions_dict = {s["session_id"]: s for s in sessions}

            if session_id and session_id in sessions_dict:
                # 已存在，更新活跃时间
                meta = sessions_dict[session_id]
                meta["last_active"] = datetime.now().isoformat()
                store["last_active_session"] = session_id
                self._write_store(store)
                self._publish_event("session_updated", meta)
                return SessionMeta(**meta)

            # 创建新会话
            if session_id is None:
                import uuid
                session_id = f"sess_{uuid.uuid4().hex[:12]}"
                title = None
            elif session_id.startswith("qq:"):
                title = f"QQ-{session_id.split(':')[1][:8]}"
            elif session_id.startswith("telegram:"):
                title = f"TG-{session_id.split(':')[1][:8]}"
            else:
                title = None

            now = datetime.now().isoformat()
            meta = SessionMeta(
                session_id=session_id,
                created_at=now,
                last_active=now,
                message_count=0,
                title=title,
            )

            sessions.append(meta.to_dict())
            store["sessions"] = sessions
            store["last_active_session"] = session_id
            self._write_store(store)

            self._publish_event("session_created", meta.to_dict())
            logger.info("[SessionStore] 创建新会话: %s", session_id)
            return meta

    def _publish_event(self, event_type: str, data: Dict):
        """发布会话事件到 SSE 订阅者"""
        try:
            from app.server.routes.session_events import publish_session_event
            publish_session_event(event_type, data)
            logger.debug(f"[SessionStore] 事件已发布: {event_type}")
        except Exception as e:
            logger.warning(f"[SessionStore] 事件发布失败: {e}")

    def update_message_count(self, session_id: str, delta: int = 1):
        """更新消息计数"""
        with self._lock:
            store = self._read_store()
            sessions = store.get("sessions", [])

            for s in sessions:
                if s["session_id"] == session_id:
                    s["message_count"] = s.get("message_count", 0) + delta
                    s["last_active"] = datetime.now().isoformat()
                    self._publish_event("session_updated", s)
                    break

            store["last_active_session"] = session_id
            self._write_store(store)

    def set_session_title(self, session_id: str, title: str):
        """设置会话标题"""
        with self._lock:
            store = self._read_store()
            sessions = store.get("sessions", [])

            for s in sessions:
                if s["session_id"] == session_id:
                    s["title"] = title
                    self._publish_event("session_updated", s)
                    break

            self._write_store(store)

    def get_last_active_session(self) -> Optional[str]:
        """获取最后活跃的 session_id"""
        store = self._read_store()
        last = store.get("last_active_session")

        if last:
            sessions = {s["session_id"] for s in store.get("sessions", [])}
            if last in sessions:
                return last

        sessions = store.get("sessions", [])
        if sessions:
            return sessions[-1]["session_id"]

        return None

    def list_sessions(self, limit: int = 50) -> List[SessionMeta]:
        """列出所有会话（按活跃时间倒序）"""
        store = self._read_store()
        sessions = store.get("sessions", [])

        sessions.sort(key=lambda x: x.get("last_active", ""), reverse=True)

        return [SessionMeta(**s) for s in sessions[:limit]]

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        with self._lock:
            store = self._read_store()
            sessions = store.get("sessions", [])

            new_sessions = [s for s in sessions if s["session_id"] != session_id]

            if len(new_sessions) == len(sessions):
                return False

            store["sessions"] = new_sessions

            if store.get("last_active_session") == session_id:
                store["last_active_session"] = new_sessions[-1]["session_id"] if new_sessions else None

            self._write_store(store)
            self._publish_event("session_deleted", {"session_id": session_id})
            return True

    def session_exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        store = self._read_store()
        sessions = {s["session_id"] for s in store.get("sessions", [])}
        return session_id in sessions


def get_session_store() -> SessionStore:
    """获取会话存储单例"""
    return SessionStore.get_instance()
