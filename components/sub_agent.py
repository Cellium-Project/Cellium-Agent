# -*- coding: utf-8 -*-
"""
子 Agent 组件 — 主 Agent 编排并行子 Agent

你可为每个子 Agent 定义工具白名单、人格、约束，并并行执行。

用法（调用 sub_agent 工具）：
  sub_agent.parallel(tasks)  # 同步：阻塞到全部完成
  sub_agent.spawn(tasks)     # 异步：后台跑，立即返回
  sub_agent.list()
  sub_agent.status(name)     # 实时进度
  sub_agent.results(name)    # 最终结论
  sub_agent.cancel(name)

参数说明：
  - tools: 工具名数组，可选 read edit file grep glob ls shell memory
  - persona / constraints: 注入子 Agent 系统提示词
  - 平台组件工具（weixin_files/web_search/web_fetch/telegram_files/scheduler/
    qq_files/feishu_files）对子 Agent 禁用
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from typing import Any, Dict, List

from app.core.interface.base_cell import BaseCell

logger = logging.getLogger(__name__)

_NOTIFY_RETRY_WINDOW = 300.0
_NOTIFY_RETRY_INTERVAL = 1.0
_PENDING_NOTIFY: Dict[str, deque] = {}
_NOTIFY_WORKERS: Dict[str, Any] = {}


def _resolve_parent_mode(session_id: str = None) -> str:
    """子 Agent 继承父会话的 Plan/Run 模式"""
    if not session_id:
        return "run"
    try:
        from app.server.session_mode import get_mode
        return get_mode(session_id)
    except Exception:
        return "run"


def _resolve_engine_clone():
    from app.core.di.container import get_container
    from app.agent.llm.engine import OpenAICompatibleEngine, BaseLLMEngine

    container = get_container()
    if not container.has(BaseLLMEngine):
        raise RuntimeError("LLM 引擎未初始化，请先配置模型")
    main = container.resolve(BaseLLMEngine)

    base_url = getattr(main, "base_url", "") or "https://api.openai.com/v1"

    engine_cls = OpenAICompatibleEngine
    engine_kwargs = {}
    if "opencode.ai" in base_url:
        try:
            from app.agent.llm.providers.opencode.engine import OpenCodeEngine
            engine_cls = OpenCodeEngine
            session_id = (getattr(main, "_extra_client_args", None) or {}).get("session_id")
            if session_id:
                engine_kwargs["session_id"] = session_id
        except ImportError:
            pass

    clone = engine_cls(
        api_key=getattr(main, "api_key", "") or "",
        base_url=base_url,
        model=getattr(main, "model", "") or "gpt-4o",
        temperature=getattr(main, "temperature", 0.7),
        max_tokens=getattr(main, "_explicit_max_tokens", None),
        timeout=getattr(main, "timeout", 60),
        context_window=getattr(main, "context_window", None),
        verify_model=False,
        omit_max_tokens=getattr(main, "_omit_max_tokens", True),
        thinking=getattr(main, "_thinking", False),
        thinking_budget=getattr(main, "_thinking_budget", None),
        **engine_kwargs,
    )
    return clone


SUB_AGENT_TIMEOUT_SECONDS = 900

SUB_AGENT_MAX_TIMEOUT_SECONDS = 3600


def _get_main_loop():
    """获取主事件循环"""
    from app.agent.di_config import _main_loop
    if _main_loop is not None:
        return _main_loop
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _run_coro_on_main_loop(coro, timeout=None):
    loop = _get_main_loop()
    if loop is None:
        raise RuntimeError("无可用主事件循环，无法运行子 Agent")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        future.cancel()
        raise


_SUBAGENTS: Dict[str, Dict[str, Any]] = {}


def _sub_key(session_id: str = None, name: str = "") -> str:
    return f"{session_id or 'default'}::{name}"


def _sub_scope(session_id: str = None) -> str:
    return _sub_key(session_id, "")


def _clip_preview(value, limit: int = 200) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."

def _resolve_user_name() -> str:
    """继承主 Agent 记忆里的用户称呼"""
    try:
        from app.agent.loop.agent_loop_manager import AgentLoopManager
        repo = getattr(AgentLoopManager.get_instance()._three_layer_memory, "repository", None)
        rec = repo.get_by_memory_key("profile:user_name", schema_type="profile") if repo else None
        return (rec.get("content") or "").strip() if rec else ""
    except Exception:
        return ""


def _parent_undo_context(session_id: str):
    if not session_id:
        return None
    try:
        from app.agent.loop.agent_loop_manager import AgentLoopManager
        parent = AgentLoopManager.get_instance().get_loop_sync(session_id)
        executor = getattr(parent, "_tool_executor", None)
        return executor.get_turn_context() if executor else None
    except Exception:
        return None


def _elapsed_seconds(info: Dict[str, Any]) -> float:
    """真实耗时：优先用 finished_at，运行中才用当前时间"""
    info = info or {}
    end = info.get("finished_at") or time.time()
    start = info.get("started_at") or end
    return round(max(0.0, end - start), 2)


class SubAgent(BaseCell):
    """子 Agent 编排器 — 主 Agent 定义并行子 Agent"""

    FORBIDDEN_COMPONENT_TOOLS = {
        "weixin_files", "web_search", "web_fetch", "telegram_files",
        "scheduler", "qq_files", "feishu_files", "sub_agent",
        "session_agent",  # 会话委托仅限主会话发起
        "config",  # 主 Agent 专属（管理自身 LLM 配置），子 Agent 禁用
    }

    @property
    def cell_name(self) -> str:
        return "sub_agent"

    def _build_system_injection(self, persona: str, constraints: str, tools: List[str]) -> str:
        parts = ["[子 Agent 任务身份] 你是主 Agent 委派的子 Agent，只完成指定子任务，不要请求主 Agent 协助。"]
        if persona and persona.strip():
            parts.append(f"[人格设定]\n{persona.strip()}")
        if constraints and constraints.strip():
            parts.append(f"[约束与边界]\n{constraints.strip()}")

        effective_tools = list(dict.fromkeys(list(tools or [])))
        tool_rules = self._extract_tool_rules(effective_tools)
        if tool_rules:
            parts.append(f"[工具规则]\n{tool_rules}")

        user_name = _resolve_user_name()
        parts.append(
            "[称呼] " + (f"用户称呼为「{user_name}」，直接使用。" if user_name else "不要询问用户称呼。")
        )
        parts.append(
            "[输出要求] 只给结论（发现+判断）、依据（文件:行）与建议；"
            "禁止粘贴或引用文件原文（任务明确要求原文时除外）。"
            "禁止寒暄、自我介绍、询问称呼等社交套话，直接交付结果。"
        )
        return "\n\n".join(parts)

    _TOOL_RULE_FRAGMENTS = {
        "read": "read：读文件用 read（禁止 shell cat/type）；大文件用 offset/limit；定位用 needle",
        "edit": "edit：先 read 再 edit；old_string 必须唯一（除非 replace_all）",
        "grep": "grep：搜内容用 grep（禁止 shell grep/findstr）；先 files_with_matches 再 content",
        "file": "file：fs=mkdir/delete/exists/create；insight=structure/symbol",
        "shell": "shell：Python/脚本用 argv；需要管道/重定向才用 cmd",
        "ls": "ls：列目录用 ls（禁止 shell ls/dir）",
        "glob": "glob：按文件名找文件用 glob，搜内容用 grep",
        "memory": "memory：上下文缺失先 search；store/profile/read_archive 按需用",
    }

    def _extract_tool_rules(self, tools: List[str]) -> str:
        """按白名单提取工具铁律，返回文本（无匹配则空串）"""
        if not tools:
            return ""
        selected = []
        for name in tools:
            rule = self._TOOL_RULE_FRAGMENTS.get(name)
            if rule:
                selected.append(rule)
        return "\n".join(selected)

    DEFAULT_AUTO_TOOLS = ("read", "ls", "glob", "grep")
    MAX_BACKGROUND_SUBAGENTS = 4
    MAX_PARALLEL_SUBAGENTS = 4

    def _allowed_tool_names(self, tools) -> List[str]:
        """显式传入即精确白名单；未传才回退 read/ls/glob/grep"""
        provided = tools is not None
        if isinstance(tools, str):
            try:
                import json
                tools = json.loads(tools)
            except Exception:
                tools = [t.strip() for t in tools.split(",") if t.strip()]
        if not isinstance(tools, list):
            tools = []
        if not provided:
            return list(self.DEFAULT_AUTO_TOOLS)
        names = []
        for t in tools:
            if isinstance(t, str) and t.strip():
                t = t.strip()
                if t in self.FORBIDDEN_COMPONENT_TOOLS:
                    logger.warning("[SubAgent] 工具 %s 在子 Agent 禁用列表，已忽略", t)
                    continue
                names.append(t)
        return list(dict.fromkeys(names))

    def _collect_tool_instances(self, allowed_names: List[str]) -> Dict[str, Any]:
        """收集白名单内工具实例（内置工具 + 组件工具）"""
        from app.core.di.container import get_container
        from app.core.util.component_tool_registry import get_component_tool_registry
        from app.agent.tools.shell_tool import ShellTool
        from app.agent.tools.read_tool import ReadTool
        from app.agent.tools.edit_tool import EditTool
        from app.agent.tools.file_tool import FileTool
        from app.agent.tools.grep_tool import GrepTool
        from app.agent.tools.glob_tool import GlobTool
        from app.agent.tools.ls_tool import LSTool
        from app.agent.tools.memory_tool import MemoryTool

        container = get_container()
        result: Dict[str, Any] = {}

        builtin_map = {
            "shell": ShellTool, "read": ReadTool, "edit": EditTool,
            "file": FileTool, "grep": GrepTool, "glob": GlobTool,
            "ls": LSTool, "memory": MemoryTool,
        }
        for name, cls in builtin_map.items():
            if name not in allowed_names:
                continue
            try:
                inst = container.resolve(cls)
                if inst is not None:
                    result[name] = inst
            except Exception as e:
                logger.warning("[SubAgent] 内置工具 %s 获取失败: %s", name, e)

        try:
            registry = get_component_tool_registry()
            comp_tools = registry.get_component_tools()
            for name, inst in comp_tools.items():
                if name in allowed_names:
                    result[name] = inst
        except Exception as e:
            logger.warning("[SubAgent] 获取组件工具失败: %s", e)

        try:
            from app.agent.loop.agent_loop_manager import AgentLoopManager
            mgr = AgentLoopManager.get_instance()
            main_tools = getattr(mgr, "_tools", {}) or {}
            for name, inst in main_tools.items():
                if name in allowed_names and name not in result:
                    result[name] = inst
        except Exception:
            pass

        return result

    @staticmethod
    def _error_item(name, error: str, *, timed_out: bool = False, duration_s: float = 0.0) -> Dict[str, Any]:
        item = {
            "name": name, "success": False, "content": "", "iterations": 0,
            "tools_used": [], "duration_s": round(duration_s, 2), "error": error,
        }
        if timed_out:
            item["timed_out"] = True
        return item

    async def _run_one(self, spec: Dict[str, Any], parent_session_id: str = None,
                       verbose: bool = False, progress=None) -> Dict[str, Any]:
        """运行单个子 Agent"""
        name = str(spec.get("name", f"sub_{uuid.uuid4().hex[:6]}"))
        task = str(spec.get("task", "")).strip()
        if not task:
            return {"name": name, "success": False, "error": "task 不能为空"}

        mode = _resolve_parent_mode(parent_session_id)
        tools_list = self._allowed_tool_names(spec.get("tools"))
        persona = str(spec.get("persona", "") or "")
        constraints = str(spec.get("constraints", "") or "")
        max_iterations = int(spec.get("max_iterations", 5) or 5)
        session_id = f"subagent:{name}:{uuid.uuid4().hex[:8]}"

        engine = _resolve_engine_clone()

        sub_mem_dir = None

        async def _cleanup():
            try:
                await engine.close()
            except Exception:
                pass
            if sub_mem_dir:
                import shutil
                try:
                    shutil.rmtree(sub_mem_dir, ignore_errors=True)
                except Exception:
                    pass

        from app.agent.loop.agent_loop import AgentLoop
        from app.agent.loop.memory import MemoryManager

        try:
            memory = MemoryManager()
            loop = AgentLoop(
                llm_engine=engine,
                memory=memory,
                three_layer_memory=None,
                tools={},
                max_iterations=max_iterations,
                session_id=session_id,
                enable_heuristics=False,
                flash_mode=True,
                enable_learning=False,
                intent_enabled=False,
                undo_context=_parent_undo_context(parent_session_id),
            )
        except Exception as e:
            await _cleanup()
            return {
                "name": name, "success": False, "content": "",
                "iterations": 0, "tool_traces": [], "tools_used": [],
                "duration_s": 0.0,
                "error": f"{type(e).__name__}: {e}",
            }

        allowed = self._collect_tool_instances(tools_list)
        if allowed:
            loop._builtin_tools = dict(allowed)
            loop.tools = dict(allowed)
            loop._tool_executor._builtin_tools = dict(allowed)
            loop._tool_executor.refresh_tools(dict(allowed))

        try:
            from app.agent.prompt import create_default_builder
            import tempfile
            import os as _os
            sub_mem_dir = _os.path.join(tempfile.gettempdir(), f"cellium_subagent_{uuid.uuid4().hex[:8]}")
            _os.makedirs(sub_mem_dir, exist_ok=True)
            builder = create_default_builder(
                memory_dir=sub_mem_dir, memory=None, ask_user_name=False,
            )
            loop._prompt_builder = builder
            loop._prompt_diff_tracker.__init__()
            loop._notes_dir = _os.path.join(sub_mem_dir, "notes")
            loop._session_notes_cache = {}
            logger.info("[SubAgent] 已隔离子 Agent 人格（独立 memory_dir=%s）", sub_mem_dir)
        except Exception as e:
            logger.warning("[SubAgent] 隔离人格失败，回退默认: %s", e)

        def _noop_refresh(*a, **kw):
            pass
        loop._refresh_tools = _noop_refresh

        te = loop._tool_executor

        async def _guarded_execute(tool_call, session_id=None, platform_context=None):
            name = getattr(tool_call, "name", "")
            if name not in allowed:
                return {"error": f"Tool '{name}' is not allowed for this sub-agent", "allowed": sorted(allowed)}
            inst = allowed.get(name)
            if inst is None:
                return {"error": f"Tool '{name}' not found in whitelist"}
            args = getattr(tool_call, "arguments", None) or {}
            if isinstance(args, dict):
                args = {k: v for k, v in args.items() if k != "_intent"}
            loop_ = asyncio.get_running_loop()
            try:
                result = await loop_.run_in_executor(
                    None,
                    lambda a=args: inst.execute_with_context(a, session_id=session_id, platform_context=platform_context)
                    if hasattr(inst, "execute_with_context")
                    else inst.execute(a)
                    if hasattr(inst, "execute")
                    else inst(**a),
                )
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}"}
            return result

        def _guarded_execute_sync(tool_call, session_id=None, platform_context=None):
            """同步版白名单执行（供 execute_sync 路径使用，保持同样的白名单隔离）"""
            name = getattr(tool_call, "name", "")
            if name not in allowed:
                return {"error": f"Tool '{name}' is not allowed for this sub-agent", "allowed": sorted(allowed)}
            inst = allowed.get(name)
            if inst is None:
                return {"error": f"Tool '{name}' not found in whitelist"}
            args = getattr(tool_call, "arguments", None) or {}
            if isinstance(args, dict):
                args = {k: v for k, v in args.items() if k != "_intent"}
            try:
                if hasattr(inst, "execute_with_context"):
                    return inst.execute_with_context(args, session_id=session_id, platform_context=platform_context)
                if hasattr(inst, "execute"):
                    return inst.execute(args)
                return inst(**args)
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}"}

        te.execute = _guarded_execute
        te.execute_sync = _guarded_execute_sync

        sub_key = _sub_key(parent_session_id, name)
        info = _SUBAGENTS.setdefault(sub_key, {})
        info.update({
            "name": name, "task": task, "tools": tools_list,
            "persona": persona, "constraints": constraints,
            "loop": loop, "engine": engine,
            "status": "running", "started_at": info.get("started_at") or time.time(),
            "updated_at": time.time(), "current_tool": None, "tool_calls": 0,
        })

        system_injection = self._build_system_injection(persona, constraints, tools_list)
        start_time = time.time()

        result = {"type": "error", "content": "", "iterations": 0, "tool_traces": []}
        try:
            async for event in loop.run_stream(
                task, session_id=session_id, system_injection=system_injection, mode=mode,
            ):
                if progress is not None and isinstance(event, dict):
                    try:
                        progress(event)
                    except Exception:
                        pass
                if event.get("type") == "done":
                    result = event
                elif event.get("type") in {"stopped", "control_loop_stop", "heuristic_stop"}:
                    result = {**result, **event}
                elif event.get("type") == "error":
                    result = {"type": "error", "content": "", "error": event.get("error", "unknown")}
        finally:
            await _cleanup()
            if sub_key in _SUBAGENTS:
                _SUBAGENTS[sub_key]["status"] = "done"
                _SUBAGENTS[sub_key]["finished_at"] = time.time()

        tool_traces = result.get("tool_traces", []) or []
        info = _SUBAGENTS.get(sub_key) or {}
        payload = {
            "name": name,
            "success": result.get("type") != "error",
            "content": result.get("content", ""),
            "iterations": result.get("iterations", 0),
            "tools_used": sorted({t.get("tool", "") for t in tool_traces if t.get("tool")}),
            "tool_calls": info.get("tool_calls", 0),
            "duration_s": round(time.time() - start_time, 2),
            "error": result.get("error"),
        }
        if verbose:
            payload["tool_traces"] = [self._compact_trace(t) for t in tool_traces]
        if info:
            info.update({
                "result_content": payload["content"],
                "tools_used": payload["tools_used"],
                "iterations": payload["iterations"],
                "error": payload.get("error"),
            })
        return payload

    @staticmethod
    def _compact_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
        """压缩 trace：只保留定位信息，原文压到 120 字符"""
        import json

        def clip(value, limit=120):
            if isinstance(value, dict):
                return {k: clip(v, limit) for k, v in list(value.items())[:4]}
            if isinstance(value, (list, tuple)):
                return [clip(v, limit) for v in list(value)[:4]]
            if isinstance(value, str):
                return value if len(value) <= limit else value[:limit] + "..."
            if isinstance(value, (int, float, bool)) or value is None:
                return value
            text = str(value)
            return text if len(text) <= limit else text[:limit] + "..."

        args = trace.get("arguments")
        result = trace.get("result")
        preview = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return {
            "tool": trace.get("tool", ""),
            "success": trace.get("success", False),
            "duration_ms": trace.get("duration_ms", 0),
            "arguments": clip(args if isinstance(args, dict) else {}),
            "result_preview": clip(preview),
        }

    # ================================================================
    # 命令
    # ================================================================

    def _normalize_tasks(self, tasks) -> List[Dict[str, Any]]:
        """规范化 tasks 参数为对象数组。

        兼容 LLM 的三种传法:
          1. 对象数组: [{"name": "...", "task": "..."}]
          2. JSON 字符串数组: ['{"name":"...","task":"..."}', ...]
          3. 整个 JSON 字符串: '[{"name":"...","task":"..."}]'
        """
        import json

        if isinstance(tasks, str):
            try:
                tasks = json.loads(tasks)
            except json.JSONDecodeError:
                return []

        if not isinstance(tasks, list):
            return []

        normalized = []
        for item in tasks:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError:
                    continue
            if isinstance(item, dict):
                normalized.append(item)
        return normalized

    def _cmd_parallel(
        self,
        tasks: List[Dict[str, Any]],
        timeout: float = 0,
        verbose: bool = False,
        session_id: str = None,
    ) -> Dict[str, Any]:
        """
        并行执行子 Agent（单任务传单元素数组），每个子 Agent 可独立定义工具/人格/约束

        Args:
            tasks: 子 Agent 任务数组，每项为对象:
                   {"name": "子Agent名", "task": "任务描述", "tools": ["read","grep"],
                    "persona": "人格", "constraints": "约束", "max_iterations": 5}
                   name 必填；tools 显式传入即精确白名单（不传才默认 read/ls/glob/grep）；
                   平台组件工具（weixin_files/web_search/web_fetch/telegram_files/
                   scheduler/qq_files/feishu_files）对子 Agent 禁用。
                   兼容传法: 对象数组 或 JSON 字符串数组 或 整个 JSON 字符串。
            timeout: 本次并行的总超时秒数（可选），0 或留空用默认 900 秒；上限 3600 秒，
                     长任务可自行调大，超时会取消子 Agent 并返回 timeout=true
            verbose: 默认 false 只返回结论；true 时附带压缩后的工具轨迹（原文≤120 字）

        Returns:
            {"results": [{name, success, content, iterations, tools_used, duration_s}...],
             "total": N, "success_count": M, "failed": [names], "total_duration_s": ...}
            verbose=true 时每项额外含 tool_traces（压缩版）
        """
        import json
        specs = self._normalize_tasks(tasks)
        if not specs:
            return {"success": False, "error": "tasks 必须是非空数组，每项为 {name, task, tools, persona, constraints}"}
        seen_names = {}
        for spec in specs:
            if not spec.get("name"):
                spec["name"] = f"sub_{uuid.uuid4().hex[:6]}"
            name = str(spec["name"])
            if name in seen_names:
                seen_names[name] += 1
                spec["name"] = f"{name}_{seen_names[name]}"
            else:
                seen_names[name] = 1

        try:
            effective_timeout = float(timeout or 0)
        except (TypeError, ValueError):
            effective_timeout = 0.0
        if not (effective_timeout > 0):
            effective_timeout = float(SUB_AGENT_TIMEOUT_SECONDS)
        effective_timeout = min(effective_timeout, float(SUB_AGENT_MAX_TIMEOUT_SECONDS))

        def _progress_for(name):
            key = _sub_key(session_id, name)

            def _progress(event):
                self._update_progress(key, event, session_id)

            return _progress

        async def _run_and_publish(spec):
            name = spec["name"]
            key = _sub_key(session_id, name)
            started = time.time()
            info = _SUBAGENTS.setdefault(key, {})
            info.update({
                "name": name, "task": spec.get("task", ""), "status": "running",
                "started_at": started, "updated_at": started, "tool_calls": 0,
            })
            self._publish_progress(session_id, info)
            payload = await self._run_one(
                spec, parent_session_id=session_id, verbose=verbose,
                progress=_progress_for(name),
            )
            self._publish_done_event(session_id, payload)
            return payload

        sem = asyncio.Semaphore(max(1, int(self.MAX_PARALLEL_SUBAGENTS)))

        async def _run_one_bounded(spec):
            name = spec["name"]
            started = time.time()
            async with sem:
                try:
                    return await asyncio.wait_for(
                        _run_and_publish(spec), timeout=effective_timeout
                    )
                except asyncio.TimeoutError:
                    item = self._error_item(
                        name, f"timeout（{effective_timeout:g}s）",
                        timed_out=True, duration_s=time.time() - started,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    item = self._error_item(
                        name, f"{type(e).__name__}: {e}", duration_s=time.time() - started
                    )
            key = _sub_key(session_id, name)
            info = _SUBAGENTS.get(key)
            if info is not None:
                info.update({"status": "error", "error": item["error"], "finished_at": time.time()})
            self._publish_done_event(session_id, item)
            return item

        async def _run_all():
            return await asyncio.gather(*[_run_one_bounded(s) for s in specs])

        t0 = time.time()
        batch = max(1, (len(specs) + self.MAX_PARALLEL_SUBAGENTS - 1) // self.MAX_PARALLEL_SUBAGENTS)
        guard_timeout = effective_timeout * batch + 30
        try:
            raw_results = _run_coro_on_main_loop(_run_all(), timeout=guard_timeout)
        except Exception as e:
            logger.exception("[SubAgent] parallel 失败")
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

        results = []
        for raw in raw_results:
            if isinstance(raw, Exception):
                results.append(self._error_item(None, f"{type(raw).__name__}: {raw}"))
            else:
                results.append(raw)

        success_count = sum(1 for r in results if r.get("success"))
        timed_out = [r.get("name") for r in results if r.get("timed_out")]
        response = {
            "success": success_count == len(results),
            "results": results,
            "total": len(results),
            "success_count": success_count,
            "failed": [r.get("name") for r in results if not r.get("success")],
            "total_duration_s": round(time.time() - t0, 2),
        }
        if timed_out:
            response["timeout"] = True
            response["error"] = (
                f"部分子 Agent 超时（{effective_timeout:g} 秒），已完成结果已保留；"
                f"超时: {', '.join(str(n) for n in timed_out)}"
            )
        return response

    def _cmd_spawn(
        self,
        tasks: List[Dict[str, Any]],
        notify: bool = True,
        auto_resume: bool = True,
        session_id: str = None,
    ) -> Dict[str, Any]:
        """后台启动子 Agent（立即返回，不阻塞主 Agent）

        Args:
            tasks: 同 parallel 的任务数组
            notify: 完成后是否通知主 Agent
            auto_resume: 通知时父会话空闲则自动起一轮处理结果
            session_id: 父会话 ID（适配层注入）

        Returns:
            {"success": True, "tasks": [{name, status}], "count": N}
        """
        specs = self._normalize_tasks(tasks)
        if not specs:
            return {"success": False, "error": "tasks 必须是非空数组"}
        for spec in specs:
            if not str(spec.get("task", "")).strip():
                return {"success": False, "error": "task 不能为空"}
            if not spec.get("name"):
                spec["name"] = f"sub_{uuid.uuid4().hex[:6]}"
        prefix = _sub_scope(session_id)
        running = [
            k for k, v in _SUBAGENTS.items()
            if k.startswith(prefix) and v.get("status") in ("queued", "running", "cancelling")
        ]
        if len(running) + len(specs) > self.MAX_BACKGROUND_SUBAGENTS:
            return {
                "success": False,
                "error": f"后台子 Agent 上限 {self.MAX_BACKGROUND_SUBAGENTS}，当前运行 {len(running)}",
            }
        for spec in specs:
            existing = _SUBAGENTS.get(_sub_key(session_id, spec["name"]))
            if existing and existing.get("status") in ("queued", "running", "cancelling"):
                return {"success": False, "error": f"子 Agent 名称已在使用: {spec['name']}"}
        loop = _get_main_loop()
        if loop is None or not loop.is_running():
            return {"success": False, "error": "无可用主事件循环，无法后台运行"}
        self._prune_subagents(prefix)
        now = time.time()
        for spec in specs:
            key = _sub_key(session_id, spec["name"])
            _SUBAGENTS.setdefault(key, {}).update({
                "name": spec["name"], "task": spec.get("task", ""),
                "tools": self._allowed_tool_names(spec.get("tools")),
                "persona": spec.get("persona", ""),
                "constraints": spec.get("constraints", ""),
                "status": "queued", "started_at": now, "updated_at": now,
                "cancel_requested": False,
            })
        future = asyncio.run_coroutine_threadsafe(
            self._spawn_all(specs, session_id, notify, auto_resume), loop
        )

        def _log_failure(f):
            try:
                f.result()
            except Exception as e:
                logger.warning("[SubAgent] 后台调度失败 | error=%s", e)

        future.add_done_callback(_log_failure)
        return {
            "success": True,
            "tasks": [{"name": s["name"], "status": "running"} for s in specs],
            "count": len(specs),
            "message": "已提交后台运行：status/results 查询进度，cancel 停止",
        }

    @staticmethod
    def _prune_subagents(prefix: str, keep: int = 20) -> None:
        finished = [
            (k, v.get("finished_at") or v.get("started_at") or 0)
            for k, v in _SUBAGENTS.items()
            if k.startswith(prefix) and v.get("status") in ("done", "error", "cancelled")
        ]
        if len(finished) <= keep:
            return
        for key, _ in sorted(finished, key=lambda x: x[1])[: len(finished) - keep]:
            _SUBAGENTS.pop(key, None)

    async def _spawn_all(self, specs, parent_session_id, notify, auto_resume):
        if _PENDING_NOTIFY.get(parent_session_id):
            self._ensure_notify_worker(parent_session_id)
        for spec in specs:
            key = _sub_key(parent_session_id, spec["name"])
            task = asyncio.create_task(
                self._run_background(spec, parent_session_id, notify, auto_resume)
            )
            _SUBAGENTS.setdefault(key, {})["bg_task"] = task

    async def _run_background(self, spec, parent_session_id, notify, auto_resume):
        name = spec["name"]
        key = _sub_key(parent_session_id, name)
        info = _SUBAGENTS.setdefault(key, {})
        if info.get("cancel_requested"):
            info.update({"status": "cancelled", "finished_at": time.time(), "current_tool": None})
            payload = {
                "name": name, "success": False, "content": "", "iterations": 0,
                "tools_used": [], "duration_s": 0, "error": "cancelled",
            }
            self._publish_done_event(parent_session_id, payload)
            await self._notify_parent(parent_session_id, payload, notify, auto_resume)
            return
        info.update({"status": "running", "updated_at": time.time()})
        self._publish_progress(parent_session_id, info)

        def progress(event):
            self._update_progress(key, event, parent_session_id)

        payload = None
        try:
            payload = await self._run_one(
                spec, parent_session_id=parent_session_id, verbose=False, progress=progress,
            )
        except asyncio.CancelledError:
            info = _SUBAGENTS.setdefault(key, {})
            info.update({"status": "cancelled", "finished_at": time.time(), "current_tool": None})
            payload = {
                "name": name, "success": False, "content": "", "iterations": 0,
                "tools_used": [], "duration_s": 0, "error": "cancelled",
            }
            self._publish_done_event(parent_session_id, payload)
            await self._notify_parent(parent_session_id, payload, notify, auto_resume)
            raise
        except Exception as e:
            info = _SUBAGENTS.setdefault(key, {})
            payload = {
                "name": name, "success": False, "content": "", "iterations": 0,
                "tools_used": [], "duration_s": 0, "error": f"{type(e).__name__}: {e}",
            }
            info.update({"status": "error", "finished_at": time.time(), "error": payload["error"]})
            self._publish_done_event(parent_session_id, payload)
        else:
            info = _SUBAGENTS.setdefault(key, {})
            info.update({
                "status": "done" if payload.get("success") else "error",
                "finished_at": time.time(),
                "result_content": payload.get("content", ""),
                "tools_used": payload.get("tools_used", []),
                "iterations": payload.get("iterations", 0),
                "error": payload.get("error"),
            })
            self._publish_done_event(parent_session_id, payload)
        if payload is not None:
            await self._notify_parent(parent_session_id, payload, notify, auto_resume)

    def _update_progress(self, key: str, event: Dict[str, Any], parent_session_id: str = None) -> None:
        info = _SUBAGENTS.get(key)
        if not info:
            return
        info["updated_at"] = time.time()
        et = event.get("type")
        publish = False
        if et == "tool_start":
            starts = int(info.get("tool_starts", 0)) + 1
            info["tool_starts"] = starts
            info["current_tool"] = event.get("tool", "")
            info["tool_calls"] = max(int(info.get("tool_calls", 0)), starts)
            publish = True
        elif et == "tool_result":
            info["current_tool"] = None
            info["last_tool"] = event.get("tool", "")
            info["last_tool_ms"] = event.get("duration_ms", 0)
            info["last_tool_ok"] = not bool(event.get("error"))
            info["last_result_preview"] = _clip_preview(event.get("result"), 200)
            results = int(info.get("tool_results", 0)) + 1
            info["tool_results"] = results
            info["tool_calls"] = max(int(info.get("tool_calls", 0)), results)
            publish = True
        if publish and parent_session_id:
            self._publish_progress(parent_session_id, info)

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
                "name": info.get("name"),
                "status": info.get("status"),
                "task": info.get("task", ""),
                "current_tool": info.get("current_tool"),
                "tool_calls": info.get("tool_calls", 0),
                "last_tool": info.get("last_tool"),
                "last_result_preview": info.get("last_result_preview"),
                "elapsed_s": _elapsed_seconds(info),
            })
        except Exception:
            pass

    @staticmethod
    def _build_done_message(payload: Dict[str, Any]) -> str:
        content = (payload.get("content") or "").strip()
        if len(content) > 4000:
            content = content[:4000] + "...[截断]"
        lines = [
            "[子Agent完成]",
            f"名称: {payload.get('name')}",
            f"状态: {'成功' if payload.get('success') else '失败'}",
            f"耗时: {payload.get('duration_s')}s | 迭代: {payload.get('iterations')}",
            f"工具: {', '.join(payload.get('tools_used') or []) or '无'}",
        ]
        if content:
            lines.append(f"结论:\n{content}")
        if payload.get("error"):
            lines.append(f"错误: {payload['error']}")
        lines.append("如需完整结果可调用 sub_agent.results(name)。")
        return "\n".join(lines)

    @staticmethod
    def _publish_done_event(session_id, payload) -> None:
        """向会话广播子 Agent 完成事件（仅 UI 展示，不触发唤醒）"""
        if not session_id:
            return
        try:
            from app.messaging.message_broker import get_message_broker
            get_message_broker().publish(session_id, {
                "type": "subagent_done",
                "name": payload.get("name"),
                "status": "done" if payload.get("success") else "error",
                "success": payload.get("success"),
                "tools_used": payload.get("tools_used", []),
                "content": (payload.get("content") or "")[:300],
                "error": payload.get("error"),
                "duration_s": payload.get("duration_s", 0),
                "elapsed_s": payload.get("duration_s", 0),
                "tool_calls": payload.get("tool_calls", 0),
                "iterations": payload.get("iterations", 0),
            })
        except Exception:
            pass

    async def _notify_parent(self, parent_session_id, payload, notify=True, auto_resume=True):
        if not notify or not parent_session_id:
            return
        message = self._build_done_message(payload)
        if not auto_resume:
            return
        _PENDING_NOTIFY.setdefault(parent_session_id, deque()).append(message)
        self._ensure_notify_worker(parent_session_id)

    def _ensure_notify_worker(self, session_id: str) -> None:
        task = _NOTIFY_WORKERS.get(session_id)
        if task is not None and not task.done():
            return
        try:
            _NOTIFY_WORKERS[session_id] = asyncio.create_task(self._notify_worker(session_id))
        except RuntimeError:
            logger.warning("[SubAgent] 无运行中的事件循环，通知保留在队列 | session=%s", session_id)

    async def _notify_worker(self, session_id: str) -> None:
        """可靠投递：运行中就排队，空闲就起一轮；失败持续重试"""
        deadline = time.time() + _NOTIFY_RETRY_WINDOW
        try:
            while True:
                queue = _PENDING_NOTIFY.get(session_id)
                if not queue:
                    return
                message = queue[0]
                if await self._deliver_parent_message(session_id, message):
                    queue.popleft()
                    continue
                if time.time() >= deadline:
                    logger.warning(
                        "[SubAgent] 通知超过重试窗口仍未送达，保留在队列 | session=%s | pending=%d",
                        session_id, len(queue),
                    )
                    return
                await asyncio.sleep(_NOTIFY_RETRY_INTERVAL)
        finally:
            if _NOTIFY_WORKERS.get(session_id) is asyncio.current_task():
                _NOTIFY_WORKERS.pop(session_id, None)

    async def _deliver_parent_message(self, session_id: str, message: str) -> bool:
        try:
            from app.server.task_manager import get_task_manager
            from app.agent.loop.agent_loop_manager import AgentLoopManager
            from app.agent.loop.session_manager import get_session_manager
            from app.server.session_mode import get_mode
            task_mgr = get_task_manager()
            queued_payload = {
                "content": message, "source": "sub_agent",
                "msg_id": f"subagent_{int(time.time() * 1000)}", "received_at": time.time(),
                "platform": "sub_agent", "message_type": "event",
            }
            if task_mgr.has_running_task(session_id):
                return bool(task_mgr.enqueue_supplement_message(session_id, queued_payload))
            mgr = AgentLoopManager.get_instance()
            agent_loop = await mgr.get_loop(session_id)
            session_info = get_session_manager().get_or_create(session_id)
            started = await task_mgr.start_task(
                session_id=session_id,
                agent_loop=agent_loop,
                user_input=message,
                memory=session_info.memory,
                mode=get_mode(session_id),
            )
            if started:
                return True
            return bool(task_mgr.enqueue_supplement_message(session_id, queued_payload))
        except Exception as e:
            logger.warning("[SubAgent] 通知投递失败 | session=%s | error=%s", session_id, e)
            return False

    def _cmd_results(self, name: str = "", session_id: str = None) -> Dict[str, Any]:
        """获取后台子 Agent 的最终结果

        Args:
            name: 子 Agent 名称

        Returns:
            {"success", "name", "status", "running", "content", "tools_used", "error"}
        """
        if not name:
            return {"success": False, "error": "name 必填"}
        info = _SUBAGENTS.get(_sub_key(session_id, name))
        if not info:
            return {"success": False, "error": f"子 Agent 不存在: {name}"}
        status = info.get("status")
        return {
            "success": True,
            "name": name,
            "status": status,
            "running": status in ("queued", "running", "cancelling"),
            "content": info.get("result_content", ""),
            "tools_used": info.get("tools_used", []),
            "iterations": info.get("iterations", 0),
            "error": info.get("error"),
            "elapsed_s": _elapsed_seconds(info),
        }

    def _cmd_list(self, session_id: str = None) -> Dict[str, Any]:
        """列出所有已创建的子 Agent 及其状态

        Returns:
            {"success", "count", "subagents": [{name, status, task, tools, started_at}]}
        """
        prefix = _sub_scope(session_id)
        items = []
        for key, info in _SUBAGENTS.items():
            if not key.startswith(prefix):
                continue
            items.append({
                "name": info.get("name", key[len(prefix):]),
                "status": info.get("status"),
                "task": info.get("task", "")[:80],
                "tools": info.get("tools", []),
                "started_at": info.get("started_at"),
                "elapsed_s": _elapsed_seconds(info),
                "current_tool": info.get("current_tool"),
                "tool_calls": info.get("tool_calls", 0),
            })
        return {"success": True, "count": len(items), "subagents": items}

    def _cmd_status(self, name: str = "", session_id: str = None) -> Dict[str, Any]:
        """查看子 Agent 运行状态

        Args:
            name: 子 Agent 名称（create/parallel 时指定的 name，留空则列出全部）

        Returns:
            指定子 Agent 的状态详情（status/task/tools/persona/constraints/时间戳）
        """
        if not name:
            return self._cmd_list(session_id=session_id)
        prefix = _sub_scope(session_id)
        info = _SUBAGENTS.get(_sub_key(session_id, name))
        if not info:
            return {
                "success": False,
                "error": f"子 Agent 不存在: {name}",
                "available": [k[len(prefix):] for k in _SUBAGENTS if k.startswith(prefix)],
            }
        return {
            "success": True,
            "name": name,
            "status": info.get("status"),
            "task": info.get("task"),
            "tools": info.get("tools"),
            "persona": info.get("persona"),
            "constraints": info.get("constraints"),
            "started_at": info.get("started_at"),
            "finished_at": info.get("finished_at"),
            "elapsed_s": _elapsed_seconds(info),
            "progress": {
                "current_tool": info.get("current_tool"),
                "tool_calls": info.get("tool_calls", 0),
                "last_tool": info.get("last_tool"),
                "last_tool_ms": info.get("last_tool_ms"),
                "last_tool_ok": info.get("last_tool_ok"),
                "last_result_preview": info.get("last_result_preview"),
            },
            "result_preview": (info.get("result_content") or "")[:300],
            "error": info.get("error"),
        }

    def _cmd_cancel(self, name: str = "", session_id: str = None) -> Dict[str, Any]:
        """取消后台子 Agent（发取消请求，工具线程到迭代边界停止）

        Args:
            name: 子 Agent 名称

        Returns:
            {"success": True, "message": "已请求取消子 Agent: <name>"}
        """
        if not name:
            return {"success": False, "error": "name 必填"}
        key = _sub_key(session_id, name)
        info = _SUBAGENTS.get(key)
        if not info:
            return {"success": False, "error": f"子 Agent 不存在: {name}"}
        status = info.get("status")
        if status in ("done", "error", "cancelled"):
            return {"success": True, "message": f"子 Agent 已结束: {name}（{status}）"}
        info["status"] = "cancelling"
        info["cancel_requested"] = True
        info["updated_at"] = time.time()
        loop = info.get("loop")
        if loop:
            try:
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
        return {"success": True, "message": f"已请求取消子 Agent: {name}"}

    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """查询子 Agent 组件使用帮助

        Args:
            topic: 要查看的子命令名（parallel/list/status/cancel，留空返回总览）

        Returns:
            组件的使用说明、命令列表、调用示例、注意事项
        """
        commands = self.get_commands()
        base_info: Dict[str, Any] = {
            "name": self.cell_name,
            "description": "子 Agent 编排器 — 主 Agent 并行编排子 Agent，每个子 Agent 可独立配置工具、人格、约束",
            "available_commands": commands,
            "command_count": len(commands),
            "usage_examples": [
                {
                    "command": "parallel",
                    "args": {
                        "tasks": [
                            {"name": "reviewer", "task": "审查 src/main.py", "tools": ["read", "grep"], "persona": "代码审查专家", "constraints": "只读"},
                            {"name": "tester", "task": "检查 tests/ 覆盖", "tools": ["glob", "ls"], "constraints": "只读"},
                        ],
                    },
                },
                {
                    "command": "parallel",
                    "args": {
                        "tasks": [
                            {"name": "single", "task": "单任务也走 parallel", "tools": ["read"]},
                        ],
                    },
                },
            ],
            "_notes": [
                "spawn: 后台运行子 Agent，立即返回；完成后默认自动唤醒主 Agent",
                "spawn 可选 notify=false（不通知）与 auto_resume=false（只发事件不唤醒）",
                "results(name): 取后台子 Agent 的最终结论",
                "status(name): 实时进度（current_tool/tool_calls/elapsed_s）",
                "cancel(name): 请求取消，工具线程到迭代边界停止",
                f"后台并发上限: {self.MAX_BACKGROUND_SUBAGENTS} 个/会话",
                "parallel: 同步并行，阻塞直到全部完成（需要等待结果时用）",
                "每个子 Agent 独立使用 LLM 引擎（可安全并行）",
                "tools 显式传入即精确白名单；不传才默认附带 read/ls/glob/grep",
                "tools 可选: read edit file grep glob ls shell memory",
                "默认只返回结论（content）；verbose=true 才附带压缩后的工具轨迹",
                "子 Agent 只应输出结论与文件:行依据，禁止粘贴文件原文",
                "平台组件工具对子 Agent 禁用: weixin_files/web_search/web_fetch/telegram_files/scheduler/qq_files/feishu_files",
                "persona / constraints 会注入到子 Agent 的系统提示词",
            ],
        }
        if topic and topic in commands:
            base_info["focused_command"] = topic
        return base_info
