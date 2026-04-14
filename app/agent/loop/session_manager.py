# -*- coding: utf-8 -*-
"""
会话管理器 — 按 session_id 维护多轮对话的短期记忆
"""

import json
import logging
import time
import threading
from typing import Dict, Optional, List, Any
from datetime import datetime

from app.agent.loop.memory import MemoryManager

logger = logging.getLogger(__name__)


class SessionInfo:
    """单个会话的信息"""

    def __init__(
        self,
        session_id: str,
        max_history: int = 200,
        max_tool_results: int = 10,
        max_tool_result_length: int = 500,
        auto_compact_threshold: int = 10000,
    ):
        self.session_id = session_id
        self.memory = MemoryManager(
            max_history=max_history,
            max_tool_results=max_tool_results,
            max_tool_result_length=max_tool_result_length,
            auto_compact_threshold=auto_compact_threshold,
        )
        self.created_at = time.time()
        self.last_active = time.time()
        self.message_count = 0

    def touch(self):
        """更新最后活跃时间"""
        self.last_active = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "message_count": self.message_count,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "last_active": datetime.fromtimestamp(self.last_active).isoformat(),
            "age_seconds": int(time.time() - self.created_at),
            "idle_seconds": int(time.time() - self.last_active),
        }


class SessionManager:
    """会话管理器 — 单例"""

    # 默认会话超时（秒）：24小时
    DEFAULT_SESSION_TIMEOUT = 86400
    MAX_SESSIONS = 100

    def __init__(
        self,
        timeout: int = None,
        max_sessions: int = None,
        three_layer_memory=None,
    ):
        self._sessions: Dict[str, SessionInfo] = {}
        self._lock = threading.Lock()
        self._timeout = timeout or self.DEFAULT_SESSION_TIMEOUT
        self._max_sessions = max_sessions or self.MAX_SESSIONS
        self.three_layer_memory = three_layer_memory  # 可选，用于对话结束时持久化

    def get_or_create(self, session_id: str) -> SessionInfo:
        """获取或创建会话（冷启动时自动从归档恢复历史消息）"""
        with self._lock:
            if session_id in self._sessions:
                info = self._sessions[session_id]
                if not info.memory.get_messages():
                    logger.info(
                        "[SessionManager] 内存为空，尝试归档恢复 | session=%s",
                        session_id,
                    )
                    self._restore_from_archive(session_id, info.memory)
                info.touch()
                return info

            if len(self._sessions) >= self._max_sessions:
                self._evict_oldest()

            from app.core.util.agent_config import get_config
            _cfg = get_config()
            memory_cfg = _cfg.get_section("memory") or {}
            short_term = memory_cfg.get("short_term", {})

            info = SessionInfo(
                session_id,
                max_history=short_term.get("max_history", 200),
                max_tool_results=short_term.get("max_tool_results", 10),
                max_tool_result_length=short_term.get("max_tool_result_length", 500),
                auto_compact_threshold=short_term.get("auto_compact_threshold", 10000),
            )

            # 冷启动：从归档恢复历史对话到 MemoryManager
            self._restore_from_archive(session_id, info.memory)

            self._sessions[session_id] = info
            return info

    def get(self, session_id: str) -> Optional[SessionInfo]:
        """获取已有会话（不存在返回 None）"""
        with self._lock:
            info = self._sessions.get(session_id)
            if info:
                info.touch()
            return info

    def get_memory(self, session_id: str) -> MemoryManager:
        """获取指定 session 的 MemoryManager（自动创建）"""
        return self.get_or_create(session_id).memory

    def list_sessions(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """列出所有会话信息"""
        with self._lock:
            result = []
            for info in self._sessions.values():
                if active_only:
                    idle = time.time() - info.last_active
                    if idle > self._timeout:
                        continue
                result.append(info.to_dict())
            result.sort(key=lambda x: x["last_active"], reverse=True)
            return result

    def close_session(self, session_id: str) -> bool:
        """关闭并清理指定会话"""
        with self._lock:
            info = self._sessions.pop(session_id, None)
        if info:
            self._persist_session_snapshot(session_id, info)
        return info is not None

    def _persist_session_snapshot(self, session_id: str, info: SessionInfo) -> None:
        if not info or not self.three_layer_memory:
            return

        try:
            messages = info.memory.get_messages()
            if len(messages) < 2:
                return

            user_messages = [m.get("content", "") for m in messages if m.get("role") == "user" and m.get("content")]
            assistant_messages = [m.get("content", "") for m in messages if m.get("role") == "assistant" and m.get("content")]
            if not user_messages or not assistant_messages:
                return

            self.three_layer_memory.persist_session(
                user_input=user_messages[-1],
                response=assistant_messages[-1],
                session_id=session_id,
                messages=messages,
            )
        except Exception as e:
            logger.warning("[SessionManager] 会话持久化失败 | session=%s | error=%s", session_id, e)


    def _restore_from_archive(self, session_id: str,
                               memory: 'MemoryManager') -> int:
        """
        从归档 JSONL 恢复指定 session 的历史对话到 MemoryManager

        归档数据结构：
          - 每条记录 = 一轮对话结束时的 MemoryManager 完整快照（含所有历史）
          - 新格式有 messages 字段（完整消息链，含 tool_call/tool_result）
          - 旧格式只有 user+assistant 字段（纯文本）

        混合恢复策略：
          Step 1: 最新完整快照 → 还原最新对话（含 tool_call）
          Step 2: 快照之前的旧格式记录 → 纯文本补全早期历史
        """
        if self.three_layer_memory is None:
            return 0

        try:
            records = self.three_layer_memory.archive.get_by_session(
                session_id, limit=200
            )
        except Exception as e:
            logger.warning("[SessionManager] 获取归档记录失败: %s", e)
            return 0

        if not records:
            return 0

        restored_count = 0

        # ── Step 1: 找最新完整快照 ──
        latest_snapshot = None
        snapshot_index = -1
        for i in range(len(records) - 1, -1, -1):
            msgs = records[i].get("messages")
            if isinstance(msgs, list) and len(msgs) >= 2:
                latest_snapshot = msgs
                snapshot_index = i
                break

        if latest_snapshot:
            for msg in latest_snapshot:
                try:
                    role = msg.get("role", "")
                    content = msg.get("content")
                    tool_calls = msg.get("tool_calls")

                    if role == "user":
                        memory.add_user_message(content or "")
                        restored_count += 1
                    elif role == "assistant":
                        if tool_calls:
                            for tc in tool_calls:
                                original_tc_id = tc.get("id", "")
                                memory.add_tool_call(
                                    tc.get("function", {}).get("name", ""),
                                    json.loads(tc.get("function", {}).get("arguments", "{}")),
                                    tool_call_id=original_tc_id,  #保留原始 ID，与 tool_result 匹配
                                )
                                restored_count += 1
                        elif content:
                            memory.add_assistant_message(content)
                            restored_count += 1
                    elif role == "tool":
                        tc_id = msg.get("tool_call_id", "")
                        result_raw = msg.get("content", "{}")
                        try:
                            parsed = json.loads(result_raw)
                            memory.add_tool_result(tc_id, parsed)
                            restored_count += 1
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            logger.warning(
                                "[SessionManager] 工具结果 JSON 解析失败，跳过 | tc_id=%s | error=%s",
                                tc_id, e,
                            )
                        except Exception as e:
                            logger.warning(
                                "[SessionManager] 工具结果恢复失败，跳过 | tc_id=%s | error=%s",
                                tc_id, e,
                            )
                except (KeyError, TypeError, json.JSONDecodeError):
                    pass

            logger.info(
                "[SessionManager] 快照恢复 | session=%s | %d 条",
                session_id, restored_count,
            )

            # ── Step 2: 补充快照之前的纯旧格式记录（不含 messages 字段的才补）──
            if snapshot_index > 0 and restored_count < memory.max_history:
                supplemental = 0
                for rec in records[:snapshot_index]:
                    #跳过已有 messages 字段的新格式记录（数据已包含在快照中）
                    if rec.get("messages"):
                        continue
                    user_text = rec.get("user", "")
                    asst_text = rec.get("assistant", "")
                    if user_text:
                        memory.add_user_message(user_text)
                        restored_count += 1
                        supplemental += 1
                    if asst_text:
                        memory.add_assistant_message(asst_text)
                        restored_count += 1
                        supplemental += 1
                if supplemental > 0:
                    logger.info(
                        "[SessionManager] 补充旧格式 | +%d 条 | 总=%d",
                        supplemental, restored_count,
                    )

            return restored_count

        # ── 无快照：全量降级 ──
        for rec in records:
            try:
                user_text = rec.get("user", "")
                asst_text = rec.get("assistant", "")
                if user_text:
                    memory.add_user_message(user_text)
                    restored_count += 1
                if asst_text:
                    memory.add_assistant_message(asst_text)
                    restored_count += 1
            except (KeyError, TypeError):
                continue

        if restored_count > 0:
            logger.info(
                "[SessionManager] 纯文本降级 | session=%s | %d 轮",
                session_id, restored_count // 2,
            )

    def cleanup_expired(self) -> int:
        """清理所有超时会话，返回清理数量"""
        now = time.time()
        expired_infos = []
        with self._lock:
            for sid, info in list(self._sessions.items()):
                if now - info.last_active > self._timeout:
                    expired_infos.append((sid, info))
                    del self._sessions[sid]

        for sid, info in expired_infos:
            self._persist_session_snapshot(sid, info)
        return len(expired_infos)

    def _evict_oldest(self):
        """淘汰最老的不活跃会话"""
        oldest_sid = min(
            self._sessions.keys(),
            key=lambda sid: self._sessions[sid].last_active,
        )
        info = self._sessions.pop(oldest_sid)
        self._persist_session_snapshot(oldest_sid, info)


    @property
    def total_sessions(self) -> int:
        with self._lock:
            return len(self._sessions)

    def clear_all(self):
        """清理所有会话"""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            return count

    def update_all_memory_configs(self, short_term_cfg: dict):
        """
        更新所有活跃会话的 MemoryManager 配置（热重载支持）

        Args:
            short_term_cfg: memory.yaml 中的 short_term 配置
        """
        with self._lock:
            updated_count = 0
            for info in self._sessions.values():
                info.memory.update_config(
                    max_history=short_term_cfg.get("max_history"),
                    max_tool_results=short_term_cfg.get("max_tool_results"),
                    max_tool_result_length=short_term_cfg.get("max_tool_result_length"),
                    auto_compact_threshold=short_term_cfg.get("auto_compact_threshold"),
                )
                updated_count += 1
        logger.info("[SessionManager] Memory 配置热重载 | 已更新 %d 个会话", updated_count)


# ── 全局单例 ──────────────────────────────────────────────
_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取全局会话管理器单例"""
    global _manager
    if _manager is None:
        # 尝试从 DI 容器获取 three_layer_memory
        tlm = None
        try:
            from app.agent.memory.three_layer import ThreeLayerMemory
            from app.core.di.container import get_container
            container = get_container()
            if container.has(ThreeLayerMemory):
                tlm = container.resolve(ThreeLayerMemory)
                logger.info("[SessionManager] 从 DI 容器获取 ThreeLayerMemory")
        except Exception as e:
            logger.debug("[SessionManager] DI 容器未就绪: %s", e)
        _manager = SessionManager(three_layer_memory=tlm)
    elif _manager.three_layer_memory is None:
        # 如果已有实例但没有 three_layer_memory，尝试补充
        try:
            from app.agent.memory.three_layer import ThreeLayerMemory
            from app.core.di.container import get_container
            container = get_container()
            if container.has(ThreeLayerMemory):
                _manager.three_layer_memory = container.resolve(ThreeLayerMemory)
                logger.info("[SessionManager] 补充 ThreeLayerMemory 到现有实例")
        except Exception as e:
            logger.debug("[SessionManager] 补充 ThreeLayerMemory 失败: %s", e)
    return _manager


def init_session_manager(
    timeout: int = None,
    max_sessions: int = None,
    three_layer_memory=None,
) -> SessionManager:
    """初始化全局会话管理器（main.py 启动时调用）"""
    global _manager
    _manager = SessionManager(
        timeout=timeout,
        max_sessions=max_sessions,
        three_layer_memory=three_layer_memory,
    )
    logger.info("[SessionManager] 初始化完成 | timeout=%s | three_layer_memory=%s",
                timeout, three_layer_memory is not None)
    return _manager
