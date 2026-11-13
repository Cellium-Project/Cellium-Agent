# -*- coding: utf-8 -*-
"""
会话委托组件 — 主会话把任务异步委托给另一个真实持久会话

用法（调用 session_agent 工具）：
  session_agent.send(target, message, purpose, create, notify, timeout)  # 立即返回 task_id
  session_agent.status(target) / results(target) / cancel(target) / list()
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from typing import Any, Dict, List

from app.core.interface.base_cell import BaseCell
from app.core.util import delegation_state

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 900
_MAX_TIMEOUT = 3600
_MAX_ACTIVE_PER_PARENT = 4
_MAX_RESULT_CHARS = 4000
_KEEP_FINISHED = 20
_FINISHED_STATUSES = ("done", "error", "timeout", "cancelled")
_NOTIFY_RETRY_WINDOW = 300.0
_NOTIFY_RETRY_INTERVAL = 1.0

# 热重载会重建组件模块，状态放在稳定模块里，避免进行中的委托丢失
_DELEGATIONS = delegation_state.DELEGATIONS
_DELEGATED_ACTIVE = delegation_state.DELEGATED_ACTIVE
_PENDING_NOTIFY = delegation_state.PENDING_NOTIFY
_NOTIFY_WORKERS = delegation_state.NOTIFY_WORKERS
_ACTIVE_LOCK = delegation_state.LOCK

_FILES_NOTE = "撤回不级联：目标会话的对话与文件改动会保留；如需回滚文件，请到目标会话用 turn_id 撤回。"

_NET_ERROR_HINTS = {
    "ReadError": "网络读取中断（LLM 流连接被重置），可稍后重试",
    "ReadTimeout": "读取超时，可稍后重试",
    "ConnectError": "无法连接模型服务，请检查网络或 Base URL",
    "ConnectTimeout": "连接模型服务超时",
    "RemoteProtocolError": "模型服务连接被对端关闭，可稍后重试",
}

_STOP_REASON_TEXT = {
    "empty_response": "回复为空（模型中断或提前结束）",
    "max_iterations_exceeded": "达到最大迭代次数",
    "loop_detected": "检测到重复输出",
    "user_cancelled": "用户取消",
    "control_loop_stop": "控制策略终止",
    "heuristic_stop": "启发式终止",
    "timeout": "超时",
}


def _clip_result(text: str) -> tuple:
    """委托结论限长，避免回传时撑大父会话上下文"""
    text = (text or "").strip()
    if len(text) <= _MAX_RESULT_CHARS:
        return text, False
    return text[:_MAX_RESULT_CHARS] + "\n...[截断]", True


def _describe_error(e: Exception) -> str:
    """异常转可读文案：底层异常常为空 message（如 httpx.ReadError）"""
    name = type(e).__name__
    detail = str(e).strip()
    hint = _NET_ERROR_HINTS.get(name)
    if hint:
        return f"{hint}（{name}）"
    if not detail:
        detail = repr(e) or "无详情"
    return f"{name}: {detail}"


def is_delegated(session_id: str) -> bool:
    with _ACTIVE_LOCK:
        return session_id in _DELEGATED_ACTIVE


def _scope(parent: str = None) -> str:
    return f"{parent or 'default'}::"


def _elapsed_seconds(info: Dict[str, Any]) -> float:
    info = info or {}
    end = info.get("finished_at") or time.time()
    start = info.get("started_at") or end
    return round(max(0.0, end - start), 2)


def _clip_preview(value, limit: int = 200) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def build_delegation_notice(info: Dict[str, Any]) -> Dict[str, Any]:
    info = info or {}
    status = info.get("status") or "error"
    ok = status == "done"
    content, _ = _clip_result(info.get("result_content") or "")
    turn_id = str(info.get("turn_id") or "")
    return {
        "type": "delegation",
        "title": info.get("target_title") or info.get("target") or "",
        "status": "成功" if ok else "失败",
        "elapsed": f"{_elapsed_seconds(info):g}s",
        "body": content,
        "error": str(info.get("error") or ""),
        "footer": f"（如需回滚文件，请到该会话用 turn_id={turn_id} 撤回）" if turn_id else "",
        "turn_id": turn_id,
        "task_id": str(info.get("task_id") or ""),
    }


def _get_main_loop():
    """获取主事件循环"""
    from app.agent.di_config import _main_loop
    if _main_loop is not None:
        return _main_loop
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _log_failure(future) -> None:
    try:
        future.result()
    except BaseException as e:
        logger.warning("[SessionAgent] 后台调度失败 | error=%s", e)


def _resolve_delegation_mode(target: str) -> str:
    """被委托会话用自身模式执行，不继承委托方的编排/计划模式"""
    from app.server.session_mode import get_mode
    return get_mode(target)


class SessionAgent(BaseCell):
    """会话委托 — 把任务交给另一个真实会话异步执行"""

    @property
    def cell_name(self) -> str:
        return "session_agent"

    def on_load(self):
        super().on_load()
        self._resume_pending_notify()

    @staticmethod
    def _fail(error: str, reason: str) -> Dict[str, Any]:
        return {"success": False, "error": error, "reason": reason}

    @staticmethod
    def _clean_title(value: str) -> str:
        """标题清洗：去控制字符、压空白（长度由调用方校验/截断）"""
        text = str(value or "")
        text = "".join(" " if ord(ch) < 32 else ch for ch in text)
        return " ".join(text.split()).strip()

    @staticmethod
    def _caller_block(session_id: str, action: str = "操作"):
        """调用方权限：被委托会话与外部渠道会话不能做跨会话操作"""
        if not session_id:
            return None
        if is_delegated(session_id):
            return SessionAgent._fail(
                f"被委托会话禁止{action}：只有主会话可以发起", "nested_delegation"
            )
        if str(session_id).startswith("subagent:"):
            return SessionAgent._fail(
                f"子 Agent 会话禁止{action}：只有主会话可以发起", "subagent_forbidden"
            )
        try:
            from app.server.session_mode import get_mode
            if get_mode(session_id) == "plan":
                return SessionAgent._fail(
                    f"Plan 模式禁止{action}：切到 Run 或委托模式后再用", "plan_mode_forbidden"
                )
        except Exception:
            pass
        try:
            from app.agent.loop.session_manager import get_session_manager
            info = get_session_manager().get(session_id)
            ctx = getattr(info, "platform_context", None) or {}
            if isinstance(ctx, dict) and ctx.get("platform"):
                return SessionAgent._fail(
                    f"外部渠道会话禁止{action}", "forbidden_scope"
                )
        except Exception:
            pass
        return None

    @staticmethod
    def _ambiguous(raw: str, metas: List[Any]) -> Dict[str, Any]:
        candidates = [
            {
                "session_id": m.session_id,
                "title": (getattr(m, "title", None) or "").strip() or None,
            }
            for m in metas[:5]
        ]
        names = "，".join(
            f"{c['title'] or '未命名'}({c['session_id']})" for c in candidates
        )
        return {
            "success": False,
            "error": f"会话名称「{raw}」匹配到 {len(metas)} 个会话，请改用 session_id: {names}",
            "reason": "ambiguous_target",
            "candidates": candidates,
        }

    @staticmethod
    def _resolve_target(raw: str):
        """target 解析：精确 ID → 精确名称 → 忽略大小写的包含匹配"""
        raw = (raw or "").strip()
        if not raw:
            return None, SessionAgent._fail(
                "target 必填：要委托的目标会话 ID 或会话名称", "target_required"
            )
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            if store.session_exists(raw):
                return raw, None
            metas = store.list_sessions(limit=200) or []
            exact = [
                m for m in metas
                if ((getattr(m, "title", None) or "").strip() == raw)
            ]
            if len(exact) == 1:
                return exact[0].session_id, None
            if len(exact) > 1:
                return None, SessionAgent._ambiguous(raw, exact)
            low = raw.lower()
            fuzzy = []
            for m in metas:
                t = (getattr(m, "title", None) or "").strip().lower()
                if t and (low in t or t in low):
                    fuzzy.append(m)
            if len(fuzzy) == 1:
                return fuzzy[0].session_id, None
            if len(fuzzy) > 1:
                return None, SessionAgent._ambiguous(raw, fuzzy)
            hint = "，".join(
                f"{(getattr(m, 'title', None) or '').strip() or '未命名'}({m.session_id})"
                for m in metas[:5]
            )
            error = f"目标会话不存在: {raw}（可传 create=true 新建）"
            if hint:
                error += f"；最近会话: {hint}"
            return None, SessionAgent._fail(error, "target_not_found")
        except Exception as e:
            return None, SessionAgent._fail(f"解析目标会话失败: {e}", "resolve_failed")

    @staticmethod
    def _lookup_title(target: str):
        try:
            from app.agent.loop.session_store import get_session_store
            for meta in get_session_store().list_sessions(limit=200) or []:
                if meta.session_id == target:
                    return (getattr(meta, "title", None) or "").strip() or None
        except Exception:
            pass
        return None

    @staticmethod
    def _active_count(parent: str = None) -> int:
        prefix = _scope(parent)
        with _ACTIVE_LOCK:
            return sum(
                1 for k, v in _DELEGATIONS.items()
                if k.startswith(prefix) and v.get("status") in ("queued", "running")
            )

    @staticmethod
    def _prune(parent: str = None, keep: int = _KEEP_FINISHED) -> None:
        prefix = _scope(parent)
        removed = False
        with _ACTIVE_LOCK:
            finished = [
                (k, v.get("finished_at") or v.get("started_at") or 0)
                for k, v in _DELEGATIONS.items()
                if k.startswith(prefix) and v.get("status") in _FINISHED_STATUSES
            ]
            for key, _ in sorted(finished, key=lambda x: x[1])[: max(0, len(finished) - keep)]:
                _DELEGATIONS.pop(key, None)
                removed = True
        if removed:
            delegation_state.save()

    @staticmethod
    def _records(parent: str = None) -> List[Dict[str, Any]]:
        prefix = _scope(parent)
        with _ACTIVE_LOCK:
            return [v for k, v in _DELEGATIONS.items() if k.startswith(prefix)]

    @staticmethod
    def _locate(parent: str = None, target: str = "", task_id: str = "") -> Dict[str, Any]:
        prefix = _scope(parent)
        with _ACTIVE_LOCK:
            if task_id:
                return _DELEGATIONS.get(f"{prefix}{task_id}")
            if target:
                candidates = [
                    v for k, v in _DELEGATIONS.items()
                    if k.startswith(prefix) and v.get("target") == target
                ]
                if not candidates:
                    return None
                return max(candidates, key=lambda v: v.get("started_at") or 0)
        return None

    @staticmethod
    def _summary(info: Dict[str, Any]) -> Dict[str, Any]:
        status = info.get("status")
        return {
            "task_id": info.get("task_id"),
            "target": info.get("target"),
            "purpose": info.get("purpose", ""),
            "status": status,
            "elapsed_s": _elapsed_seconds(info),
            "running": status in ("queued", "running", "cancelling"),
            "current_tool": info.get("current_tool"),
            "tool_calls": info.get("tool_calls", 0),
            "last_tool": info.get("last_tool"),
            "result_preview": (info.get("result_content") or "")[:300],
            "error": info.get("error"),
        }

    @staticmethod
    def _build_injection(parent: str = None, purpose: str = "") -> str:
        return (
            f"[会话委托] 你正在被会话「{parent or 'default'}」委托处理任务，你的最终回复会作为结果返回给它。\n"
            f"委托意图：{purpose or '未说明'}\n"
            "要求：直接执行并给出结论（发现 + 判断 + 关键文件:行），不寒暄、不询问称呼。\n"
            "限制：本次运行中禁止调用 session_agent 再向其他会话委托。"
        )

    # ================================================================
    # 命令
    # ================================================================

    def _cmd_send(
        self,
        target: str = "",
        message: str = "",
        purpose: str = "",
        create: bool = False,
        notify: bool = None,
        timeout: float = 900,
        title: str = "",
        session_id: str = None,
    ) -> Dict[str, Any]:
        """把任务异步委托给另一个会话（立即返回 task_id）

        Args:
            target: 目标会话 ID 或会话名称
            message: 交给目标会话执行的任务
            purpose: 委托意图（会注入目标会话系统提示）
            create: 目标会话不存在时是否新建
            notify: 完成后是否唤醒父会话；不传默认 true，显式 false 才静默
            timeout: 超时秒数，默认 900，上限 3600
            title: 新建目标会话时使用的名称
        """
        self._resume_pending_notify(session_id)
        target = (target or "").strip()
        if isinstance(create, str):
            create = create.strip().lower() in ("1", "true", "yes", "y")
        if not target:
            return self._fail(
                "target 必填：要委托的目标会话 ID 或名称；create=true 只表示目标不存在时新建，不能省略 target",
                "target_required",
            )
        if session_id and target == session_id:
            return self._fail("不能委托给当前会话自身（会死锁）", "self_target")
        if target.startswith("subagent:"):
            return self._fail("subagent 会话是临时子 Agent，不能作为持久委托目标", "subagent_target")
        blocked = self._caller_block(session_id, "再委托")
        if blocked:
            return blocked

        resolved, err = self._resolve_target(target)
        if err:
            # create=true 时把未命中的 raw 当作新会话 ID
            if err.get("reason") == "target_not_found" and create:
                resolved = target
            else:
                return err
        if session_id and resolved == session_id:
            return self._fail("不能委托给当前会话自身（会死锁）", "self_target")
        target = resolved

        from app.agent.loop.session_store import get_session_store
        store = get_session_store()
        if not store.session_exists(target):
            if not create:
                return self._fail(
                    f"目标会话不存在: {target}（可传 create=true 新建）", "target_not_found"
                )
            try:
                store.get_or_create_session(target)
            except Exception as e:
                return self._fail(f"创建目标会话失败: {e}", "target_create_failed")
            try:
                name = self._clean_title(title or purpose or f"委托-{message[:10]}")[:60]
                if name:
                    store.set_session_title(target, name)
            except Exception:
                pass

        if self._active_count(session_id) >= _MAX_ACTIVE_PER_PARENT:
            return self._fail(
                f"活跃委托上限 {_MAX_ACTIVE_PER_PARENT}，请等待完成或先 cancel",
                "too_many_active",
            )

        message = (message or "").strip()
        if not message:
            return self._fail("message 必填：委托给目标会话的任务内容", "message_required")

        loop = _get_main_loop()
        if loop is None or not loop.is_running():
            return self._fail("无可用主事件循环，无法后台运行委托", "no_event_loop")

        if isinstance(notify, str):
            notify = notify.strip().lower() in ("1", "true", "yes", "y")
        elif notify is None:
            # 默认推送回父会话，只有显式 notify=false 才静默
            notify = True
        notify = bool(notify)
        try:
            effective_timeout = float(timeout or _DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            effective_timeout = float(_DEFAULT_TIMEOUT)
        if effective_timeout <= 0:
            effective_timeout = float(_DEFAULT_TIMEOUT)
        effective_timeout = min(effective_timeout, float(_MAX_TIMEOUT))

        self._prune(session_id)

        task_id = f"dlg_{uuid.uuid4().hex[:8]}"
        key = f"{_scope(session_id)}{task_id}"
        now = time.time()
        with _ACTIVE_LOCK:
            _DELEGATIONS[key] = {
                "task_id": task_id,
                "parent": session_id,
                "target": target,
                "message": message,
                "purpose": purpose or "",
                "status": "queued",
                "started_at": now,
                "updated_at": now,
                "finished_at": None,
                "cancel_requested": False,
                "notify": bool(notify),
                "timeout": effective_timeout,
                "current_tool": None,
                "tool_calls": 0,
                "last_tool": None,
                "last_result_preview": None,
                "result_content": "",
                "error": None,
                "turn_id": None,
                "bg_task": None,
            }

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._run_delegation(
                    key, target, message, purpose or "", session_id, effective_timeout, bool(notify)
                ),
                loop,
            )
            future.add_done_callback(_log_failure)
        except Exception as e:
            with _ACTIVE_LOCK:
                _DELEGATIONS.pop(key, None)
            delegation_state.save()
            return self._fail(f"提交后台委托失败: {e}", "submit_failed")

        delegation_state.save()

        return {
            "success": True,
            "task_id": task_id,
            "target": target,
            "status": "queued",
            "hint": "用 status/results 查询进度，cancel 取消",
        }

    def _cmd_status(self, target: str = "", task_id: str = "", session_id: str = None) -> Dict[str, Any]:
        """查看会话委托状态（target 取该目标最新一条，留空列出全部）"""
        self._resume_pending_notify(session_id)
        if target and not task_id:
            resolved, err = self._resolve_target(target)
            if err:
                return err
            target = resolved
        info = self._locate(session_id, target, task_id)
        if info is not None:
            return {"success": True, **self._summary(info)}
        if target or task_id:
            return self._fail(f"未找到委托: {task_id or target}", "not_found")
        records = self._records(session_id)
        records.sort(key=lambda v: v.get("started_at") or 0, reverse=True)
        items = [self._summary(v) for v in records]
        return {"success": True, "count": len(items), "delegations": items}

    def _cmd_results(self, target: str = "", task_id: str = "", session_id: str = None) -> Dict[str, Any]:
        """读取会话委托的最终结论与目标会话 turn_id"""
        self._resume_pending_notify(session_id)
        if target and not task_id:
            resolved, err = self._resolve_target(target)
            if err:
                return err
            target = resolved
        info = self._locate(session_id, target, task_id)
        if info is None:
            return self._fail(f"未找到委托: {task_id or target}", "not_found")
        status = info.get("status")
        content, truncated = _clip_result(info.get("result_content") or "")
        return {
            "success": True,
            "status": status,
            "running": status in ("queued", "running", "cancelling"),
            "content": content,
            "truncated": truncated,
            "turn_id": info.get("turn_id"),
            "target": info.get("target"),
            "error": info.get("error"),
            "elapsed_s": _elapsed_seconds(info),
            "files_note": _FILES_NOTE,
        }

    def _cmd_cancel(self, target: str = "", task_id: str = "", session_id: str = None) -> Dict[str, Any]:
        """取消进行中的会话委托"""
        if target and not task_id:
            resolved, err = self._resolve_target(target)
            if err:
                return err
            target = resolved
        info = self._locate(session_id, target, task_id)
        if info is None:
            return self._fail(f"未找到委托: {task_id or target}", "not_found")
        status = info.get("status")
        if status in _FINISHED_STATUSES:
            return {"success": True, "message": f"委托已结束: {info.get('task_id')}（{status}）"}
        info["cancel_requested"] = True
        info["status"] = "cancelling"
        info["updated_at"] = time.time()
        delegation_state.save()
        try:
            from app.agent.loop.agent_loop_manager import AgentLoopManager
            loop = AgentLoopManager.get_instance().get_loop_sync(info.get("target"))
            if loop is not None:
                loop.stop()
        except Exception:
            pass
        bg_task = info.get("bg_task")
        main_loop = _get_main_loop()
        if bg_task is not None and main_loop is not None:
            try:
                main_loop.call_soon_threadsafe(bg_task.cancel)
            except Exception:
                pass
        return {"success": True, "message": f"已请求取消委托: {info.get('task_id')}"}

    def _cmd_list(self, session_id: str = None) -> Dict[str, Any]:
        """列出本会话的全部委托"""
        records = self._records(session_id)
        records.sort(key=lambda v: v.get("started_at") or 0, reverse=True)
        items = [self._summary(v) for v in records]
        return {"success": True, "count": len(items), "delegations": items}

    def _cmd_sessions(self, keyword: str = "", limit: int = 30, session_id: str = None) -> Dict[str, Any]:
        """列出会话（带名称），可按名称或 ID 过滤"""
        blocked = self._caller_block(session_id, "查看会话列表")
        if blocked:
            return blocked
        try:
            limit = int(limit or 30)
        except (TypeError, ValueError):
            limit = 30
        limit = max(1, min(100, limit))
        keyword = (keyword or "").strip().lower()
        try:
            from app.agent.loop.session_store import get_session_store
            metas = get_session_store().list_sessions(limit=200) or []
        except Exception as e:
            return self._fail(f"读取会话列表失败: {e}", "list_failed")

        items = []
        for meta in metas:
            sid = meta.session_id
            title = (getattr(meta, "title", None) or "").strip()
            if keyword and keyword not in title.lower() and keyword not in sid.lower():
                continue
            items.append({
                "session_id": sid,
                "title": title or f"未命名-{sid[:8]}",
                "message_count": getattr(meta, "message_count", 0),
                "last_active": getattr(meta, "last_active", ""),
                "is_current": bool(session_id and sid == session_id),
            })
            if len(items) >= limit:
                break
        return {"success": True, "count": len(items), "sessions": items}

    def _cmd_rename(self, target: str = "", title: str = "", session_id: str = None) -> Dict[str, Any]:
        """给会话命名（target 可用会话 ID 或会话名称）"""
        blocked = self._caller_block(session_id, "命名会话")
        if blocked:
            return blocked
        resolved, err = self._resolve_target(target)
        if err:
            return err
        clean = self._clean_title(title)
        if not 1 <= len(clean) <= 60:
            return self._fail("title 必填且长度需在 1..60 之间", "invalid_title")
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            previous = None
            for meta in store.list_sessions(limit=200) or []:
                if meta.session_id == resolved:
                    previous = getattr(meta, "title", None)
                    break
            store.set_session_title(resolved, clean)
        except Exception as e:
            return self._fail(f"重命名失败: {e}", "rename_failed")
        return {
            "success": True,
            "session_id": resolved,
            "title": clean,
            "previous_title": previous,
        }

    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """会话委托使用帮助"""
        commands = self.get_commands()
        info: Dict[str, Any] = {
            "name": self.cell_name,
            "description": "会话委托 — 主会话把任务异步委托给另一个真实持久会话执行，不阻塞当前会话",
            "available_commands": commands,
            "usage_examples": [
                {
                    "command": "send",
                    "args": {
                        "target": "sess_xxx",
                        "message": "修复 tests 下的失败用例并给出结论",
                        "purpose": "让测试全绿",
                    },
                },
                {"command": "status", "args": {"target": "sess_xxx"}},
                {"command": "results", "args": {"target": "sess_xxx"}},
            ],
            "_notes": [
                "target 可以填会话 ID 或会话名称；重名时返回 candidates，改用 session_id",
                "sessions 列出会话名称；rename 给会话命名",
                "仅本地主会话可委托/命名/列出会话；外部渠道会话与子 Agent 会被拒绝",
                "send 异步提交，立即返回 task_id；用 status/results 查询，cancel 取消",
                "只有主会话能发起委托；被委托会话禁止再委托",
                "目标会话不存在时传 create=true 新建",
                "优先复用已有会话（sessions 查名称）；同类工作固定交给同一会话，只有新职责才新建并用 title 命名",
                "撤回不级联：目标会话的对话与文件改动保留，需到目标会话用 turn_id 撤回",
                f"单会话活跃委托上限 {_MAX_ACTIVE_PER_PARENT} 个",
                "notify 不传默认 true；完成后推回父会话，300s 内失败自动重试",
            ],
        }
        if topic and topic in commands:
            info["focused_command"] = topic
        return info

    # ================================================================
    # 后台执行
    # ================================================================

    async def _run_delegation(
        self,
        key: str,
        target: str,
        message: str,
        purpose: str,
        parent: str = None,
        timeout: float = _DEFAULT_TIMEOUT,
        notify: bool = False,
    ) -> None:
        info = _DELEGATIONS.get(key)
        if info is None:
            return
        info["target_title"] = self._lookup_title(target)
        status, error, content, turn_id = "error", None, "", None
        try:
            with _ACTIVE_LOCK:
                _DELEGATED_ACTIVE.add(target)
            info["bg_task"] = asyncio.current_task()
            info.update({"status": "queued", "updated_at": time.time()})
            delegation_state.save()
            self._publish_progress(parent, info)

            from app.agent.loop.agent_loop_manager import AgentLoopManager
            from app.agent.loop.session_manager import get_session_manager

            mgr = AgentLoopManager.get_instance()
            # 目标会话正被用户使用时在此排队，status 保持 queued，不报错
            async with await mgr.get_lock(target):
                if info.get("cancel_requested"):
                    status, error = "cancelled", "已取消"
                else:
                    info.update({"status": "running", "updated_at": time.time()})
                    delegation_state.save()
                    self._publish_progress(parent, info)
                    loop = await mgr.get_loop(target)
                    memory = get_session_manager().get_or_create(target).memory
                    mode = _resolve_delegation_mode(target)
                    injection = self._build_injection(parent, purpose)
                    effective_timeout = max(0.01, float(timeout or _DEFAULT_TIMEOUT))
                    try:
                        status, content, error, turn_id = await asyncio.wait_for(
                            self._consume(
                                loop, info, message, memory, target, injection, mode, parent
                            ),
                            timeout=effective_timeout,
                        )
                    except asyncio.TimeoutError:
                        status = "timeout"
                        error = f"委托超时（{effective_timeout:g}s）"
                        try:
                            loop.stop()
                        except Exception:
                            pass
        except asyncio.CancelledError:
            status, error = "cancelled", "已取消"
        except Exception as e:
            status, error = "error", _describe_error(e)
        finally:
            with _ACTIVE_LOCK:
                _DELEGATED_ACTIVE.discard(target)
            info.update({
                "status": status,
                "result_content": content or "",
                "error": error,
                "turn_id": turn_id,
                "finished_at": time.time(),
                "updated_at": time.time(),
                "current_tool": None,
                "bg_task": None,
            })
            delegation_state.save()
            self._publish_done(parent, info)
            if notify:
                try:
                    await self._notify_parent(parent, info)
                except Exception as e:
                    logger.warning("[SessionAgent] 通知父会话失败 | session=%s | error=%s", parent, e)

    async def _consume(self, loop, info, message, memory, target, injection, mode, parent):
        status, content, error, turn_id = "error", "", None, None
        stop_reason = None
        async for event in loop.run_stream(
            message,
            memory=memory,
            session_id=target,
            system_injection=injection,
            mode=mode,
            delegated_from=parent,
        ):
            if not isinstance(event, dict):
                continue
            et = event.get("type")
            if et in ("tool_start", "tool_result", "reasoning", "thinking", "thinking_streaming"):
                self._update_progress(info, event, parent)
            elif et in ("stopped", "control_loop_stop", "heuristic_stop"):
                stop_reason = event.get("reason") or event.get("stop_reason") or "stopped"
                content = event.get("content") or content or ""
            elif et == "done":
                content = event.get("content") or content or ""
                turn_id = event.get("tracked_turn_id")
                completed = event.get("completed", True)
                done_reason = event.get("reason")
                if stop_reason or completed is False or done_reason not in (None, "complete", "completed"):
                    reason = stop_reason or done_reason or "stopped"
                    if info.get("cancel_requested") or reason == "user_cancelled":
                        status, error = "cancelled", "已取消"
                    else:
                        status, error = "error", f"目标会话未正常完成：{_STOP_REASON_TEXT.get(reason, reason)}"
                else:
                    status, error = "done", event.get("error")
        if not turn_id:
            turn_id = self._last_user_turn_id(memory)
        if status == "done" and not content.strip():
            status = "error"
            error = "目标会话没有返回结论（可能网络中断或提前终止）"
        if status == "error" and not error:
            error = "目标会话未返回结果"
        return status, content, error, turn_id

    @staticmethod
    def _last_user_turn_id(memory) -> str:
        try:
            messages = memory.get_messages() if memory else []
        except Exception:
            return None
        for msg in reversed(messages or []):
            if msg.get("role") == "user" and not msg.get("_is_compacted_notes"):
                return msg.get("id")
        return None

    def _update_progress(self, info: Dict[str, Any], event: Dict[str, Any], parent: str = None) -> None:
        et = event.get("type")
        info["updated_at"] = time.time()
        if et == "tool_start":
            starts = int(info.get("tool_starts", 0)) + 1
            info["tool_starts"] = starts
            info["current_tool"] = event.get("tool", "")
            info["tool_calls"] = max(int(info.get("tool_calls", 0)), starts)
        elif et == "tool_result":
            info["current_tool"] = None
            info["last_tool"] = event.get("tool", "")
            info["last_result_preview"] = _clip_preview(event.get("result"), 200)
            results = int(info.get("tool_results", 0)) + 1
            info["tool_results"] = results
            info["tool_calls"] = max(int(info.get("tool_calls", 0)), results)
        elif et in ("reasoning", "thinking", "thinking_streaming"):
            text = str(event.get("content") or "").strip()
            if text:
                info["last_reasoning"] = _clip_preview(text, 160)
        self._publish_progress(parent, info)

    @staticmethod
    def _publish_progress(session_id: str, info: Dict[str, Any]) -> None:
        if not session_id or not info:
            return
        now = time.time()
        if now - float(info.get("_pushed_at", 0) or 0) < 0.2:
            return
        info["_pushed_at"] = now
        try:
            from app.messaging.message_broker import get_message_broker
            get_message_broker().publish(session_id, {
                "type": "subagent_progress",
                "kind": "session",
                "task_id": info.get("task_id"),
                "name": f"session:{info.get('target')}",
                "target": info.get("target"),
                "target_title": info.get("target_title"),
                "purpose": info.get("purpose", ""),
                "status": info.get("status"),
                "current_tool": info.get("current_tool"),
                "tool_calls": info.get("tool_calls", 0),
                "last_tool": info.get("last_tool"),
                "last_result_preview": info.get("last_result_preview"),
                "last_reasoning": info.get("last_reasoning"),
                "elapsed_s": _elapsed_seconds(info),
            })
        except Exception:
            pass

    @staticmethod
    def _publish_done(session_id: str, info: Dict[str, Any]) -> None:
        if not session_id or not info:
            return
        status = info.get("status") or "error"
        try:
            from app.messaging.message_broker import get_message_broker
            get_message_broker().publish(session_id, {
                "type": "subagent_done",
                "kind": "session",
                "task_id": info.get("task_id"),
                "name": f"session:{info.get('target')}",
                "target": info.get("target"),
                "target_title": info.get("target_title"),
                "purpose": info.get("purpose", ""),
                "status": status,
                "success": status == "done",
                "content": (info.get("result_content") or "")[:300],
                "error": info.get("error"),
                "duration_s": _elapsed_seconds(info),
                "turn_id": info.get("turn_id"),
            })
        except Exception:
            pass

    async def _notify_parent(self, parent: str, info: Dict[str, Any]) -> None:
        if not parent:
            return
        status = info.get("status")
        content, _ = _clip_result(info.get("result_content") or "")
        message = (
            "[会话委托完成]\n"
            f"目标会话: {info.get('target')}\n"
            f"状态: {'成功' if status == 'done' else '失败'}\n"
            f"耗时: {_elapsed_seconds(info):g}s\n"
            f"结论:\n{content}\n"
            f"（如需回滚文件，请到该会话用 turn_id={info.get('turn_id')} 撤回）"
        )
        _PENDING_NOTIFY.setdefault(parent, deque()).append({
            "content": message,
            "notice": build_delegation_notice(info),
            "task_id": str(info.get("task_id") or ""),
            "msg_id": f"dlg_{uuid.uuid4().hex[:8]}_{int(time.time() * 1000)}",
            "target": str(info.get("target") or ""),
            "created_at": time.time(),
        })
        delegation_state.save()
        self._ensure_notify_worker(parent)

    def _ensure_notify_worker(self, session_id: str) -> None:
        task = _NOTIFY_WORKERS.get(session_id)
        if task is not None and not task.done():
            return
        try:
            _NOTIFY_WORKERS[session_id] = asyncio.create_task(self._notify_worker(session_id))
        except RuntimeError:
            logger.warning("[SessionAgent] 无运行中的事件循环，通知保留在队列 | session=%s", session_id)

    def _resume_pending_notify(self, session_id: str = None) -> int:
        """父会话重新上线时补投落库的待投递通知"""
        try:
            loop = _get_main_loop()
            if loop is None or not loop.is_running():
                return 0
            with _ACTIVE_LOCK:
                pending = [k for k, v in _PENDING_NOTIFY.items() if v]
            if session_id:
                pending = [k for k in pending if k == session_id]
            for parent in pending:
                self._ensure_notify_worker(parent)
            return len(pending)
        except Exception:
            return 0

    async def _notify_worker(self, session_id: str) -> None:
        """单条通知只投递一次：靠 _delivery 状态推进，不靠队列里是否存在"""
        deadline = time.time() + _NOTIFY_RETRY_WINDOW
        try:
            while True:
                queue = _PENDING_NOTIFY.get(session_id)
                if not queue:
                    return
                message = queue[0]
                if isinstance(message, str):
                    message = {"content": message, "notice": None}

                from app.server.task_manager import get_task_manager
                task_mgr = get_task_manager()
                info = task_mgr.get_task_info(session_id)
                if info is not None and getattr(info.status, "value", "") == "cancelled":
                    logger.info(
                        "[SessionAgent] 父会话已取消，丢弃通知 | session=%s | dropped=%d",
                        session_id, len(queue),
                    )
                    queue.clear()
                    delegation_state.save()
                    return

                msg_id = str(message.get("msg_id") or "")
                state = message.get("_delivery") or "new"

                if state == "queued":
                    if not msg_id or not task_mgr.has_pending_supplement(session_id, msg_id):
                        queue.popleft()
                        continue
                    if not task_mgr.has_running_task(session_id):
                        if not task_mgr.claim_supplement_message(session_id, msg_id):
                            queue.popleft()
                            continue
                        content = (message.get("content") or "").strip()
                        if content and await self._start_turn(session_id, content):
                            queue.popleft()
                            continue
                        task_mgr.enqueue_supplement_message(
                            session_id, self._build_payload(session_id, message)
                        )
                else:
                    result = await self._deliver_parent_message(session_id, message)
                    if result == "consumed":
                        queue.popleft()
                        continue
                    if result == "queued":
                        message["_delivery"] = "queued"

                if time.time() >= deadline:
                    logger.warning(
                        "[SessionAgent] 通知投递超时，保留待父会话上线补投 | session=%s | pending=%d",
                        session_id, len(queue),
                    )
                    delegation_state.save()
                    return
                await asyncio.sleep(_NOTIFY_RETRY_INTERVAL)
        finally:
            if _NOTIFY_WORKERS.get(session_id) is asyncio.current_task():
                _NOTIFY_WORKERS.pop(session_id, None)

    def _build_payload(self, session_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        content = (message.get("content") if isinstance(message, dict) else message) or ""
        notice = message.get("notice") if isinstance(message, dict) else None
        msg_id = str(message.get("msg_id") or "") if isinstance(message, dict) else ""
        if not msg_id:
            msg_id = f"session_agent_{int(time.time() * 1000)}"
            if isinstance(message, dict):
                message["msg_id"] = msg_id
        task_id = ""
        if isinstance(notice, dict):
            task_id = str(notice.get("task_id") or "")
        if not task_id and isinstance(message, dict):
            task_id = str(message.get("task_id") or "")
        return {
            "content": content,
            "source": "session_agent",
            "msg_id": msg_id,
            "received_at": time.time(),
            "created_at": time.time(),
            "platform": "session_agent",
            "message_type": "event",
            "notice": notice,
            "task_id": task_id,
        }

    async def _start_turn(self, session_id: str, content: str) -> bool:
        try:
            from app.agent.loop.agent_loop_manager import AgentLoopManager
            from app.agent.loop.session_manager import get_session_manager
            from app.server.session_mode import get_mode
            from app.server.task_manager import get_task_manager

            mgr = AgentLoopManager.get_instance()
            agent_loop = await mgr.get_loop(session_id)
            session_info = get_session_manager().get_or_create(session_id)
            started = await get_task_manager().start_task(
                session_id=session_id,
                agent_loop=agent_loop,
                user_input=content,
                memory=session_info.memory,
                mode=get_mode(session_id),
            )
            if started:
                self._attach_channel_reply(session_id)
            return bool(started)
        except Exception as e:
            logger.warning("[SessionAgent] 拉起父会话失败 | session=%s | error=%s", session_id, e)
            return False

    async def _deliver_parent_message(self, session_id: str, message: Dict[str, Any]) -> str:
        """首次投递，返回 consumed / queued / failed"""
        try:
            from app.server.task_manager import get_task_manager

            task_mgr = get_task_manager()
            payload = self._build_payload(session_id, message)
            if task_mgr.has_running_task(session_id):
                return "queued" if task_mgr.enqueue_supplement_message(session_id, payload) else "failed"
            content = (message.get("content") if isinstance(message, dict) else message) or ""
            if await self._start_turn(session_id, content):
                return "consumed"
            return "queued" if task_mgr.enqueue_supplement_message(session_id, payload) else "failed"
        except Exception as e:
            logger.warning("[SessionAgent] 通知父会话失败 | session=%s | error=%s", session_id, e)
            return "failed"

    @staticmethod
    def _attach_channel_reply(session_id: str) -> None:
        """父会话是外部渠道（QQ/飞书等）时，把本轮回复挂回渠道消费者"""
        try:
            from app.agent.loop.session_manager import get_session_manager
            from app.server.task_manager import get_task_manager
            info = get_session_manager().get_or_create(session_id)
            ctx = getattr(info, "platform_context", None) or {}
            if not isinstance(ctx, dict) or not ctx.get("platform"):
                return
            queue = get_task_manager().get_queue(session_id)
            if queue is None:
                return
            from app.channels import ChannelManager
            ChannelManager.get_instance().start_scheduler_task_queue_consumer(
                ctx, session_id, queue,
                {"source": "session_agent", "event_type": "delegation_done"},
                trigger_type="component",
            )
            logger.info(
                "[SessionAgent] 已挂载渠道回发 | session=%s | platform=%s",
                session_id, ctx.get("platform"),
            )
        except Exception as e:
            logger.warning(
                "[SessionAgent] 挂载渠道回发失败 | session=%s | error=%s", session_id, e
            )
