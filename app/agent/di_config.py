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

import asyncio
import logging
import os
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
from app.agent.tools.read_tool import ReadTool
from app.agent.tools.edit_tool import EditTool
from app.agent.tools.grep_tool import GrepTool
from app.agent.tools.glob_tool import GlobTool
from app.agent.tools.ls_tool import LSTool
from app.agent.llm.engine import BaseLLMEngine, create_llm_engine
from app.core.util.agent_config import get_config

logger = logging.getLogger(__name__)


async def _do_channel_reconnect(adapter):
    """触发通道重连（模块级函数供回调使用）"""
    try:
        await adapter.disconnect()
        await asyncio.sleep(0.5)
        await adapter.connect()
        logger.info("[AgentDI] 通道重连完成")
    except Exception as e:
        logger.error("[AgentDI] 通道重连失败: %s", e)


async def _do_channel_start(channel_mgr, qq_config):
    """启动新通道"""
    try:
        from app.channels.qq import QQAdapter
        adapter = QQAdapter(
            app_id=qq_config.get_app_id(),
            app_secret=qq_config.get_app_secret(),
            intents=qq_config._intents,
        )
        await channel_mgr.register_adapter(adapter)
        await adapter.connect()
        logger.info("[AgentDI] 通道启动完成")
    except Exception as e:
        logger.error("[AgentDI] 通道启动失败: %s", e)


async def _do_telegram_channel_start(channel_mgr, tg_config):
    """启动 Telegram 通道"""
    try:
        from app.channels.telegram import TelegramAdapter
        adapter = TelegramAdapter(
            bot_token=tg_config.get_bot_token(),
            whitelist_user_ids=tg_config.get_whitelist_user_ids(),
            whitelist_usernames=tg_config.get_whitelist_usernames(),
        )
        await channel_mgr.register_adapter(adapter)
        await adapter.connect()
        logger.info("[AgentDI] Telegram 通道启动完成")
    except Exception as e:
        logger.error("[AgentDI] Telegram 通道启动失败: %s", e)


async def _do_feishu_channel_start(channel_mgr, feishu_config):
    """启动飞书通道"""
    try:
        from app.channels.feishu import FeishuAdapter
        adapter = FeishuAdapter(config=feishu_config)
        channel_mgr.register_adapter(adapter)
        await adapter.connect()
        logger.info("[AgentDI] 飞书通道启动完成")
    except Exception as e:
        logger.error("[AgentDI] 飞书通道启动失败: %s", e)


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

    _cfg = get_config()

    _enforce_limit = _cfg.get("agent.enforce_iteration_limit", False)
    if max_iterations is None:
        _default = _cfg.get("agent.max_iterations", 10)
        max_iterations = _default if _enforce_limit else float('inf')
    flash_mode = _cfg.get("agent.flash_mode", False)
    if memory_dir is None:
        memory_dir = _cfg.get("memory.memory_dir", "memory")
    allow_sensitive_store = _cfg.get("memory.allow_sensitive_store", False)

    _client_logger = logging.getLogger("app.client")
    _client_logger.setLevel(logging.DEBUG if _cfg.get("logging.client_log", False) else logging.CRITICAL + 1)

    _agent_config_holder = {
        "max_iterations": max_iterations,
        "flash_mode": flash_mode,
        "enable_heuristics": True,
        "enable_learning": _cfg.get("learning.enabled", True),
    }

    def _update_all_loops():
        """热更新所有活跃 loop 的配置"""
        try:
            from app.agent.loop import AgentLoopManager
            mgr = AgentLoopManager.get_instance()
            mgr.update_all_loops(
                flash_mode=_agent_config_holder.get("flash_mode", False),
                max_iterations=_agent_config_holder['max_iterations'] if _agent_config_holder['max_iterations'] != float('inf') else None,
            )
        except Exception as e:
            logger.warning(f"[AgentDI] 热更新 loop 失败: {e}")

    def _on_agent_config_change(section, old_val, new_val):
        """agent 配置变更时更新 _agent_config_holder"""
        if section != "agent":
            return
        try:
            enforce = new_val.get("enforce_iteration_limit", False) if new_val else False
            default_iter = new_val.get("max_iterations", 10) if new_val else 10
            _agent_config_holder["max_iterations"] = default_iter if enforce else float('inf')
            _agent_config_holder["flash_mode"] = new_val.get("flash_mode", False) if new_val else False
            shell_cwd = new_val.get("shell_cwd", "") if new_val else ""
            if shell_cwd:
                if not os.path.isabs(shell_cwd):
                    shell_cwd = os.path.join(get_config().config_root, shell_cwd)
                if os.path.isdir(shell_cwd):
                    from app.agent.shell.cellium_shell import CelliumShell
                    shell = container.resolve(CelliumShell) if container.has(CelliumShell) else None
                    if shell:
                        shell._cwd = shell_cwd
                        logger.info("[AgentDI] Agent 配置已热更新 | max_iterations=%s | flash_mode=%s | shell_cwd=%s",
                                   _agent_config_holder["max_iterations"], _agent_config_holder["flash_mode"], shell_cwd)
                    else:
                        logger.info("[AgentDI] Agent 配置已热更新 | max_iterations=%s | flash_mode=%s | shell_cwd=%s (Shell未初始化)",
                                   _agent_config_holder["max_iterations"], _agent_config_holder["flash_mode"], shell_cwd)
                else:
                    logger.info("[AgentDI] Agent 配置已热更新 | max_iterations=%s | flash_mode=%s",
                               _agent_config_holder["max_iterations"], _agent_config_holder["flash_mode"])
            else:
                logger.info("[AgentDI] Agent 配置已热更新 | max_iterations=%s | flash_mode=%s",
                           _agent_config_holder["max_iterations"], _agent_config_holder["flash_mode"])
            _update_all_loops()
        except Exception as e:
            logger.error("[AgentDI] Agent 配置热更新失败: %s", e, exc_info=True)

    _cfg.on_change("agent", _on_agent_config_change)

    def _on_heuristics_config_change(section, old_val, new_val):
        """heuristics 配置变更时重新加载 HeuristicEngine"""
        if section != "heuristics":
            return
        try:
            from app.agent.heuristics.engine import get_heuristic_engine
            engine = get_heuristic_engine()
            engine.reload_config()
            logger.info("[AgentDI] Heuristics 配置已热更新")
        except Exception as e:
            logger.error("[AgentDI] Heuristics 配置热更新失败: %s", e, exc_info=True)

    _cfg.on_change("heuristics", _on_heuristics_config_change)

    def _on_learning_config_change(section, old_val, new_val):
        if section != "learning":
            return
        try:
            from app.agent.learning.policy import reload_templates
            reload_templates()
            if new_val:
                new_enabled = new_val.get("enabled", True)
                _agent_config_holder["enable_learning"] = new_enabled
                try:
                    from app.agent.loop import AgentLoopManager
                    mgr = AgentLoopManager.get_instance()
                    mgr.update_all_loops(
                        enable_learning=new_enabled,
                    )
                except Exception:
                    pass
                logger.info("[AgentDI] Learning 配置已热更新 | enabled=%s", new_enabled)
        except Exception as e:
            logger.error("[AgentDI] Learning 配置热更新失败: %s", e, exc_info=True)

    _cfg.on_change("learning", _on_learning_config_change)

    def _on_security_config_change(section, old_val, new_val):
        if section != "security":
            return
        try:
            if container.has(SecurityPolicy):
                security = container.resolve(SecurityPolicy)
                security.reload_blacklist()
                if new_val:
                    new_perm = new_val.get("permission_level")
                    if new_perm:
                        security.permission_level = new_perm
                    forbidden_dirs = new_val.get("forbidden_dirs", [])
                    if forbidden_dirs:
                        security.set_forbidden_dirs(forbidden_dirs)
                logger.info("[AgentDI] Security 配置已热更新 | permission_level=%s", security.permission_level)
        except Exception as e:
            logger.error("[AgentDI] Security 配置热更新失败: %s", e, exc_info=True)

    _cfg.on_change("security", _on_security_config_change)

    def _on_logging_config_change(section, old_val, new_val):
        """logging 配置变更时动态调整日志级别"""
        if section != "logging":
            return
        try:
            if new_val:
                level_str = new_val.get("level", "INFO").upper()
                level_map = {
                    "DEBUG": logging.DEBUG,
                    "INFO": logging.INFO,
                    "WARNING": logging.WARNING,
                    "ERROR": logging.ERROR,
                    "CRITICAL": logging.CRITICAL,
                }
                new_level = level_map.get(level_str, logging.INFO)
                root_logger = logging.getLogger()
                root_logger.setLevel(new_level)
                for handler in root_logger.handlers:
                    handler.setLevel(new_level)

                client_log_enabled = new_val.get("client_log", False)
                _client_logger = logging.getLogger("app.client")
                _client_logger.setLevel(logging.DEBUG if client_log_enabled else logging.CRITICAL + 1)

                logger.info("[AgentDI] Logging 配置已热更新 | level=%s | client_log=%s", level_str, client_log_enabled)
        except Exception as e:
            logger.error("[AgentDI] Logging 配置热更新失败: %s", e, exc_info=True)

    _cfg.on_change("logging", _on_logging_config_change)

    def _on_channels_config_change(section, old_val, new_val):
        """channels 配置变更时重新加载通道配置并重连"""
        if section != "channels":
            return
        try:
            from app.channels.qq import QQChannelConfig
            from app.channels.telegram import TelegramChannelConfig
            from app.channels import ChannelManager
            channel_mgr = ChannelManager.get_instance()

            qq_config = QQChannelConfig()
            qq_config.reload()
            adapter = channel_mgr.get_adapter("qq")
            if adapter:
                adapter.app_id = qq_config.get_app_id(force_reload=True)
                adapter.app_secret = qq_config.get_app_secret(force_reload=True)
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(_do_channel_reconnect(adapter))
                )
                logger.info("[AgentDI] QQ 通道配置已热更新，正在重连...")
            elif qq_config.should_auto_start():
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(_do_channel_start(channel_mgr, qq_config))
                )
                logger.info("[AgentDI] QQ 通道配置已热更新，正在启动...")
            else:
                logger.warning("[AgentDI] QQ 通道配置已更新，但凭证缺失或未启用")

            tg_config = TelegramChannelConfig()
            tg_config.reload()
            tg_adapter = channel_mgr.get_adapter("telegram")
            if tg_adapter:
                tg_adapter.bot_token = tg_config.get_bot_token(force_reload=True)
                tg_adapter.whitelist_user_ids = set(tg_config.get_whitelist_user_ids(force_reload=True))
                tg_adapter.whitelist_usernames = set(u.lower() for u in tg_config.get_whitelist_usernames(force_reload=True))
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(_do_channel_reconnect(tg_adapter))
                )
                logger.info("[AgentDI] Telegram 通道配置已热更新，正在重连...")
            elif tg_config.should_auto_start():
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(_do_telegram_channel_start(channel_mgr, tg_config))
                )
                logger.info("[AgentDI] Telegram 通道配置已热更新，正在启动...")
            else:
                logger.warning("[AgentDI] Telegram 通道配置已更新，但凭证缺失或未启用")

            from app.channels.feishu import FeishuChannelConfig
            feishu_config = FeishuChannelConfig()
            feishu_config.reload()
            feishu_adapter = channel_mgr.get_adapter("feishu")
            if feishu_adapter:
                new_app_id = feishu_config.get_app_id(force_reload=True)
                new_app_secret = feishu_config.get_app_secret(force_reload=True)
                new_whitelist = feishu_config.get_whitelist_users(force_reload=True)
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        feishu_adapter.update_config(
                            app_id=new_app_id,
                            app_secret=new_app_secret,
                            whitelist_users=new_whitelist,
                        )
                    )
                )
                logger.info("[AgentDI] 飞书通道配置已热更新，正在重连...")
            elif feishu_config.should_auto_start():
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(_do_feishu_channel_start(channel_mgr, feishu_config))
                )
                logger.info("[AgentDI] 飞书通道配置已热更新，正在启动...")
            else:
                logger.warning("[AgentDI] 飞书通道配置已更新，但凭证缺失或未启用")
        except Exception as e:
            logger.error("[AgentDI] Channels 配置热更新失败: %s", e, exc_info=True)

    _cfg.on_change("channels", _on_channels_config_change)

    # --- 1. 注册 EventBus ---
    if not container.has(EventBus):
        container.register(EventBus, get_event_bus(), singleton=True)

    # --- 2. 创建/注册 LLM 引擎---
    if llm_engine is None:
        try:
            llm_engine = create_llm_engine()
            logger.info("[AgentDI] LLM 引擎已从配置创建 (model=%s)", getattr(llm_engine, 'model', '?'))
        except Exception as e:
            logger.error("[AgentDI] LLM 引擎创建失败，启动降级模式（可在 WebUI 配置后重载）: %s", e)
            llm_engine = None

    if llm_engine is not None and not container.has(BaseLLMEngine):
        container.register(BaseLLMEngine, llm_engine, singleton=True)

    # --- 2. 注册安全策略（先于Shell，因为Shell依赖它）---
    _security_cfg = _cfg.get_section("security") or {}
    _security = SecurityPolicy(
        permission_level=_security_cfg.get("permission_level", "standard"),
    )
    if not container.has(SecurityPolicy):
        container.register(SecurityPolicy, _security, singleton=True)

    # --- 3. 注册 Shell（注入 SecurityPolicy）---
    agent_cfg = _cfg.get_section("agent") or {}
    shell_cwd = agent_cfg.get("shell_cwd", "") or None
    if shell_cwd and not os.path.isabs(shell_cwd):
        shell_cwd = os.path.join(get_config().config_root, shell_cwd)
    _shell = shell or CelliumShell(security_policy=_security, initial_cwd=shell_cwd)
    if not container.has(CelliumShell):
        container.register(CelliumShell, _shell, singleton=True)

    # --- 4. 注册三层记忆 ---
    _memory = ThreeLayerMemory(memory_dir, allow_sensitive_store=allow_sensitive_store)
    if not container.has(ThreeLayerMemory):
        container.register(ThreeLayerMemory, _memory, singleton=True)

    # --- 5. 注册对话上下文 MemoryManager ---
    memory_cfg = _cfg.get_section("memory") or {}
    short_term = memory_cfg.get("short_term", {})
    _mem_mgr = MemoryManager(
        max_history=short_term.get("max_history", 50),
    )
    if not container.has(MemoryManager):
        container.register(MemoryManager, _mem_mgr, singleton=False)

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
    if not container.has(ShellTool):
        container.register(ShellTool, _tool, singleton=True)

    # --- 8b. 注册 MemoryTool（注入 ThreeLayerMemory，让 LLM 可主动读写长期记忆）---
    _mem_tool = MemoryTool(three_layer_memory=_memory)
    if not container.has(MemoryTool):
        container.register(MemoryTool, _mem_tool, singleton=True)

    # --- 8c. 注册 FileTool（专用文件读写工具，替代不可靠的 shell 文件命令）---
    _file_tool = FileTool()
    if not container.has(FileTool):
        container.register(FileTool, _file_tool, singleton=True)

    # --- 8d. 注册 ReadTool（文件读取工具）---
    _read_tool = ReadTool()
    if not container.has(ReadTool):
        container.register(ReadTool, _read_tool, singleton=True)

    # --- 8e. 注册 EditTool（文件编辑工具）---
    _edit_tool = EditTool()
    if not container.has(EditTool):
        container.register(EditTool, _edit_tool, singleton=True)

    # --- 8f. 注册 GrepTool（内容搜索工具）---
    _grep_tool = GrepTool()
    if not container.has(GrepTool):
        container.register(GrepTool, _grep_tool, singleton=True)

    # --- 8g. 注册 GlobTool（文件名模式匹配工具）---
    _glob_tool = GlobTool()
    if not container.has(GlobTool):
        container.register(GlobTool, _glob_tool, singleton=True)

    # --- 8h. 注册 LSTool（目录列表工具）---
    _ls_tool = LSTool()
    if not container.has(LSTool):
        container.register(LSTool, _ls_tool, singleton=True)

    # --- 9. 注册 AgentLoop---
    def _create_agent_loop():
        # 从 DI 容器获取当前的 LLM 引擎（支持热重载）
        current_llm = container.resolve(BaseLLMEngine) if container.has(BaseLLMEngine) else llm_engine
        loop = AgentLoop(
            llm_engine=current_llm,
            shell=_shell,
            tools={
                "shell": _tool,
                "memory": _mem_tool,
                "file": _file_tool,
                "read": _read_tool,
                "edit": _edit_tool,
                "grep": _grep_tool,
                "glob": _glob_tool,
                "ls": _ls_tool,
            },
            max_iterations=_agent_config_holder["max_iterations"],
            three_layer_memory=_memory,   # 注入三层记忆
            flash_mode=_agent_config_holder["flash_mode"],        # Flash 模式配置（支持热重载）
            enable_heuristics=_agent_config_holder["enable_heuristics"],
            enable_learning=_agent_config_holder["enable_learning"],
        )
        return loop

    if not container.has(AgentLoop):
        container.register_factory(AgentLoop, _create_agent_loop)

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
        "read_tool": container.resolve(ReadTool) if container.has(ReadTool) else None,
        "edit_tool": container.resolve(EditTool) if container.has(EditTool) else None,
        "grep_tool": container.resolve(GrepTool) if container.has(GrepTool) else None,
        "glob_tool": container.resolve(GlobTool) if container.has(GlobTool) else None,
        "ls_tool": container.resolve(LSTool) if container.has(LSTool) else None,
        "security": container.resolve(SecurityPolicy),
        "llm_engine": container.resolve(BaseLLMEngine) if container.has(BaseLLMEngine) else None,
    }
