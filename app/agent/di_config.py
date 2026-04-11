# -*- coding: utf-8 -*-
"""
Agent 依赖注入容器初始化

将 core 的 DIContainer 用于 Agent 系统各模块的解耦管理。
注册的服务：
  - CelliumShell       → Shell 命令执行器
  - ThreeLayerMemory   → 三层记忆系统
  - MemoryManager      → 对话上下文记忆
  - SecurityPolicy     → 安全策略引擎（命令黑名单/白名单/风险等级）
  - AgentLoop          → Agent 主循环（需 LLM 引擎）
  - ShellTool          → Shell 工具
"""

import logging
from typing import Optional
from app.core.di.container import (
    get_container,
    DIContainer,
)
from app.core.bus.event_bus import get_event_bus, EventBus
from app.agent.shell.cellium_shell import CelliumShell
from app.agent.loop.memory import MemoryManager
from app.agent.loop.agent_loop import AgentLoop
from app.agent.memory.three_layer import ThreeLayerMemory
from app.agent.security.policy import SecurityPolicy
from app.agent.tools.shell_tool import ShellTool
from app.agent.tools.memory_tool import MemoryTool
from app.agent.tools.file_tool import FileTool
from app.agent.llm.engine import BaseLLMEngine, create_llm_engine
from app.core.util.agent_config import get_config

logger = logging.getLogger(__name__)


def setup_agent_di(
    llm_engine=None,
    shell=None,
    memory_dir: Optional[str] = None,   # None 表示从 memory.yaml 读取
    max_iterations: Optional[int] = None,  # None 表示从 agent.yaml 读取
    container: DIContainer = None,
) -> DIContainer:
    """
    初始化 Agent 系统的 DI 容器

    Args:
        llm_engine: LLM 引擎实例（可选，不传则 AgentLoop 暂不可用）
        shell: 自定义 CelliumShell 实例（可选，默认新建）
        memory_dir: 记忆系统目录
        max_iterations: Agent 最大迭代次数（None 则从 agent.yaml 读取）
        container: 外部传入的 DI 容器（可选，默认使用全局单例）

    Returns:
        配置好的 DI 容器实例
    """
    if container is None:
        container = get_container()

    # ★ 从配置读取默认参数
    _cfg = get_config()
    if max_iterations is None:
        max_iterations = int(_cfg.get("agent.max_iterations", 10))
    flash_mode = _cfg.get("agent.flash_mode", False)  # Flash 模式
    if memory_dir is None:
        memory_dir = _cfg.get("memory.memory_dir", "memory")

    # --- 1. 注册 EventBus ---
    if not container.has(EventBus):
        container.register(EventBus, get_event_bus(), singleton=True)

    # --- 2. 创建/注册 LLM 引擎---
    if llm_engine is None:
        try:
            llm_engine = create_llm_engine()
            logger.info("[AgentDI] LLM 引擎已从配置创建 (model=%s)", getattr(llm_engine, 'model', '?'))
        except Exception as e:
            logger.warning("[AgentDI] LLM 引擎创建失败，AgentLoop 将不可用: %s", e)
            llm_engine = None

    if llm_engine is not None and not hasattr(BaseLLMEngine, '_di_registered'):
        container.register(BaseLLMEngine, llm_engine, singleton=True)
        BaseLLMEngine._di_registered = True

    # --- 2. 注册安全策略（先于Shell，因为Shell依赖它）---
    _security = SecurityPolicy()
    if not hasattr(SecurityPolicy, '_di_registered'):
        container.register(SecurityPolicy, _security, singleton=True)
        SecurityPolicy._di_registered = True

    # --- 3. 注册 Shell（注入 SecurityPolicy）---
    _shell = shell or CelliumShell(security_policy=_security)
    if not hasattr(CelliumShell, '_di_registered'):
        container.register(CelliumShell, _shell, singleton=True)
        CelliumShell._di_registered = True

    # --- 4. 注册三层记忆 ---
    _memory = ThreeLayerMemory(memory_dir)
    if not hasattr(ThreeLayerMemory, '_di_registered'):
        container.register(ThreeLayerMemory, _memory, singleton=True)
        ThreeLayerMemory._di_registered = True

    # --- 5. 注册对话上下文 MemoryManager ---
    # ★ 从 memory.yaml 读取配置
    memory_cfg = _cfg.get_section("memory") or {}
    short_term = memory_cfg.get("short_term", {})
    _mem_mgr = MemoryManager(
        max_history=short_term.get("max_history", 50),
        max_tool_results=short_term.get("max_tool_results", 10),
        max_tool_result_length=short_term.get("max_tool_result_length", 500),
        auto_compact_threshold=short_term.get("auto_compact_threshold", 10000),
    )
    if not hasattr(MemoryManager, '_di_registered'):
        container.register(MemoryManager, _mem_mgr, singleton=False)  # 每次会话新实例
        MemoryManager._di_registered = True

    # ★ 注册 memory 配置热重载回调
    def _on_memory_config_change(section, old_val, new_val):
        """memory 配置变更时更新所有活跃的 MemoryManager"""
        if section != "memory":
            return
        from app.agent.loop.session_manager import get_session_manager
        session_mgr = get_session_manager()
        short_term_new = (new_val or {}).get("short_term", {})
        session_mgr.update_all_memory_configs(short_term_new)

    _cfg.on_change("memory", _on_memory_config_change)

    # --- 6. 获取多进程管理器（用于 ShellTool 防阻塞）---
    try:
        from app.core.util.mp_manager import get_multiprocess_manager as get_mp
        _mp_manager = get_mp()
    except Exception:
        _mp_manager = None

    # --- 7. 注册 ShellTool（注入 MultiprocessManager）---
    _tool = ShellTool(
        shell=_shell,
        mp_manager=_mp_manager,
    )
    if not hasattr(ShellTool, '_di_registered'):
        container.register(ShellTool, _tool, singleton=True)
        ShellTool._di_registered = True

    # --- 8b. 注册 MemoryTool（注入 ThreeLayerMemory，让 LLM 可主动读写长期记忆）---
    _mem_tool = MemoryTool(three_layer_memory=_memory)
    if not hasattr(MemoryTool, '_di_registered'):
        container.register(MemoryTool, _mem_tool, singleton=True)
        MemoryTool._di_registered = True

    # --- 8c. 注册 FileTool（专用文件读写工具，替代不可靠的 shell 文件命令）---
    _file_tool = FileTool()
    if not hasattr(FileTool, '_di_registered'):
        container.register(FileTool, _file_tool, singleton=True)
        FileTool._di_registered = True

    # --- 9. 注册 AgentLoop---
    def _create_agent_loop():
        # 从 DI 容器获取当前的 LLM 引擎（支持热重载）
        current_llm = container.resolve(BaseLLMEngine) if container.has(BaseLLMEngine) else llm_engine
        loop = AgentLoop(
            llm_engine=current_llm,
            shell=_shell,
            tools={
                "shell": _tool,
                "memory": _mem_tool,   # 记忆工具
                "file": _file_tool,     # 文件工具
            },
            max_iterations=max_iterations,
            three_layer_memory=_memory,   # 注入三层记忆
            flash_mode=flash_mode,        # Flash 模式配置
            enable_heuristics=True,       # 启用启发式引擎
            enable_learning=True,         # 启用学习模块
        )
        return loop

    if not hasattr(AgentLoop, '_di_registered'):
        container.register_factory(AgentLoop, _create_agent_loop)
        AgentLoop._di_registered = True

    logger.info("[AgentDI] 依赖注入容器初始化完成 (LLM=%s)", "OK" if llm_engine else "None")

    return container


def resolve_agent_services(container: DIContainer = None):
    """
    从 DI 容器解析所有 Agent 服务

    Returns:
        dict: {shell, memory, agent_loop, security, shell_tool}
    """
    if container is None:
        container = get_container()

    return {
        "shell": container.resolve(CelliumShell),
        "memory": container.resolve(ThreeLayerMemory),
        "agent_loop": container.resolve(AgentLoop),
        "shell_tool": container.resolve(ShellTool),
        "memory_tool": container.resolve(MemoryTool) if container.has(MemoryTool) else None,
        "file_tool": container.resolve(FileTool) if container.has(FileTool) else None,
        "security": container.resolve(SecurityPolicy),
        "llm_engine": container.resolve(BaseLLMEngine) if container.has(BaseLLMEngine) else None,
    }
