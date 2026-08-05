# -*- coding: utf-8 -*-
"""
子 Agent 组件 — 主 Agent 编排并行子 Agent

你可为每个子 Agent 定义工具白名单、人格、约束，并并行执行。

用法（调用 sub_agent 工具）：
  sub_agent.parallel(tasks)  # tasks 为对象数组，单任务传单元素数组即可
  sub_agent.list()
  sub_agent.status(name)
  sub_agent.cancel(name)

参数说明：
  - tools: 工具名数组，可选 read edit file grep glob ls shell memory
  - persona / constraints: 注入子 Agent 系统提示词
  - 平台组件工具（weixin_files/web_search/web_fetch/telegram_files/scheduler/
    qq_files/feishu_files）对子 Agent 禁用
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List

from app.core.interface.base_cell import BaseCell

logger = logging.getLogger(__name__)


def _resolve_engine_clone():
    from app.core.di.container import get_container
    from app.agent.llm.engine import OpenAICompatibleEngine, BaseLLMEngine

    container = get_container()
    if not container.has(BaseLLMEngine):
        raise RuntimeError("LLM 引擎未初始化，请先配置模型")
    main = container.resolve(BaseLLMEngine)

    clone = OpenAICompatibleEngine(
        api_key=getattr(main, "api_key", "") or "",
        base_url=getattr(main, "base_url", "") or "https://api.openai.com/v1",
        model=getattr(main, "model", "") or "gpt-4o",
        temperature=getattr(main, "temperature", 0.7),
        max_tokens=getattr(main, "_explicit_max_tokens", None),
        timeout=getattr(main, "timeout", 60),
        context_window=getattr(main, "context_window", None),
        verify_model=False,
        omit_max_tokens=getattr(main, "_omit_max_tokens", True),
        thinking=getattr(main, "_thinking", False),
        thinking_budget=getattr(main, "_thinking_budget", None),
    )
    return clone


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
    return future.result(timeout=timeout)


_SUBAGENTS: Dict[str, Dict[str, Any]] = {}


class SubAgent(BaseCell):
    """子 Agent 编排器 — 主 Agent 定义并行子 Agent"""

    FORBIDDEN_COMPONENT_TOOLS = {
        "weixin_files", "web_search", "web_fetch", "telegram_files",
        "scheduler", "qq_files", "feishu_files", "sub_agent",
        "config",  # 主 Agent 专属（管理自身 LLM 配置），子 Agent 禁用
    }

    @property
    def cell_name(self) -> str:
        return "sub_agent"

    def _build_system_injection(self, persona: str, constraints: str, tools: List[str]) -> str:
        parts = []
        parts.append("[子 Agent 任务身份] 你是主 Agent 委派的子 Agent，负责完成指定子任务。")
        if persona and persona.strip():
            parts.append(f"[人格设定]\n{persona.strip()}")
        if constraints and constraints.strip():
            parts.append(f"[约束与边界]\n{constraints.strip()}")

        tool_rules = self._extract_tool_rules(tools)
        if tool_rules:
            parts.append(f"[工具使用规则]\n{tool_rules}")

        parts.append("[协作要求]\n- 独立完成子任务，不要请求主 Agent 协助\n- 完成任务后给出简洁的结论总结")
        return "\n\n".join(parts)

    _TOOL_RULE_FRAGMENTS = {
        "read": (
            "**read 工具** - 读取文件内容\n"
            "| 参数 | 用途 |\n"
            "| file_path | 文件路径（必填） |\n"
            "| offset | 起始行号 |\n"
            "| limit | 读取行数 |\n"
            "| target | 搜索字符串，读取附近内容 |\n"
            "| needle | 精准匹配字符串，返回 ±3 行上下文 + 行号 |\n"
            "铁律: 读取文件必须用 read，禁止用 shell(cat/type/Get-Content)；大文件用 offset/limit 分页；编辑前用 needle 精准定位"
        ),
        "edit": (
            "**edit 工具** - 精确字符串替换\n"
            "| 参数 | 用途 |\n"
            "| file_path | 文件路径（必填） |\n"
            "| old_string | 要替换的文本（必填） |\n"
            "| new_string | 替换后的文本（必填） |\n"
            "| replace_all | 是否替换所有出现 |\n"
            "铁律: 编辑前必须先 read；old_string 必须唯一（除非 replace_all）；禁止用 shell 修改文件"
        ),
        "grep": (
            "**grep 工具** - 搜索文件内容\n"
            "| 参数 | 用途 |\n"
            "| pattern | 正则表达式（必填） |\n"
            "| path | 搜索目录 |\n"
            "| glob | 文件名过滤 |\n"
            "| output_mode | content/files_with_matches/count |\n"
            "| head_limit | 结果上限 |\n"
            "铁律: 搜索内容必须用 grep，禁止用 shell grep/findstr；先 files_with_matches 找文件，再 content 看细节"
        ),
        "file": (
            "**file 工具** - 文件系统操作\n"
            "fs 子命令: mkdir(创建目录, parents=true 建父目录) / delete(删除, 非空目录需 recursive=true) / "
            "exists(检查存在) / create(批量创建文件, files 为 dict[str,str])\n"
            "insight 子命令: structure(查看结构) / symbol(搜索符号, 必填 query)\n"
            "铁律: 文件操作优先用 file，禁止用 shell 直接操作"
        ),
        "shell": (
            "**shell 工具** - 执行命令\n"
            "决策: Python/脚本命令用 argv(数组)；需要 pipe/&&/>/通配符才用 cmd\n"
            "| 参数 | 适用场景 | 示例 |\n"
            "| argv | Python、git、单命令 | [\"python\",\"-c\",\"print(1)\"] |\n"
            "| cmd | pipe、&&、重定向 | \"python a.py | grep ok\" |\n"
            "铁律: 执行 Python 必须用 argv；argv 无引号解析问题；只有 shell 特性才用 cmd"
        ),
        "ls": (
            "**ls 工具** - 列出目录内容\n"
            "| 参数 | 用途 |\n"
            "| path | 目录路径 |\n"
            "| ignore | 忽略模式数组 |\n"
            "铁律: 列目录用 ls，禁止用 shell(ls/dir)；隐藏文件自动跳过"
        ),
        "glob": (
            "**glob 工具** - 按文件名模式搜索\n"
            "| 参数 | 用途 |\n"
            "| pattern | glob 模式（必填，如 **/*.py） |\n"
            "| path | 搜索根目录 |\n"
            "| limit | 结果上限 |\n"
            "铁律: 按文件名找文件用 glob；搜内容用 grep 不是 glob"
        ),
        "memory": (
            "**memory 工具** - 长期记忆\n"
            "命令: search(搜索记忆) / store(保存记忆) / profile(管理名字/称呼/人格) / read_archive(读存档)\n"
            "铁律: 上下文缺失时先 search 再回复；关联问题时优先检索记忆"
        ),
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
        return "\n\n".join(selected)

    def _allowed_tool_names(self, tools) -> List[str]:
        """规范化工具白名单，过滤禁用平台组件"""
        if tools is None:
            return []
        if isinstance(tools, str):
            try:
                import json
                tools = json.loads(tools)
            except Exception:
                tools = [t.strip() for t in tools.split(",") if t.strip()]
        if not isinstance(tools, list):
            return []
        names = []
        for t in tools:
            if isinstance(t, str) and t.strip():
                t = t.strip()
                if t in self.FORBIDDEN_COMPONENT_TOOLS:
                    logger.warning("[SubAgent] 工具 %s 在子 Agent 禁用列表，已忽略", t)
                    continue
                names.append(t)
        return names

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

    async def _run_one(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """运行单个子 Agent"""
        name = str(spec.get("name", f"sub_{uuid.uuid4().hex[:6]}"))
        task = str(spec.get("task", "")).strip()
        if not task:
            return {"name": name, "success": False, "error": "task 不能为空"}

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
            builder = create_default_builder(memory_dir=sub_mem_dir, memory=None)
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

        te.execute = _guarded_execute

        _SUBAGENTS[name] = {
            "name": name, "task": task, "tools": tools_list,
            "persona": persona, "constraints": constraints,
            "loop": loop, "engine": engine,
            "status": "running", "started_at": time.time(),
        }

        system_injection = self._build_system_injection(persona, constraints, tools_list)
        start_time = time.time()

        result = {"type": "error", "content": "", "iterations": 0, "tool_traces": []}
        try:
            async for event in loop.run_stream(task, session_id=session_id, system_injection=system_injection):
                if event.get("type") == "done":
                    result = event
                elif event.get("type") in {"stopped", "control_loop_stop", "heuristic_stop"}:
                    result = {**result, **event}
                elif event.get("type") == "error":
                    result = {"type": "error", "content": "", "error": event.get("error", "unknown")}
        finally:
            await _cleanup()
            if name in _SUBAGENTS:
                _SUBAGENTS[name]["status"] = "done"
                _SUBAGENTS[name]["finished_at"] = time.time()

        tool_traces = result.get("tool_traces", []) or []
        return {
            "name": name,
            "success": result.get("type") != "error",
            "content": result.get("content", ""),
            "iterations": result.get("iterations", 0),
            "tool_traces": tool_traces,
            "tools_used": sorted({t.get("tool", "") for t in tool_traces if t.get("tool")}),
            "duration_s": round(time.time() - start_time, 2),
            "error": result.get("error"),
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
    ) -> Dict[str, Any]:
        """
        并行执行子 Agent（单任务传单元素数组），每个子 Agent 可独立定义工具/人格/约束

        Args:
            tasks: 子 Agent 任务数组，每项为对象:
                   {"name": "子Agent名", "task": "任务描述", "tools": ["read","grep"],
                    "persona": "人格", "constraints": "约束", "max_iterations": 5}
                   name 必填；tools 可用 read edit file grep glob ls shell memory；
                   平台组件工具（weixin_files/web_search/web_fetch/telegram_files/
                   scheduler/qq_files/feishu_files）对子 Agent 禁用。
                   兼容传法: 对象数组 或 JSON 字符串数组 或 整个 JSON 字符串。

        Returns:
            {"results": [{name, success, content, ...}...], "total": N, "success_count": M,
             "failed": [names], "total_duration_s": ...}
        """
        import json
        specs = self._normalize_tasks(tasks)
        if not specs:
            return {"success": False, "error": "tasks 必须是非空数组，每项为 {name, task, tools, persona, constraints}"}

        async def _run_all():
            return await asyncio.gather(
                *[self._run_one(s) for s in specs],
                return_exceptions=True,
            )

        t0 = time.time()
        try:
            raw_results = _run_coro_on_main_loop(_run_all())
        except Exception as e:
            logger.exception("[SubAgent] parallel 失败")
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

        results = []
        for spec, raw in zip(specs, raw_results):
            if isinstance(raw, Exception):
                results.append({"name": spec.get("name"), "success": False, "error": f"{type(raw).__name__}: {raw}"})
            else:
                results.append(raw)

        success_count = sum(1 for r in results if r.get("success"))
        return {
            "success": success_count == len(results),
            "results": results,
            "total": len(results),
            "success_count": success_count,
            "failed": [r.get("name") for r in results if not r.get("success")],
            "total_duration_s": round(time.time() - t0, 2),
        }

    def _cmd_list(self) -> Dict[str, Any]:
        """列出所有已创建的子 Agent 及其状态

        Returns:
            {"success", "count", "subagents": [{name, status, task, tools, started_at}]}
        """
        items = []
        for name, info in _SUBAGENTS.items():
            items.append({
                "name": name,
                "status": info.get("status"),
                "task": info.get("task", "")[:80],
                "tools": info.get("tools", []),
                "started_at": info.get("started_at"),
            })
        return {"success": True, "count": len(items), "subagents": items}

    def _cmd_status(self, name: str = "") -> Dict[str, Any]:
        """查看子 Agent 运行状态

        Args:
            name: 子 Agent 名称（create/parallel 时指定的 name，留空则列出全部）

        Returns:
            指定子 Agent 的状态详情（status/task/tools/persona/constraints/时间戳）
        """
        if not name:
            return self._cmd_list()
        info = _SUBAGENTS.get(name)
        if not info:
            return {"success": False, "error": f"子 Agent 不存在: {name}", "available": list(_SUBAGENTS.keys())}
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
        }

    def _cmd_cancel(self, name: str = "") -> Dict[str, Any]:
        """取消/移除子 Agent（仅标记，运行中的无法强停）

        Args:
            name: 子 Agent 名称（create/parallel 时指定的 name）

        Returns:
            {"success": True, "message": "已移除子 Agent: <name>"}
        """
        if not name:
            return {"success": False, "error": "name 必填"}
        if name not in _SUBAGENTS:
            return {"success": False, "error": f"子 Agent 不存在: {name}"}
        info = _SUBAGENTS[name]
        loop = info.get("loop")
        if loop:
            try:
                loop.stop()
            except Exception:
                pass
        _SUBAGENTS.pop(name, None)
        return {"success": True, "message": f"已移除子 Agent: {name}"}

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
                "parallel: 并行执行子 Agent，tasks 为对象数组（单任务传单元素数组）",
                "每个子 Agent 独立使用 LLM 引擎（可安全并行）",
                "tools 传工具名数组（不传则子 Agent 无工具，只能文本回复）",
                "tools 可选: read edit file grep glob ls shell memory",
                "平台组件工具对子 Agent 禁用: weixin_files/web_search/web_fetch/telegram_files/scheduler/qq_files/feishu_files",
                "persona / constraints 会注入到子 Agent 的系统提示词",
            ],
        }
        if topic and topic in commands:
            base_info["focused_command"] = topic
        return base_info
