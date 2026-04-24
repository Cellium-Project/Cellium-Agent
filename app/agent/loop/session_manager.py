# -*- coding: utf-8 -*-
"""
会话管理器 — 按 session_id 维护多轮对话的短期记忆
"""

import json
import logging
import time
import threading
from typing import Dict, Optional, List, Any, Tuple
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
        flash_mode: bool = False,
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
        self.flash_mode = flash_mode

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
            "flash_mode": self.flash_mode,
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

    def get_or_create(self, session_id: str, flash_mode: bool = None) -> SessionInfo:
        """获取或创建会话（冷启动时自动从归档恢复历史消息）"""
        evicted_info = None  # 用于保存被淘汰的会话信息
        info = None

        if flash_mode is None:
            try:
                from app.core.util.agent_config import get_config
                _cfg = get_config()
                flash_mode = _cfg.get("flash_mode", False)
            except Exception:
                flash_mode = False

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
            else:
                if len(self._sessions) >= self._max_sessions:
                    evicted_info = self._evict_oldest()  

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
                    flash_mode=flash_mode,
                )

                # 冷启动：从归档恢复历史对话到 MemoryManager
                self._restore_from_archive(session_id, info.memory)

                self._sessions[session_id] = info

        if evicted_info:
            self._persist_session_snapshot(evicted_info[0], evicted_info[1])

        return info

    def get(self, session_id: str) -> Optional[SessionInfo]:
        """获取已有会话（不存在返回 None）"""
        with self._lock:
            info = self._sessions.get(session_id)
            if info:
                info.touch()
            return info

    def get_memory(self, session_id: str) -> MemoryManager:
        return self.get_or_create(session_id).memory

    def list_sessions(self, active_only: bool = True) -> List[Dict[str, Any]]:
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
            messages = info.memory.messages
            if len(messages) < 1:
                return

            last_user_idx = -1
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx == -1:
                return

            round_messages = messages[last_user_idx:]
            if not round_messages:
                return

            has_assistant = any(m.get("role") == "assistant" for m in round_messages)
            if not has_assistant:
                if info.flash_mode:
                    logger.debug("[SessionManager] Flash模式会话，跳过快照保存 | session=%s", session_id)
                else:
                    logger.debug("[SessionManager] 无 assistant 消息，跳过快照保存 | session=%s", session_id)
                return

            user_msg = round_messages[0]
            assistant_msg = None
            for m in round_messages:
                if m.get("role") == "assistant":
                    assistant_msg = m
                    break

            self.three_layer_memory.persist_session(
                user_input=user_msg.get("content", ""),
                response=assistant_msg.get("content", "") if assistant_msg else "",
                session_id=session_id,
                messages=round_messages, 
            )
        except Exception as e:
            logger.warning("[SessionManager] 会话持久化失败 | session=%s | error=%s", session_id, e)


    def _restore_from_archive(self, session_id: str,
                               memory: 'MemoryManager') -> int:
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

        for rec in records:
            msgs = rec.get("messages")
            if isinstance(msgs, list):
                for msg in msgs:
                    try:
                        role = msg.get("role", "")
                        content = msg.get("content")
                        tool_calls = msg.get("tool_calls")

                        if role == "user":
                            memory.add_user_message(content or "")
                            restored_count += 1
                        elif role == "assistant":
                            reasoning_content = msg.get("reasoning_content")
                            if tool_calls:
                                tool_calls_data = []
                                for tc in tool_calls:
                                    original_tc_id = tc.get("id", "")
                                    tool_calls_data.append({
                                        "tool_name": tc.get("function", {}).get("name", ""),
                                        "arguments": json.loads(tc.get("function", {}).get("arguments", "{}")),
                                        "tool_call_id": original_tc_id,
                                    })
                                memory.add_tool_calls_batch(
                                    tool_calls_data,
                                    content=content or None,
                                    reasoning_content=reasoning_content,
                                )
                                restored_count += 1
                            elif content:
                                memory.add_assistant_message(
                                    content,
                                    reasoning_content=reasoning_content,
                                )
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
                    except (KeyError, TypeError, json.JSONDecodeError):
                        pass
            else:
                user_text = rec.get("user", "")
                asst_text = rec.get("assistant", "")
                if user_text:
                    memory.add_user_message(user_text)
                    restored_count += 1
                if asst_text:
                    memory.add_assistant_message(asst_text)
                    restored_count += 1

        if restored_count > 0:
            logger.info(
                "[SessionManager] 归档恢复 | session=%s | %d 条消息",
                session_id, restored_count,
            )
            self._cleanup_incomplete_tool_calls(memory)

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

    def _cleanup_incomplete_tool_calls(self, memory) -> None:
        messages = memory.messages
        tool_call_ids = set()
        tool_result_ids = set()

        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    tool_call_ids.add(tc.get("id"))
            elif msg.get("role") == "tool":
                tool_result_ids.add(msg.get("tool_call_id"))

        incomplete_ids = tool_call_ids - tool_result_ids
        if not incomplete_ids:
            return

        logger.info("[SessionManager] 清理 %d 个不完整 tool_call", len(incomplete_ids))
        cleaned = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                remaining_tcs = [tc for tc in msg.get("tool_calls", []) if tc.get("id") not in incomplete_ids]
                if remaining_tcs:
                    msg_copy = dict(msg)
                    msg_copy["tool_calls"] = remaining_tcs
                    cleaned.append(msg_copy)
                elif msg.get("content"):
                    msg_copy = dict(msg)
                    msg_copy.pop("tool_calls", None)
                    cleaned.append(msg_copy)
            else:
                cleaned.append(msg)

        memory.messages = cleaned

    def _evict_oldest(self) -> Optional[Tuple[str, SessionInfo]]:
        """淘汰最老的不活跃会话（仅从内存移除，返回被移除的会话信息供锁外持久化）"""
        if not self._sessions:
            return None
        oldest_sid = min(
            self._sessions.keys(),
            key=lambda sid: self._sessions[sid].last_active,
        )
        info = self._sessions.pop(oldest_sid)
        return (oldest_sid, info)


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


_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取全局会话管理器单例"""
    global _manager
    if _manager is None:
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
