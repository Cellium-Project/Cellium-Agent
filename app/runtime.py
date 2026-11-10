# -*- coding: utf-8 -*-
"""
组合根 — 装配 DI、Agent、Channel 与 Web 应用
"""

import logging
import os

logger = logging.getLogger("app")


class _TaskPort:
    async def start_task(self, **kwargs):
        from app.server.task_manager import get_task_manager
        return await get_task_manager().start_task(**kwargs)

    def has_running_task(self, session_id):
        from app.server.task_manager import get_task_manager
        return get_task_manager().has_running_task(session_id)

    def get_task_info(self, session_id):
        from app.server.task_manager import get_task_manager
        return get_task_manager().get_task_info(session_id)


class _SessionPort:
    def get_or_create(self, session_id):
        from app.agent.loop.session_manager import get_session_manager
        return get_session_manager().get_or_create(session_id)


class _ChannelPort:
    def get_adapter(self, platform):
        from app.channels import ChannelManager
        return ChannelManager.get_instance().get_adapter(platform)

    def start_scheduler_task_queue_consumer(self, platform_context, session_id, queue, task_info):
        from app.channels import ChannelManager
        return ChannelManager.get_instance().start_scheduler_task_queue_consumer(
            platform_context, session_id, queue, task_info
        )


class _DelegatedPort:
    def is_delegated(self, session_id):
        from components.session_agent import is_delegated
        return is_delegated(session_id)


def setup_containers(cfg):
    from app.core.di.container import setup_di_container
    from app.agent.di_config import setup_agent_di, resolve_agent_services
    from app.agent.loop import AgentLoopManager
    from app.channels import ChannelManager

    setup_di_container()
    logger.info("[OK] Core DI 容器初始化完成")

    from app.core.util.runtime_paths import resolve_dir_writable
    mem_dir = cfg.get("memory.memory_dir", "") or "memory"
    if not os.path.isabs(mem_dir):
        mem_dir = os.path.join(resolve_dir_writable(), mem_dir)
    container = setup_agent_di(memory_dir=mem_dir)
    logger.info("[OK] Agent DI 容器初始化完成")

    agent_loop_mgr = AgentLoopManager.get_instance()
    services = resolve_agent_services(container)

    enforce_limit = cfg.get("agent.enforce_iteration_limit", False)
    default_iter = cfg.get("agent.max_iterations", 10)
    max_iter = default_iter if enforce_limit else float('inf')

    agent_cfg = {
        "max_iterations": max_iter,
        "tool_timeout": cfg.get("agent.tool_timeout", 900),
        "flash_mode": cfg.get("agent.flash_mode", False),
        "enable_heuristics": True,
        "enable_learning": True,
    }
    agent_loop_mgr.initialize(
        llm_engine=services["llm_engine"],
        shell=services["shell"],
        three_layer_memory=services["memory"],
        tools={
            "shell": services["shell_tool"],
            "memory": services["memory_tool"],
            "file": services["file_tool"],
            "read": services["read_tool"],
            "edit": services["edit_tool"],
            "grep": services["grep_tool"],
            "glob": services["glob_tool"],
            "ls": services["ls_tool"],
            "config": services["config_tool"],
        },
        global_config=agent_cfg,
    )
    channel_mgr = ChannelManager.get_instance()
    channel_mgr.set_agent_loop_manager(agent_loop_mgr)

    from app.core.ports import bind_ports
    bind_ports(
        task=_TaskPort(),
        session=_SessionPort(),
        channel=_ChannelPort(),
        memory=agent_loop_mgr,
        delegated=_DelegatedPort(),
    )
    logger.info("[OK] AgentLoopManager + ChannelManager 集成完成")
    return container


def setup_session_manager(cfg):
    from app.agent.memory.three_layer import ThreeLayerMemory
    from app.core.di.container import get_container
    from app.agent.loop.session_manager import init_session_manager

    di = get_container()
    tlm = di.resolve(ThreeLayerMemory) if di.has(ThreeLayerMemory) else None

    init_session_manager(
        timeout=cfg.get("agent.request_timeout", 86400),
        max_sessions=cfg.get("agent.max_sessions", 100),
        three_layer_memory=tlm,
    )
    logger.info("[OK] 会话管理器初始化完成")


def create_web_app(auto_open_browser: bool = True):
    from app.server.web_server import create_app

    app = create_app(auto_open_browser=auto_open_browser)
    logger.info("[OK] FastAPI 应用创建完成")
    return app


def print_event_system_info():
    from app.core.bus.event_bus import event_bus
    from app.agent.events.event_types import AgentEventType

    logger.info("[EVENT] 事件总线就绪:")
    for et in AgentEventType:
        count = event_bus.get_subscribers_count(et)
        marker = "[ACTIVE]" if count > 0 else "       "
        logger.info("  %s %s (%d subscribers)", marker, et.value, count)
