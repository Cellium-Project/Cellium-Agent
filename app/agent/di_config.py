# -*- coding: utf-8 -*-
"""
Agent 依赖注入容器初始化

将 core 的 DIContainer 用于 Agent 系统无状态单例服务的解耦管理。
注册的服务：
  - CelliumShell       → Shell 命令执行器
  - ThreeLayerMemory   → 三层记忆系统
  - MemoryManager      → 对话上下文记忆
  - SecurityPolicy     → 安全策略引擎
  - ShellTool/MemoryTool/FileTool/ReadTool/EditTool/GrepTool/GlobTool/LSTool/ConfigTool
  - BaseLLMEngine      → LLM 引擎（热重载时重建）

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
from app.agent.tools.config_tool import ConfigTool
from app.agent.llm.engine import BaseLLMEngine, create_llm_engine
from app.core.util.agent_config import get_config

logger = logging.getLogger(__name__)

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def bind_main_loop(loop: asyncio.AbstractEventLoop):
    global _main_loop
    _main_loop = loop


def _schedule_async(coro):
    if _main_loop is not None and _main_loop.is_running():
        _main_loop.call_soon_threadsafe(lambda: asyncio.ensure_future(coro, loop=_main_loop))
        return
    try:
        running = asyncio.get_running_loop()
        if running.is_running():
            running.create_task(coro)
            return
    except RuntimeError:
        pass
    try:
        asyncio.run(coro)
    except RuntimeError:
        logger.warning("[AgentDI] 无法调度异步任务（无可用事件循环），协程被丢弃")


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

    global _main_loop
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中事件循环：置 None，由 _schedule_async 回退到 asyncio.run
        _main_loop = None
    logger.debug("[AgentDI] 主事件循环已捕获: %s", _main_loop)

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

    def _on_llm_config_change(section, old_val, new_val):
        """llm 配置变更时重建 LLM 引擎并推送"""
        if section != "llm":
            return
        try:
            from app.agent.llm.engine import BaseLLMEngine, create_llm_engine
            old_engine = None
            if container.has(BaseLLMEngine):
                old_engine = container.resolve(BaseLLMEngine)
            new_engine = create_llm_engine(new_val)
            container.register(BaseLLMEngine, new_engine, singleton=True)
            if old_engine is not None and old_engine is not new_engine:
                _schedule_async(old_engine.close())
            logger.info("[AgentDI] LLM 引擎已热重建 | model=%s", new_engine.model)

            from app.agent.loop import AgentLoopManager
            mgr = AgentLoopManager.get_instance()
            updated = mgr.update_llm_engine(new_engine)
            logger.info("[AgentDI] LLM 引擎已推送到 %d 个活跃 loop", updated)
        except Exception as e:
            logger.error("[AgentDI] LLM 热重建失败: %s", e, exc_info=True)

    _cfg.on_change("llm", _on_llm_config_change)

    def _on_heuristics_config_change(section, old_val, new_val):
        """heuristics 配置变更时重新加载 HeuristicEngine 和意图 LLM"""
        if section != "heuristics":
            return
        try:
            from app.agent.heuristics.engine import get_heuristic_engine
            engine = get_heuristic_engine()
            engine.reload_config()
            logger.info("[AgentDI] Heuristics 配置已热更新")
        except Exception as e:
            logger.error("[AgentDI] Heuristics 配置热更新失败: %s", e, exc_info=True)

        intent_llm = None
        intent_enabled = True
        try:
            if new_val:
                intent_cfg = new_val.get("intent", {})
                intent_enabled = intent_cfg.get("enabled", True)
                if intent_enabled:
                    model_cfg = intent_cfg.get("model", {})
                    if model_cfg.get("api_key") and model_cfg.get("model"):
                        from app.agent.llm.engine import OpenAICompatibleEngine
                        intent_llm = OpenAICompatibleEngine(
                            api_key=model_cfg["api_key"],
                            base_url=model_cfg.get("base_url", "https://api.openai.com/v1"),
                            model=model_cfg["model"],
                            temperature=float(model_cfg.get("temperature", 0.3)),
                            max_tokens=10,
                            timeout=int(model_cfg.get("timeout", 30)),
                            verify_model=False,
                        )
                        logger.info("[AgentDI] 意图 LLM 已热重建 | model=%s", intent_llm.model)
                else:
                    logger.info("[AgentDI] 意图感知已关闭，推送禁用信号")
        except Exception as e:
            logger.warning("[AgentDI] 意图 LLM 热重建失败: %s", e)

        try:
            from app.agent.loop import AgentLoopManager
            mgr = AgentLoopManager.get_instance()
            mgr.update_all_loops(intent_llm=intent_llm, intent_enabled=intent_enabled)
            logger.info("[AgentDI] 意图配置已推送到所有活跃 loop (enabled=%s)", intent_enabled)
        except Exception as e:
            logger.warning("[AgentDI] 推送意图配置失败: %s", e)

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
                _schedule_async(_do_channel_reconnect(adapter))
                logger.info("[AgentDI] QQ 通道配置已热更新，正在重连...")
            elif qq_config.should_auto_start():
                _schedule_async(_do_channel_start(channel_mgr, qq_config))
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
                _schedule_async(_do_channel_reconnect(tg_adapter))
                logger.info("[AgentDI] Telegram 通道配置已热更新，正在重连...")
            elif tg_config.should_auto_start():
                _schedule_async(_do_telegram_channel_start(channel_mgr, tg_config))
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
                _schedule_async(
                    feishu_adapter.update_config(
                        app_id=new_app_id,
                        app_secret=new_app_secret,
                        whitelist_users=new_whitelist,
                    )
                )
                logger.info("[AgentDI] 飞书通道配置已热更新，正在重连...")
            elif feishu_config.should_auto_start():
                _schedule_async(_do_feishu_channel_start(channel_mgr, feishu_config))
                logger.info("[AgentDI] 飞书通道配置已热更新，正在启动...")
            else:
                logger.warning("[AgentDI] 飞书通道配置已更新，但凭证缺失或未启用")
        except Exception as e:
            logger.error("[AgentDI] Channels 配置热更新失败: %s", e, exc_info=True)

    _cfg.on_change("channels", _on_channels_config_change)

    # --- 注册 EventBus ---
    if not container.has(EventBus):
        container.register(EventBus, get_event_bus(), singleton=True)

    # --- 创建/注册 LLM 引擎 ---
    if llm_engine is None:
        try:
            llm_engine = create_llm_engine()
            logger.info("[AgentDI] LLM 引擎已从配置创建 (model=%s)", getattr(llm_engine, 'model', '?'))
        except Exception as e:
            logger.warning(
                "[AgentDI] LLM 引擎创建失败，启动降级模式 | 原因: %s | "
                "请检查 config/agent/llm.yaml 中的 api_key/base_url/model 配置",
                e,
                exc_info=True,
            )
            llm_engine = None

    if llm_engine is not None and not container.has(BaseLLMEngine):
        container.register(BaseLLMEngine, llm_engine, singleton=True)

    # --- 注册安全策略---
    _security_cfg = _cfg.get_section("security") or {}
    _security = SecurityPolicy(
        permission_level=_security_cfg.get("permission_level", "standard"),
    )
    if not container.has(SecurityPolicy):
        container.register(SecurityPolicy, _security, singleton=True)

    # --- 注册 Shell（注入 SecurityPolicy）---
    agent_cfg = _cfg.get_section("agent") or {}
    shell_cwd = agent_cfg.get("shell_cwd", "") or None
    if shell_cwd and not os.path.isabs(shell_cwd):
        shell_cwd = os.path.join(get_config().config_root, shell_cwd)
    _shell = shell or CelliumShell(security_policy=_security, initial_cwd=shell_cwd)
    if not container.has(CelliumShell):
        container.register(CelliumShell, _shell, singleton=True)

    # --- 注册三层记忆 ---
    _memory = ThreeLayerMemory(memory_dir, allow_sensitive_store=allow_sensitive_store)
    if not container.has(ThreeLayerMemory):
        container.register(ThreeLayerMemory, _memory, singleton=True)

    # --- 注册对话上下文 MemoryManager ---
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

    # --- 注册 ShellTool（注入 Shell）---
    _tool = ShellTool(shell=_shell)
    if not container.has(ShellTool):
        container.register(ShellTool, _tool, singleton=True)

    # --- 注册 MemoryTool（注入 ThreeLayerMemory）---
    _mem_tool = MemoryTool(three_layer_memory=_memory)
    if not container.has(MemoryTool):
        container.register(MemoryTool, _mem_tool, singleton=True)

    # --- 注册 FileTool ---
    _file_tool = FileTool()
    if not container.has(FileTool):
        container.register(FileTool, _file_tool, singleton=True)

    # --- 注册 ReadTool ---
    _read_tool = ReadTool()
    if not container.has(ReadTool):
        container.register(ReadTool, _read_tool, singleton=True)

    # --- 注册 EditTool ---
    _edit_tool = EditTool()
    if not container.has(EditTool):
        container.register(EditTool, _edit_tool, singleton=True)

    # --- 注册 GrepTool ---
    _grep_tool = GrepTool()
    if not container.has(GrepTool):
        container.register(GrepTool, _grep_tool, singleton=True)

    # --- 注册 GlobTool ---
    _glob_tool = GlobTool()
    if not container.has(GlobTool):
        container.register(GlobTool, _glob_tool, singleton=True)

    # --- 注册 LSTool ---
    _ls_tool = LSTool()
    if not container.has(LSTool):
        container.register(LSTool, _ls_tool, singleton=True)

    # --- 注册 ConfigTool ---
    _config_tool = ConfigTool()
    if not container.has(ConfigTool):
        container.register(ConfigTool, _config_tool, singleton=True)

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
        "shell_tool": container.resolve(ShellTool),
        "memory_tool": container.resolve(MemoryTool) if container.has(MemoryTool) else None,
        "file_tool": container.resolve(FileTool) if container.has(FileTool) else None,
        "read_tool": container.resolve(ReadTool) if container.has(ReadTool) else None,
        "edit_tool": container.resolve(EditTool) if container.has(EditTool) else None,
        "grep_tool": container.resolve(GrepTool) if container.has(GrepTool) else None,
        "glob_tool": container.resolve(GlobTool) if container.has(GlobTool) else None,
        "ls_tool": container.resolve(LSTool) if container.has(LSTool) else None,
        "config_tool": container.resolve(ConfigTool) if container.has(ConfigTool) else None,
        "security": container.resolve(SecurityPolicy),
        "llm_engine": container.resolve(BaseLLMEngine) if container.has(BaseLLMEngine) else None,
    }
