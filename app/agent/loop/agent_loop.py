# -*- coding: utf-8 -*-
"""
Agent 主循环
"""

import json
import logging
import time
import asyncio
from typing import List, Dict, Any, Optional

from app.core.bus.event_bus import event_bus
from app.agent.loop.memory import MemoryManager
from app.core.util.component_tool_registry import get_component_tool_registry
from app.agent.heuristics import AgentLoopIntegration
from app.agent.learning import LearningIntegration
from app.agent.prompt import PromptBuilder
from app.agent.prompt.pieces import create_default_builder
from app.agent.memory.session_notes import SessionNotes
from app.agent.memory.session_compact import SessionCompactor
from app.agent.control import ControlLoop, LoopState, HardConstraintRenderer
from app.agent.control import (
    HybridController,
    HybridPhase,
    Observation,
    ThoughtParser,
    ParsedThought,
    ActionType,
    create_hybrid_controller,
)
from app.core.util.logger import set_runtime_status, get_runtime_status

from .tool_executor import (
    ToolExecutor,
    ToolDescriptionGenerator,
)
from .command_handler import CommandHandler
from .auto_hints import AutoHintManager
from .loop_controller import LoopController
from .prompt_context_builder import PromptContextBuilder
from .loop_event_publisher import LoopEventPublisher

logger = logging.getLogger(__name__)


class AgentLoop:
    """Agent 主循环"""

    def __init__(
        self,
        llm_engine,
        shell=None,
        memory: MemoryManager = None,
        three_layer_memory=None,
        tools: Dict[str, Any] = None,
        max_iterations: int = 10,
        session_id: str = "default",
        event_bus_instance=None,
        loop_detection_threshold: int = 3,
        enable_heuristics: bool = True,
        flash_mode: bool = False,
        enable_learning: bool = True,
        enable_hybrid: bool = True,
    ):
        self.llm = llm_engine
        self.shell = shell
        self.three_layer_memory = three_layer_memory
        self.max_iterations = max_iterations
        self.session_id = session_id
        self.flash_mode = flash_mode
        self._did_persist_before_compact = False  # 标记压缩前是否已持久化
        self._enable_hybrid = enable_hybrid

        # 事件总线
        self._bus = event_bus_instance or event_bus
        self._event_publisher = LoopEventPublisher(self._bus)

        # Hybrid 控制器（Plan-Execute-Observe-RePlan）
        self._hybrid_controller: Optional[HybridController] = None
        self._last_hybrid_phase: str = ""  # 跟踪上一次发送的 phase
        if enable_hybrid:
            self._hybrid_controller = create_hybrid_controller(
                max_plan_steps=5,
                max_replans=3,
            )
            logger.info("[AgentLoop] Hybrid 控制器已启用（Plan→Execute→Observe→RePlan）")

        # 加载记忆配置（必须在创建 MemoryManager 之前）
        memory_dir = getattr(three_layer_memory, 'memory_dir', 'memory') if three_layer_memory else 'memory'
        self._load_memory_config(memory_dir)

        # 创建 MemoryManager（使用配置参数）
        short_term_config = self._mem_config.get("short_term", {})
        self.memory = memory or MemoryManager(
            max_history=short_term_config.get("max_history", 50),
            max_tool_results=short_term_config.get("max_tool_results", 10),
            max_tool_result_length=short_term_config.get("max_tool_result_length", 500),
            auto_compact_threshold=short_term_config.get("auto_compact_threshold", 10000),
        )

        # 启发式模块
        self.heuristics = AgentLoopIntegration() if enable_heuristics else None

        # Learning 模块（依赖 heuristics）
        self.learning = None
        if enable_learning and self.heuristics:
            self.learning = LearningIntegration(
                heuristic_engine=self.heuristics.engine,
            )
            # 读取 override_policy 配置
            learning_cfg = self._get_learning_config()
            override = learning_cfg.get("override_policy")
            if override:
                self.learning.set_override_policy(override)

        # Control Loop Harness（flash_mode=False 时启用完整控制环）
        self.control_loop: Optional[ControlLoop] = None
        self._constraint_renderer: Optional[HardConstraintRenderer] = None
        self._loop_state: Optional[LoopState] = None
        if not flash_mode and self.heuristics:
            # flash_mode=False → ControlLoop + Heuristics（完整能力）
            from app.agent.control import create_control_loop
            bandit_memory_path = "data/control/bandit_stats.json"
            self.control_loop = create_control_loop(memory_path=bandit_memory_path)
            self._constraint_renderer = HardConstraintRenderer(max_output_tokens=100)
            logger.info("[AgentLoop] 控制环已启用（强约束模式）")
        elif flash_mode:
            # flash_mode=True → 只用 Heuristics（轻量模式）
            logger.info("[AgentLoop] 控制环已禁用")

        # ★ 分层协作：Learning → ControlLoop
        if self.control_loop and self.learning:
            self.learning.set_control_loop(self.control_loop)
            logger.info("[AgentLoop] Learning 与 ControlLoop 已建立协作")

        # Prompt 构建器
        self._prompt_builder = create_default_builder(memory_dir)
        self._prompt_context_builder = PromptContextBuilder(
            prompt_builder=self._prompt_builder,
            three_layer_memory=three_layer_memory,
            flash_mode=flash_mode,
        )

        # 会话笔记压缩
        session_config = self._mem_config.get("session_compact", {})
        self._notes_dir = session_config.get("notes_dir", f"{memory_dir}/notes")
        self._session_notes_cache: Dict[str, SessionNotes] = {}  # session_id -> SessionNotes
        self._session_compactor = SessionCompactor(
            llm_engine=self.llm,
            token_threshold=session_config.get("token_threshold", 100000),
            tool_call_threshold=session_config.get("tool_call_threshold", 10),
            keep_recent_messages=session_config.get("keep_recent_messages", 10),
            max_notes_length=session_config.get("max_notes_length", 2000),
            repository=three_layer_memory.repository if three_layer_memory else None,
        )

        # 循环控制器
        self._loop_controller = LoopController(
            max_iterations=max_iterations,
            loop_detection_threshold=loop_detection_threshold,
        )

        # 工具注册表
        self._builtin_tools: Dict[str, Any] = tools or {}
        self.tools = dict(self._builtin_tools)

        self._tool_executor = ToolExecutor(self.tools, self._builtin_tools)
        self._cmd_handler = CommandHandler()
        self._auto_hints = AutoHintManager()

        self._tool_call_count_in_round = 0

    def _get_session_notes(self, session_id: str) -> SessionNotes:
        """获取或创建指定 session 的笔记管理器"""
        if session_id not in self._session_notes_cache:
            self._session_notes_cache[session_id] = SessionNotes(session_id, notes_dir=self._notes_dir)
        return self._session_notes_cache[session_id]

    def _load_memory_config(self, memory_dir: str):
        """
        加载记忆配置

        修复：使用 AgentConfig 单例，而非独立读取文件
        """
        # 默认配置
        self._mem_config = {
            "short_term": {
                "max_history": 50,
                "max_tool_results": 10,
                "max_tool_result_length": 500,
                "auto_compact_threshold": 10000,
            },
            "session_compact": {
                "token_threshold": 100000,
                "keep_recent_messages": 10,
                "max_notes_length": 2000,
                "notes_dir": f"{memory_dir}/notes",
            },
        }

        try:
            # 使用 AgentConfig 单例获取配置
            from app.core.util.agent_config import get_config
            config = get_config()
            mem_config = config.get_section("memory")

            if mem_config:
                # 深度合并配置
                for key in ["short_term", "session_compact", "long_term"]:
                    if key in mem_config:
                        if key not in self._mem_config:
                            self._mem_config[key] = {}
                        if isinstance(mem_config[key], dict):
                            self._mem_config[key].update(mem_config[key])
                        else:
                            self._mem_config[key] = mem_config[key]
                logger.debug("[AgentLoop] 从 AgentConfig 加载记忆配置成功")
        except Exception as e:
            logger.debug("[AgentLoop] 加载记忆配置失败，使用默认值: %s", e)

    def _get_learning_config(self) -> Dict[str, Any]:
        """获取学习模块配置"""
        try:
            from app.core.util.agent_config import get_config
            config = get_config()
            return config.get_section("learning") or {}
        except Exception as e:
            logger.debug("[AgentLoop] 加载学习配置失败: %s", e)
            return {}

    def _sync_hybrid_state(self) -> Optional[Dict[str, Any]]:
        """
        同步 Hybrid 状态并发送状态变化事件
        
        Returns:
            如果状态发生变化，返回事件字典；否则返回 None
        """
        if not self._hybrid_controller or not self._loop_state:
            return None
        
        self._hybrid_controller.sync_to_loop_state(self._loop_state)
        
        phase_msg = self._hybrid_controller.get_phase_message()
        if phase_msg["phase"] != self._last_hybrid_phase:
            self._last_hybrid_phase = phase_msg["phase"]
            return {
                "type": "hybrid_phase",
                "phase": phase_msg["phase"],
                "icon": phase_msg["icon"],
                "message": phase_msg["message"],
                "description": phase_msg["description"],
                "detail": phase_msg.get("detail", ""),
            }
        
        return None

    def stop(self):
        """请求停止当前推理"""
        self._loop_controller.request_stop()

    def _should_update_goal(self, new_input: str, current_goal: str) -> bool:
        """
        判断是否应该更新用户目标

        Args:
            new_input: 新的用户输入
            current_goal: 当前的目标

        Returns:
            是否应该更新目标
        """
        if not current_goal:
            return True

        # 检测明确的"新任务"信号词（中英文）
        new_task_keywords = [
            # 中文
            "新任务", "新的任务", "换一个", "接下来", "现在请",
            "帮我", "请帮我", "我想要", "我想让", "现在需要",
            "重新开始", "从新开始", "开始新的", "另一个问题",
            "换个话题", "换个主题", "不再", "不用了", "算了",
            # 英文
            "new task", "new question", "next task", "another one",
            "help me", "please help", "i want", "i need", "let's do",
            "start over", "restart", "different topic", "change topic",
            "never mind", "forget it", "actually", "instead",
        ]
        
        new_input_lower = new_input.lower()
        for kw in new_task_keywords:
            if kw in new_input_lower:
                if not self._is_similar_goal(new_input, current_goal):
                    return True

        # 检测明确的取消/否定当前任务（中英文）
        cancel_keywords = [
            # 中文
            "不对", "不是这个", "弄错了", "取消", "停止", "不要了",
            # 英文
            "wrong", "not this", "mistake", "cancel", "stop", "never mind",
            "don't", "dont", "nope", "incorrect",
        ]
        for kw in cancel_keywords:
            if kw in new_input_lower:
                return True

        return False

    def _is_similar_goal(self, new_input: str, current_goal: str) -> bool:
        """
        判断新输入是否与当前目标相似（可能是延续同一任务）
        """
        new_words = set(new_input.lower().split())
        goal_words = set(current_goal.lower().split())
        
        # 移除常见停用词（中英文）
        stop_words = {
            # 中文
            "的", "了", "是", "在", "有", "和", "与", "或", "我", "你", "他", "她", "它",
            # 英文
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
            "my", "your", "his", "her", "its", "our", "their",
            "to", "for", "of", "with", "at", "by", "from", "in", "on",
            "and", "or", "but", "so", "if", "then", "that", "this", "these",
            "please", "can", "could", "would", "will", "should", "do", "does",
        }
        new_words -= stop_words
        goal_words -= stop_words
        
        if not new_words or not goal_words:
            return False
        
        # 计算重叠率
        overlap = len(new_words & goal_words)
        similarity = overlap / min(len(new_words), len(goal_words))
        
        return similarity > 0.3  # 30% 以上重叠认为是相似目标

    @property
    def has_long_term_memory(self) -> bool:
        return self.three_layer_memory is not None

    def register_tool(self, name: str, tool_instance):
        """注册工具"""
        self.tools[name] = tool_instance
        self._builtin_tools[name] = tool_instance
        self._tool_executor.refresh_tools(self.tools)

    def _refresh_tools(self):
        """刷新工具表"""
        try:
            registry = get_component_tool_registry()
            component_tools = registry.get_component_tools()
            self.tools = {**component_tools, **self._builtin_tools}
            self._tool_executor.refresh_tools(self.tools)
            if component_tools:
                logger.info(
                    "[AgentLoop] 工具刷新 | 内置=%d | 组件工具=%d | 总计=%d",
                    len(self._builtin_tools), len(component_tools), len(self.tools),
                )
        except Exception as e:
            logger.warning("[AgentLoop] 工具刷新失败，使用内置工具: %s", e)
            self.tools = dict(self._builtin_tools)

    async def run(self, user_input: str, memory: MemoryManager = None, session_id: str = None) -> Dict[str, Any]:
        result = {"type": "error", "content": "", "iterations": 0, "tool_traces": []}
        async for event in self.run_stream(user_input, memory=memory, session_id=session_id):
            if event["type"] == "done":
                result = event
            elif event["type"] in {"stopped", "control_loop_stop", "heuristic_stop"}:
                result = {
                    **result,
                    **event,
                    "content": event.get("content", result.get("content", "")),
                    "iterations": event.get("iteration", result.get("iterations", 0)),
                    "tool_traces": event.get("tool_traces", result.get("tool_traces", [])),
                }
            elif event["type"] == "error":
                raise Exception(event.get("error", "AgentLoop stream error"))
        return result

    def _get_last_assistant_message(self, memory: MemoryManager, skip_thinking: bool = True) -> str:
        """
        获取最近一条 assistant 文本消息
        
        Args:
            memory: 内存管理器
            skip_thinking: 是否跳过 JSON 思考格式
            
        Returns:
            最后一条 assistant 消息内容
        """
        for msg in reversed(memory.get_messages()):
            if msg.get("role") == "assistant" and msg.get("content"):
                content = msg.get("content", "")
                if skip_thinking and self._is_thinking_json(content):
                    continue
                return content
        return ""

    def _is_thinking_json(self, content: str) -> bool:
        """
        检测内容是否是纯 JSON 思考格式
        
        Args:
            content: 消息内容
            
        Returns:
            是否是纯 JSON 思考
        """
        if not content:
            return False
        
        content = content.strip()
        
        # 检测纯 JSON 格式
        if content.startswith("{") and content.endswith("}"):
            try:
                import json
                data = json.loads(content)
                # 判断是否是思考格式
                if isinstance(data, dict) and "reasoning" in data and "action" in data:
                    return True
            except:
                pass
        
        # 检测 markdown 包裹的 JSON
        if content.startswith("```json") or content.startswith("```"):
            try:
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if json_match:
                    import json
                    data = json.loads(json_match.group(1))
                    if isinstance(data, dict) and "reasoning" in data and "action" in data:
                        return True
            except:
                pass
        
        return False

    def _persist_snapshot_before_compact(
        self,
        user_input: str,
        effective_session: str,
        effective_memory: MemoryManager,
    ) -> None:
        """在强制压缩前持久化当前对话快照"""
        if self.has_long_term_memory and effective_memory.get_messages():
            last_response = self._get_last_assistant_message(effective_memory)
            self._persist_conversation(user_input, last_response, effective_session, memory=effective_memory)
            self._did_persist_before_compact = True

    def _resolve_runtime_max_tokens(self, constraint: Any) -> Optional[int]:
        """将控制约束映射为真正的 LLM 输出限制"""
        if not constraint:
            return None
        trigger_reason = getattr(constraint, "trigger_reason", "") or ""
        max_tokens = int(getattr(constraint, "max_tokens", 0) or 0)
        if trigger_reason.startswith("compress") and max_tokens > 0:
            return max_tokens
        return None

    def _get_forbidden_tool_names(self, constraint: Any) -> set:
        """提取本轮真正需要在运行时阻止的工具名"""
        if not constraint:
            return set()
        return {
            item for item in getattr(constraint, "forbidden", [])
            if isinstance(item, str) and item in self.tools
        }

    READ_ONLY_TOOLS = {"file", "memory", "web_search", "web_fetch", "read_file", "file_read", "shell"}
    WRITE_TOOLS = {"write_to_file", "file_write", "mkdir", "delete", "edit", "move", "copy"}

    def _can_parallel(self, tool_name: str) -> bool:
        """判断工具是否可以并行执行"""
        return tool_name in self.READ_ONLY_TOOLS

    async def _execute_tools_parallel(
        self,
        tool_calls_info: List[Dict[str, Any]],
        effective_session: str,
        iteration: int,
        effective_memory: MemoryManager,
    ):
        """顺序执行所有工具（async generator，逐个yield结果）"""
        if not tool_calls_info:
            return

        async def execute_and_yield(info: Dict[str, Any]) -> Dict[str, Any]:
            trace = await self._execute_single_tool(info, effective_session, iteration, effective_memory)
            call_id = getattr(info["tool_call"], 'id', None) or info.get("tool_call_id") or f"{info['tool_name']}_{id(info['tool_call'])}"
            return info["tool_name"], info["arguments"], trace, call_id

        for idx, info in enumerate(tool_calls_info):
            name, arguments, trace, call_id = await execute_and_yield(info)
            yield {"type": "tool_result", "tool": name, "call_id": call_id,
                   "arguments": arguments, "result": trace["result"], "duration_ms": trace["duration_ms"]}

    async def _execute_single_tool(
        self,
        info: Dict[str, Any],
        effective_session: str,
        iteration: int,
        effective_memory: MemoryManager,
    ) -> Dict[str, Any]:
        """执行单个工具并发布事件"""
        tool_call = info["tool_call"]
        tool_name = info["tool_name"]
        arguments = info["arguments"]
        tool_call_id = info["tool_call_id"]
        blocked_by_constraint = info["blocked_by_constraint"]

        t0 = time.time()
        if blocked_by_constraint:
            duration_ms = 0
            result = {
                "error": f"Tool '{tool_name}' is blocked by current control decision",
                "blocked": True,
                "reason": "redirect",
                "forbidden_tools": [],
            }
            logger.warning("[AgentLoop] 阻止重复工具调用 | tool=%s", tool_name)
            self._event_publisher.publish_tool_call_error(
                session_id=effective_session,
                iteration=iteration,
                tool_name=tool_name,
                call_id=tool_call_id,
                error=result["error"],
            )
        else:
            try:
                result = await self._tool_executor.execute(tool_call)
                duration_ms = (time.time() - t0) * 1000
                self._event_publisher.publish_tool_call_end(
                    session_id=effective_session,
                    iteration=iteration,
                    tool_name=tool_name,
                    call_id=tool_call_id,
                    result=result,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                duration_ms = (time.time() - t0) * 1000
                result = {"error": str(e)}
                self._event_publisher.publish_tool_call_error(
                    session_id=effective_session,
                    iteration=iteration,
                    tool_name=tool_name,
                    call_id=tool_call_id,
                    error=str(e),
                )

        if isinstance(result, dict):
            result["elapsed_ms"] = round(duration_ms)
        if tool_call_id:
            effective_memory.add_tool_result(tool_call_id, result)
        self._tool_executor.track_result(tool_name, result)

        trace = {
            "tool": tool_name,
            "arguments": arguments,
            "result": result if isinstance(result, dict) else {"output": str(result)},
            "duration_ms": round(duration_ms),
            "success": not isinstance(result, dict) or (
                result.get("success") is not False and result.get("error") is None
            ),
        }

        logger.info("[AgentLoop] 发送 tool_result 事件 | tool=%s | duration=%dms", tool_name, round(duration_ms))
        return trace

    async def _emit_stop_and_finalize(
        self,
        *,
        stop_event: Dict[str, Any],
        user_input: str,
        effective_session: str,
        effective_memory: MemoryManager,
        tool_traces: List[Dict[str, Any]],
        iteration: int,
        start_time: float,
        final_response_content: Optional[str] = None,
    ):
        """统一处理 stop 场景的收尾逻辑"""
        yield stop_event

        total_time = (time.time() - start_time) * 1000
        self._event_publisher.publish_loop_end(
            session_id=effective_session,
            total_iterations=iteration,
            reason=stop_event.get("reason") or stop_event.get("type", "stopped"),
            result=stop_event,
        )

        if not self._did_persist_before_compact:
            self._cleanup_incomplete_tool_calls(effective_memory)
            content_to_persist = final_response_content or self._get_last_assistant_message(effective_memory)
            self._persist_conversation(user_input, content_to_persist, effective_session, memory=effective_memory)
        else:
            self._did_persist_before_compact = False

        if self.control_loop and self._loop_state:
            self.control_loop.end_session(self._loop_state)

        if self.learning:
            stuck_iters = self._loop_state.features.stuck_iterations if self._loop_state and self._loop_state.features else 0
            self.learning.end_session(
                error=stop_event.get("reason") != "user_cancelled",
                iteration=max(iteration, 1),
                max_iterations=self.max_iterations,
                tool_call_count=len([t for t in tool_traces if t.get("tool")]),
                stuck_iterations=stuck_iters,
            )

        yield {
            "type": "done",
            "content": final_response_content or "",
            "iterations": iteration,
            "tool_traces": tool_traces,
            "stop_reason": stop_event.get("reason"),
            "stop_type": stop_event.get("type"),
            "action": stop_event.get("action"),
            "completed": False,
            "total_time_ms": total_time,
        }


    async def run_stream(self, user_input: str, memory: MemoryManager = None, session_id: str = None, system_injection: str = None):

        """流式执行 Agent 循环"""
        effective_memory = memory or self.memory
        effective_session = session_id or self.session_id
        start_time = time.time()

        try:
            # === 1. 消息接收事件 ===
            self._event_publisher.publish_message_received(
                session_id=effective_session,
                message=user_input,
            )
            effective_memory.add_user_message(user_input)

            # === 1.5 会话目标更新 ===
            session_notes = self._get_session_notes(effective_session)
            current_goal = session_notes.get_goal()
            if self._should_update_goal(user_input, current_goal):
                logger.info("[AgentLoop] 检测到新目标，更新 | session=%s | 旧目标: %s | 新目标: %s",
                           effective_session, current_goal[:50] if current_goal else "(无)", user_input[:50])
                session_notes.set_goal(user_input, force=True)
                session_notes.save()

            # === 2. 拦截系统命令 ===
            if self._cmd_handler.is_slash_command(user_input):
                async for evt in self._cmd_handler.process(user_input):
                    yield evt
                return

            # === 3. 循环 ===
            final_response_content = None
            tool_traces = []
            # 重置循环控制器
            self._loop_controller.start()
            self._session_compactor._pending_compact = False  # 重置压缩标记

            if self.heuristics:
                self.heuristics.start_session(effective_session)

            # Learning: 会话开始，选择 Policy
            if self.learning:
                policy_name = self.learning.start_session()
                logger.info("[Learning] 选择 Policy: %s", policy_name)

            # LoopState: 会话状态（Flash模式也需要，供Learning等模块使用）
            self._loop_state = LoopState(
                session_id=effective_session,
                max_iterations=self.max_iterations,
                user_input=user_input,
                available_tools=list(self.tools.keys()),
            )

            # Hybrid 控制器：每次输入重置 + 意图检测
            if self._hybrid_controller:
                # 复用笔记系统的目标检测逻辑
                if current_goal and self._should_update_goal(user_input, current_goal):
                    logger.info("[Hybrid] 检测到意图变化，重置计划")
                self._hybrid_controller.reset()
                self._last_hybrid_phase = ""
                logger.debug("[Hybrid] 控制器已重置")

            # Control Loop: 会话开始
            if self.control_loop:
                self.control_loop.start_session(self._loop_state)

            # Hybrid: 显示初始观察状态
            if self._hybrid_controller:
                phase_msg = self._hybrid_controller.get_phase_message()
                if phase_msg["phase"] != self._last_hybrid_phase:
                    self._last_hybrid_phase = phase_msg["phase"]
                    yield {
                        "type": "hybrid_phase",
                        "phase": phase_msg["phase"],
                        "icon": phase_msg["icon"],
                        "message": phase_msg["message"],
                        "description": phase_msg["description"],
                    }

            yield {"type": "thinking", "content": "正在思考..."}

            self._tool_call_count_in_round = 0  # 重置轮次工具调用计数
            _pending_system_injection = system_injection  # 外部平台注入的引导文本

            while True:
                # === 检查并注入补充消息 ===
                try:
                    from app.server.task_manager import get_task_manager
                    task_mgr = get_task_manager()
                    supplements = task_mgr.drain_supplement_messages(effective_session)
                    for sup in supplements:
                        sup_content = sup.get("content", "")
                        if sup_content:
                            logger.info(f"[AgentLoop] 注入补充消息 | content={sup_content[:50]}...")
                            effective_memory.add_user_message(sup_content)
                            supplement_event = {
                                "type": "supplement_injected",
                                "content": sup_content,
                                "source": sup.get("source", "unknown"),
                            }
                            logger.info(f"[AgentLoop] yield supplement_injected | content={sup_content[:30]}")
                            yield supplement_event
                except Exception as e:
                    logger.warning(f"[AgentLoop] 注入补充消息失败: {e}")

                # === 在迭代开始前执行待处理的压缩 ===
                if not self.flash_mode and self._session_compactor.has_pending_compact():
                    logger.info("[AgentLoop] 执行待处理的会话压缩...")
                    yield {"type": "thinking", "content": "正在压缩会话记忆..."}
                    self._persist_snapshot_before_compact(user_input, effective_session, effective_memory)
                    session_notes = self._get_session_notes(effective_session)
                    await self._session_compactor.compact_now(effective_memory, session_notes)

                if self._loop_controller.is_stop_requested:
                    logger.info("[AgentLoop] 检测到停止请求，中断推理")
                    async for event in self._emit_stop_and_finalize(
                        stop_event={"type": "stopped", "reason": "user_cancelled", "iteration": self._loop_controller.iteration},
                        user_input=user_input,
                        effective_session=effective_session,
                        effective_memory=effective_memory,
                        tool_traces=tool_traces,
                        iteration=self._loop_controller.iteration,
                        start_time=start_time,
                        final_response_content=self._get_last_assistant_message(effective_memory),
                    ):
                        yield event
                    return

                if self._loop_controller.iteration >= self.max_iterations:
                    logger.info("[AgentLoop] 达到最大迭代硬上限 | iter=%d", self._loop_controller.iteration)
                    async for event in self._emit_stop_and_finalize(
                        stop_event={"type": "stopped", "reason": "max_iterations_exceeded", "iteration": self._loop_controller.iteration},
                        user_input=user_input,
                        effective_session=effective_session,
                        effective_memory=effective_memory,
                        tool_traces=tool_traces,
                        iteration=self._loop_controller.iteration,
                        start_time=start_time,
                        final_response_content=self._get_last_assistant_message(effective_memory),
                    ):
                        yield event
                    return

                self._loop_controller.advance()
                iteration = self._loop_controller.iteration


                _pending_guidance_msg = None
                _force_stop = False
                _active_constraint = None

                # Control Loop: 每轮决策
                if self.control_loop and self._loop_state:
                    # 更新状态
                    self._loop_state.iteration = iteration
                    self._loop_state.tool_traces = tool_traces
                    self._loop_state.last_tool_result = tool_traces[-1].get("result") if tool_traces else None
                    self._loop_state.elapsed_ms = int((time.time() - start_time) * 1000)

                    # 获取决策
                    decision = self.control_loop.step(self._loop_state)

                    # 更新运行时状态（供 Agent 自我感知）
                    set_runtime_status(self._loop_state)

                    # 渲染决策为强约束（三段结构）
                    constraint = self._constraint_renderer.render(
                        decision,
                        features=self._loop_state.features,
                        state=self._loop_state,
                    )
                    _active_constraint = constraint
                    ctrl_injection = self._constraint_renderer.render_combined(constraint)
                    if _pending_system_injection and ctrl_injection:
                        _pending_system_injection = f"{_pending_system_injection}\n{ctrl_injection}"
                    elif ctrl_injection:
                        _pending_system_injection = ctrl_injection
                    _force_stop = constraint.force_stop

                    if decision.force_memory_compact and not self.flash_mode:
                        logger.info("[AgentLoop] 执行控制环触发的强制压缩 | iter=%d | action=%s", iteration, decision.action_type)
                        yield {"type": "thinking", "content": "正在根据控制决策压缩上下文..."}
                        self._persist_snapshot_before_compact(user_input, effective_session, effective_memory)
                        if effective_memory.should_compact():
                            effective_memory.compact_tool_results()
                        session_notes = self._get_session_notes(effective_session)
                        await self._session_compactor.compact_now(effective_memory, session_notes)

                    if decision.should_stop or _force_stop:
                        logger.info("[ControlLoop] 终止迭代 | action=%s | trigger=%s", decision.action_type, constraint.trigger_reason)
                        async for event in self._emit_stop_and_finalize(
                            stop_event={
                                "type": "control_loop_stop",
                                "reason": constraint.trigger_reason,
                                "iteration": iteration,
                                "action": decision.action_type,
                            },
                            user_input=user_input,
                            effective_session=effective_session,
                            effective_memory=effective_memory,
                            tool_traces=tool_traces,
                            iteration=iteration,
                            start_time=start_time,
                            final_response_content=self._get_last_assistant_message(effective_memory),
                        ):
                            yield event
                        return


                # Heuristics: 旧系统（兼容保留，但不覆盖 ControlLoop 的决策）
                if self.heuristics:
                    context = self.heuristics.build_context(
                        session_id=effective_session,
                        iteration=iteration,
                        max_iterations=self.max_iterations,
                        tool_traces=tool_traces,
                        user_input=user_input,
                        elapsed_ms=int((time.time() - start_time) * 1000),
                    )

                    # ControlLoop 已启用时，复用其提取的 features 避免重复计算
                    features_from_control = self._loop_state.features if self.control_loop else None

                    # 只有在 ControlLoop 未决策时才用 Heuristics 判断终止
                    if not self.control_loop:
                        should_stop, stop_reason = self.heuristics.should_stop(context)
                        if should_stop:
                            logger.info("[Heuristics] 终止迭代 | reason=%s", stop_reason)
                            async for event in self._emit_stop_and_finalize(
                                stop_event={
                                    "type": "heuristic_stop",
                                    "reason": stop_reason,
                                    "iteration": iteration,
                                },
                                user_input=user_input,
                                effective_session=effective_session,
                                effective_memory=effective_memory,
                                tool_traces=tool_traces,
                                iteration=iteration,
                                start_time=start_time,
                                final_response_content=self._get_last_assistant_message(effective_memory),
                            ):
                                yield event
                            return


                    # 只有在 ControlLoop 未产生 guidance 时才用 Heuristics 的
                    if not _pending_guidance_msg:
                        redirect_guidance = self.heuristics.get_redirect_guidance(context, features_from_control)
                        if redirect_guidance:
                            suggestions = redirect_guidance.get("suggestions", [])
                            reasons = redirect_guidance.get("reasons", [])
                            tool_recommendations = self.heuristics.get_tool_recommendations(
                                context, available_tools=list(self.tools.keys()), top_k=3,
                                features=features_from_control,
                            )
                            _pending_guidance_msg = AutoHintManager.build_redirect_message(
                                reasons, suggestions, tool_recommendations
                            )
                            yield {
                                "type": "heuristic_redirect",
                                "reasons": reasons,
                                "suggestions": suggestions,
                                "iteration": iteration,
                            }

                    warnings = self.heuristics.get_warnings(context, features_from_control)
                    if warnings:
                        logger.warning("[Heuristics] %s", "; ".join(warnings))

                # === 构建提示词 ===
                session_messages = effective_memory.get_messages()

                runtime_status_str = None
                rs = get_runtime_status()
                if rs:
                    runtime_status_str = rs.to_summary()
                
                # 注入计划摘要到上下文
                if self._loop_state and self._loop_state.hybrid_plan_summary:
                    plan_context = f"[当前计划: {self._loop_state.hybrid_plan_summary}]"
                    if runtime_status_str:
                        runtime_status_str = f"{runtime_status_str}\n{plan_context}"
                    else:
                        runtime_status_str = plan_context

                if iteration == 1:
                    llm_messages = self._prompt_context_builder.build_first_round(
                        user_input=user_input,
                        session_messages=session_messages,
                        guidance_message=_pending_guidance_msg,
                        system_injection=_pending_system_injection,
                        runtime_status=runtime_status_str,
                    )
                else:
                    auto_hints = self._auto_hints.get_auto_tool_hints(self.tools)

                    security_hint = self._auto_hints.check_security_error_and_suggest(tool_traces)
                    if security_hint:
                        auto_hints = auto_hints + "\n\n" + security_hint if auto_hints else security_hint

                    llm_messages = self._prompt_context_builder.build_subsequent_round(
                        session_messages=session_messages,
                        auto_hints=auto_hints,
                        guidance_message=_pending_guidance_msg,
                        system_injection=_pending_system_injection,
                        runtime_status=runtime_status_str,
                        iteration=iteration,
                    )

                if iteration > 1:
                    yield {"type": "thinking", "content": "分析结果中..."}

                # 获取工具定义（只调用一次，避免重复刷新）
                tool_defs = self._get_tools_definition()
                logger.info("[AgentLoop] 迭代 %d: 准备调用 LLM (消息数=%d, tools=%d)",
                           iteration, len(llm_messages), len(tool_defs))
                response = await self.llm.chat(
                    messages=llm_messages,
                    tools=tool_defs,
                    max_tokens=self._resolve_runtime_max_tokens(_active_constraint),
                )

                logger.info("[AgentLoop] 迭代 %d: LLM 返回 (tool_calls=%d, content长度=%d)",
                           iteration, len(response.tool_calls) if response.tool_calls else 0, len(response.content or ""))

                # === Hybrid: 解析思考 ===
                parsed_thought: Optional[ParsedThought] = None
                if self._hybrid_controller and response.content:
                    parsed_thought = self._hybrid_controller.process_thought(response.content)
                    logger.info(
                        "[AgentLoop] 思考解析 | action=%s | plan_steps=%d | confidence=%.2f | phase=%s",
                        parsed_thought.action.value,
                        len(parsed_thought.plan),
                        parsed_thought.confidence,
                        self._hybrid_controller.state.phase.value,
                    )
                    
                    if parsed_thought.action == ActionType.DIRECT_RESPONSE:
                        logger.info("[AgentLoop] 模型判断可直接回答，跳过工具调用")
                        yield {"type": "content_chunk", "content": response.content}
                        if not self.flash_mode:
                            effective_memory.add_assistant_message(response.content)
                        continue
                    
                    if parsed_thought.action == ActionType.CLARIFY:
                        logger.info("[AgentLoop] 模型需要用户澄清")
                        yield {"type": "content_chunk", "content": response.content}
                        if not self.flash_mode:
                            effective_memory.add_assistant_message(response.content)
                        continue

                # ★ 同步 Hybrid 状态到 LoopState（让 ControlLoop 感知）
                hybrid_event = self._sync_hybrid_state()
                if hybrid_event:
                    yield hybrid_event

                # ★ 记录 LLM 输出内容（用于检测重复循环）
                if self._loop_state and response.content:
                    self._loop_state.recent_llm_outputs.append(response.content)
                    # 保留最近 10 条
                    if len(self._loop_state.recent_llm_outputs) > 10:
                        self._loop_state.recent_llm_outputs.pop(0)

                # 更新 token 统计（从 LLM 响应中获取实际值）
                if response.usage and self._loop_state:
                    prompt_tokens = response.usage.get("prompt_tokens", 0)
                    completion_tokens = response.usage.get("completion_tokens", 0)
                    self._loop_state.tokens_used += prompt_tokens + completion_tokens
                    logger.debug(
                        "[AgentLoop] Token 统计 | prompt=%d | completion=%d | 累计=%d",
                        prompt_tokens, completion_tokens, self._loop_state.tokens_used
                    )

                set_runtime_status(self._loop_state)

                self._event_publisher.publish_loop_iteration(
                    session_id=effective_session,
                    iteration=iteration,
                    max_iterations=self.max_iterations,
                    has_tool_calls=bool(response.tool_calls),
                )

                # === 5. 处理工具调用 ===
                if response.tool_calls:
                    self._tool_call_count_in_round += len(response.tool_calls)
                    self._session_compactor.track_tool_call()

                    # LLM 在工具调用前输出的文本内容（interim content）
                    interim_content = (response.content or "").strip()
                    if interim_content:
                        if parsed_thought and parsed_thought.reasoning:
                            yield {"type": "thinking", "content": parsed_thought.reasoning}
                        else:
                            yield {"type": "content_chunk", "content": interim_content}
                        if not self.flash_mode:
                            effective_memory.add_assistant_message(interim_content)

                    tool_calls_info: List[Dict[str, Any]] = []
                    for tool_call in response.tool_calls:
                        tool_name = tool_call.name
                        arguments = tool_call.arguments
                        forbidden_tools = self._get_forbidden_tool_names(_active_constraint)
                        blocked_by_constraint = tool_name in forbidden_tools

                        tool_call_id = effective_memory.add_tool_call(tool_name, arguments)

                        description = ToolDescriptionGenerator.generate(tool_name, arguments)
                        desc_str = description if isinstance(description, str) else str(description)
                        logger.debug("[AgentLoop] 工具描述（兜底）: %s", desc_str[:60] if desc_str else "")

                        # ★ 使用 tool_call.id 作为唯一标识符（确保前后端匹配）
                        call_id = getattr(tool_call, 'id', None) or tool_call_id or f"{tool_name}_{id(tool_call)}"
                        logger.info("[AgentLoop] 发送 tool_start 事件 | tool=%s | call_id=%s", tool_name, call_id)
                        yield {"type": "tool_start", "tool": tool_name, "arguments": arguments, "description": description, "call_id": call_id}

                        self._event_publisher.publish_tool_call_start(
                            session_id=effective_session,
                            iteration=iteration,
                            tool_name=tool_name,
                            arguments=arguments,
                            call_id=tool_call_id,
                        )

                        tool_calls_info.append({
                            "tool_call": tool_call,
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "tool_call_id": tool_call_id,
                            "blocked_by_constraint": blocked_by_constraint,
                        })

                    await asyncio.sleep(0.05)

                    # 收集本轮迭代的工具调用结果（append 到外层 tool_traces）
                    iteration_traces = []
                    async for event in self._execute_tools_parallel(
                        tool_calls_info, effective_session, iteration, effective_memory
                    ):
                        if event.get("type") == "tool_result":
                            tool_name = event["tool"]
                            arguments = event["arguments"]
                            result = event["result"]
                            duration_ms = event["duration_ms"]
                            trace_item = {
                                "tool": tool_name,
                                "arguments": arguments,
                                "result": result,
                                "duration_ms": duration_ms,
                                "success": not isinstance(result, dict) or (
                                    result.get("success") is not False and result.get("error") is None
                                ),
                            }
                            iteration_traces.append(trace_item)
                            tool_traces.append(trace_item)  # 同时添加到累积的 traces
                        yield event

                    if self._loop_state:
                        set_runtime_status(self._loop_state)

                    # === Hybrid: 观察执行结果 ===
                    if self._hybrid_controller and iteration_traces:
                        for trace in iteration_traces:
                            if self._hybrid_controller.state.pending_steps:
                                next_step = self._hybrid_controller.get_next_step()
                                if next_step:
                                    obs = self._hybrid_controller.observe_result(
                                        step=next_step,
                                        success=trace.get("success", False),
                                        output=trace.get("result"),
                                    )
                                    logger.info(
                                        "[Hybrid] 观察 | tool=%s | success=%s | matched=%s | phase=%s",
                                        trace.get("tool"),
                                        obs.success,
                                        obs.matched_expectation,
                                        self._hybrid_controller.state.phase.value,
                                    )
                                    if obs.needs_replan:
                                        logger.warning(
                                            "[Hybrid] 需要重新规划 | reason=%s",
                                            obs.replan_reason
                                        )
                        
                        # 观察后同步状态
                        hybrid_event = self._sync_hybrid_state()
                        if hybrid_event:
                            yield hybrid_event

                    # Control Loop: 每轮结束，更新 Bandit
                    if self.control_loop and self._loop_state:
                        self._loop_state.last_tool_result = iteration_traces[-1].get("result") if iteration_traces else None
                        reward = self.control_loop.end_round(self._loop_state)
                        logger.debug("[ControlLoop] 本轮结束 | reward=%.2f | cumulative=%.2f", reward, self._loop_state.cumulative_reward)

                    # Learning: 迭代级别反馈更新
                    if self.learning and self._loop_state:
                        stuck_iters = self._loop_state.features.stuck_iterations if self._loop_state.features else 0
                        tool_count = len(iteration_traces)  # 本轮工具调用数量
                        self.learning.update_round(
                            iteration=self._loop_state.iteration,
                            max_iterations=self._loop_state.max_iterations,
                            tool_call_count=tool_count,
                            stuck_iterations=stuck_iters,
                        )

                    # 两级压缩（在所有工具调用结束后，按顺序执行）
                    if not self.flash_mode:
                        # 第一级：快速压缩 tool_result
                        if effective_memory.should_compact():
                            saved = effective_memory.compact_tool_results()
                            if saved > 0:
                                logger.info("[AgentLoop] 压缩工具结果 | saved=%d bytes", saved)

                        # 第二级：标记会话笔记压缩（在下一次迭代开始时执行）
                        if self._session_compactor.should_compact(effective_memory):
                            self._session_compactor.request_compact()

                    continue

                # === 5.6 输出截断检测 ===
                if hasattr(response, 'finish_reason') and response.finish_reason == "length":
                    logger.info("[AgentLoop] 输出被截断 (finish_reason=length)，继续迭代补充...")
                    if not self.flash_mode:
                        effective_memory.add_assistant_message(response.content or "")
                    yield {"type": "thinking", "content": "输出被截断，正在补充..."}
                    continue

                # === 5.7 工具帮助检测 ===
                _help_keywords = ["工具帮助", "工具定义", "参数格式", "tool_help", "tool help",
                                  "怎么调用", "如何使用", "查看工具", "工具的参数", "不确定参数"]
                content_preview = (response.content or "").strip()[:200]
                if not response.tool_calls and any(kw in content_preview.lower() for kw in _help_keywords):
                    tool_defs = self._get_tools_definition()
                    help_text = AutoHintManager.format_tool_help(tool_defs)
                    logger.info("[AgentLoop] 检测到工具帮助请求，注入工具定义")
                    if not self.flash_mode:
                        effective_memory.add_assistant_message(response.content or "")
                        effective_memory.add_user_message(
                            f"[系统] 以下是可用工具的完整定义，请参考后重新执行操作：\n\n{help_text}"
                        )
                    yield {"type": "thinking", "content": "正在分析工具定义..."}
                    continue

                # === 6. 最终回复===
                content = response.content or ""

                is_looping, repeated_output = self._loop_controller.check_output_loop(content)
                if is_looping:
                    logger.warning("[AgentLoop] 检测到循环重复输出")
                    if not self.flash_mode:
                        effective_memory.add_assistant_message(content)
                    if content:
                        yield {"type": "content_chunk", "content": content}
                    async for event in self._emit_stop_and_finalize(
                        stop_event={
                            "type": "stopped",
                            "reason": "loop_detected",
                            "repeated_output": repeated_output,
                            "iteration": iteration,
                        },
                        user_input=user_input,
                        effective_session=effective_session,
                        effective_memory=effective_memory,
                        tool_traces=tool_traces,
                        iteration=iteration,
                        start_time=start_time,
                        final_response_content=content,  
                    ):
                        yield event
                    return


                content = response.content or ""
                final_response_content = content

                # Control Loop: 每轮结束（无工具调用时也要更新 Bandit）
                if self.control_loop and self._loop_state:
                    self._loop_state.last_tool_result = None  
                    reward = self.control_loop.end_round(self._loop_state)
                    logger.debug("[ControlLoop] 本轮结束（无工具调用）| reward=%.2f", reward)

                # Learning: 迭代级别反馈更新
                if self.learning and self._loop_state:
                    stuck_iters = self._loop_state.features.stuck_iterations if self._loop_state.features else 0
                    self.learning.update_round(
                        iteration=self._loop_state.iteration,
                        max_iterations=self._loop_state.max_iterations,
                        tool_call_count=0,
                        stuck_iterations=stuck_iters,
                    )

                if not self.flash_mode:
                    effective_memory.add_assistant_message(content)
                yield {"type": "content_chunk", "content": content}

                total_time = (time.time() - start_time) * 1000

                self._event_publisher.publish_response_complete(
                    session_id=effective_session,
                    iteration=iteration,
                    content=content,
                    total_time_ms=total_time,
                )
                self._event_publisher.publish_loop_end(
                    session_id=effective_session,
                    total_iterations=iteration,
                    reason="complete",
                    result={"type": "response", "content": content, "iterations": iteration},
                )

                # 如果压缩前已持久化，跳过对话结束时的重复持久化
                if not self._did_persist_before_compact:
                    self._persist_conversation(user_input, final_response_content, effective_session, memory=effective_memory)
                else:
                    self._did_persist_before_compact = False

                # Control Loop: 会话结束（end_session 内部已记录日志）
                if self.control_loop and self._loop_state:
                    self.control_loop.end_session(self._loop_state)

                # Learning: 会话结束
                if self.learning:
                    # 获取额外的统计信息
                    tool_call_count = len([t for t in tool_traces if t.get("tool")])
                    stuck_iters = self._loop_state.features.stuck_iterations if self._loop_state and self._loop_state.features else 0
                    self.learning.end_session(
                        error=False,
                        iteration=iteration,
                        max_iterations=self.max_iterations,
                        tool_call_count=tool_call_count,
                        stuck_iterations=stuck_iters,
                    )

                yield {"type": "done", "content": content, "iterations": iteration, "tool_traces": tool_traces}
                return

        except Exception as e:
            # Control Loop: 会话结束（异常）
            if self.control_loop and self._loop_state:
                self.control_loop.end_session(self._loop_state)

            # Learning: 会话结束（异常）
            if self.learning:
                current_iteration = iteration if 'iteration' in dir() else 1
                stuck_iters = self._loop_state.features.stuck_iterations if self._loop_state and self._loop_state.features else 0
                self.learning.end_session(
                    error=True,
                    iteration=current_iteration,
                    max_iterations=self.max_iterations,
                    stuck_iterations=stuck_iters,
                )

            import traceback as tb_module
            self._event_publisher.publish_error(
                session_id=effective_session if 'effective_session' in dir() else "",
                error_type=type(e).__name__,
                error_message=str(e),
                traceback=tb_module.format_exc(),
            )
            yield {"type": "error", "error": f"{type(e).__name__}: {str(e)}"}

    def _get_tools_definition(self) -> List[Dict]:
        """获取所有工具的 LLM 定义"""
        self._refresh_tools()
        definitions = []
        for tname, tool in self.tools.items():
            if hasattr(tool, "definition"):
                definitions.append(tool.definition)
            elif hasattr(tool, "__dict__") and "definition" in tool.__dict__:
                definitions.append(tool.definition)

        builtin_count = len(self._builtin_tools)
        comp_count = len(definitions) - builtin_count
        logger.info(
            "[AgentLoop] 发送工具定义 | 内置=%d | 组件=%d | 总计=%d",
            builtin_count, max(comp_count, 0), len(definitions),
        )
        return definitions

    def _cleanup_incomplete_tool_calls(self, memory):
        """移除不完整的 tool_call（没有对应 tool_result 的），保留其他内容"""
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
        logger.info("[AgentLoop] 清理 %d 个不完整 tool_call", len(incomplete_ids))
        cleaned = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                remaining_tcs = [tc for tc in msg.get("tool_calls", []) if tc.get("id") not in incomplete_ids]
                if remaining_tcs:
                    msg = {**msg, "tool_calls": remaining_tcs}
                    cleaned.append(msg)
                elif msg.get("content"):
                    cleaned.append({**msg, "tool_calls": None})
            else:
                cleaned.append(msg)
        memory.messages = cleaned

    def _persist_conversation(self, user_input, response_content=None, session_id=None, memory=None):
        """对话结束后持久化到三层记忆系统"""
        sid = session_id or self.session_id
        if not self.has_long_term_memory:
            return
        try:
            all_messages = memory.get_messages() if memory else None
            actual_response = response_content
            
            # 如果没有传入 response_content，从 memory 中查找
            if not actual_response and all_messages:
                for msg in reversed(all_messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        content = msg.get("content", "")
                        # 跳过纯 JSON 思考（工具调用前的 interim content）
                        if self._is_thinking_json(content):
                            continue
                        actual_response = content
                        break
            
            # 如果所有 assistant 消息都是 JSON 思考，取最后一条
            if not actual_response and all_messages:
                for msg in reversed(all_messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        actual_response = msg.get("content", "")
                        break
            
            if not actual_response:
                actual_response = "[无回复内容]" if user_input else ""
            if not actual_response and not all_messages:
                logger.debug("[AgentLoop] 无内容可持久化，跳过")
                return

            if self.flash_mode:
                source_id = self.three_layer_memory.persist_session(
                    user_input, actual_response, session_id=sid, messages=None,
                )
            else:
                source_id = self.three_layer_memory.persist_session(
                    user_input, actual_response, session_id=sid, messages=all_messages,
                )

            logger.info("[AgentLoop] 对话已持久化 | session=%s | archive_id=%s", sid, source_id[:12] if source_id else "N/A")
        except Exception as mem_e:
            logger.error("[AgentLoop] 对话持久化失败: %s", mem_e)
            try:
                self._event_publisher.publish_error(
                    session_id=sid,
                    error_type="MemoryPersistError",
                    error_message=str(mem_e),
                    traceback="",
                )
            except Exception as bus_e:
                logger.warning("[AgentLoop] 发布持久化错误事件失败: %s", bus_e)
