# -*- coding: utf-8 -*-
"""
启动引导 — WebUI(main.py) 与 TUI 共用
"""

import copy
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any, Optional

from app.core.util.logger import setup_logger, install_buffer

logger = logging.getLogger("app")


@dataclass
class BootstrapResult:
    host: str
    port: int
    fastapi_app: Any = None
    container: Any = None


class FailureOnlyAccessFormatter(logging.Formatter):
    def formatMessage(self, record: logging.LogRecord) -> str:
        return "" if record.status_code < 400 else super().formatMessage(record)


def setup_uvicorn_logging():
    from uvicorn.config import LOGGING_CONFIG
    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config["formatters"]["access"] = {"()": FailureOnlyAccessFormatter, "fmt": '%(h)s - "%(r)s" %(status_code)s %(client)s %(took)sms'}
    log_config["handlers"]["access"]["level"] = "WARNING"
    return log_config


def _ensure_available_port(host: str, preferred_port: int) -> int:
    port = preferred_port
    max_attempts = 20

    for attempt in range(max_attempts):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.close()
            if port != preferred_port:
                logger.warning(f"端口 {preferred_port} 被占用，已自动切换到端口 {port}")
            return port
        except OSError:
            port = preferred_port + attempt + 1
            logger.debug(f"端口 {preferred_port + attempt} 被占用，尝试端口 {port}")

    import random
    random_port = random.randint(30000, 40000)
    logger.warning(f"无法找到可用端口，使用随机端口 {random_port}")
    return random_port


def _setup_logging(cfg):
    log_level = cfg.get("logging.level", "INFO")
    buf_size = max(100, int(cfg.get("logging.max_size", 5000)))
    setup_logger("app", level=log_level)
    install_buffer(max_size=buf_size)
    logger.info("[OK] 日志系统初始化完成（level=%s, buffer=%s）", log_level, buf_size)


def _setup_containers(cfg):
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
    logger.info("[OK] AgentLoopManager + ChannelManager 集成完成")
    return container


def _setup_session_manager(cfg):
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


def _setup_components(container):
    from app.core.util.components_loader import load_components, get_all_commands

    loaded = load_components(container=container, auto_discover=True, auto_register=True)
    cmd_summary = get_all_commands()
    total_cmds = sum(len(cmds) for cmds in cmd_summary.values())

    logger.info("[OK] 组件系统就绪: %d 个组件, %d 条命令", len(loaded), total_cmds)
    for cell_name, cmds in cmd_summary.items():
        logger.info("  [Component] %s → %s", cell_name, list(cmds.keys()))


def _setup_watcher():
    from app.core.util.component_watcher import start_watching

    watcher = start_watching(interval=3.0)
    status = watcher.status()
    logger.info("[OK] 热插拔监控已启动 | watching=%d files", status.get("watched_files", 0))

    if status.get("tool_count", 0) > 0:
        logger.info("  [HotPlug] %d 个工具已注册", status["tool_count"])


def _setup_config_watcher():
    from app.core.util.agent_config import get_config

    cfg = get_config()
    cfg.start_file_watch(interval=2.0)
    logger.info("[OK] 配置文件监听已启动 | interval=2.0s")


def _setup_web_app(auto_open_browser: bool = True):
    from app.server.web_server import create_app

    app = create_app(auto_open_browser=auto_open_browser)
    logger.info("[OK] FastAPI 应用创建完成")
    return app


def _print_event_system_info():
    from app.core.bus.event_bus import event_bus
    from app.agent.events.event_types import AgentEventType

    logger.info("[EVENT] 事件总线就绪:")
    for et in AgentEventType:
        count = event_bus.get_subscribers_count(et)
        marker = "[ACTIVE]" if count > 0 else "       "
        logger.info("  %s %s (%d subscribers)", marker, et.value, count)


def bootstrap_agent(host: str = None, port: int = None, enable_webapp: bool = True, auto_open_browser: bool = True) -> BootstrapResult:
    from app.core.util.agent_config import get_config

    cfg = get_config()
    _host = host or cfg.get("server.host", "127.0.0.1")
    _port = port or cfg.get("server.port", 18000)
    _port = _ensure_available_port(_host, _port)

    _setup_logging(cfg)
    container = _setup_containers(cfg)
    _setup_session_manager(cfg)
    _setup_components(container)
    _setup_watcher()
    _setup_config_watcher()
    app = _setup_web_app(auto_open_browser=auto_open_browser) if enable_webapp else None
    _print_event_system_info()

    logger.info("=" * 50)
    logger.info("服务已启动: http://%s:%d", _host, _port)
    logger.info("WebUI 入口: http://localhost:%d/", _port)
    logger.info("API 文档: http://%s:%d/docs", _host, _port)
    logger.info("=" * 50)

    return BootstrapResult(host=_host, port=_port, fastapi_app=app, container=container)
