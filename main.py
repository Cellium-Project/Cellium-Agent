# -*- coding: utf-8 -*-
"""
Cellium Agent - 主入口
"""

import os
import sys
import uvicorn
import logging
from uvicorn.logging import AccessFormatter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.util.logger import setup_logger, LogMixin, install_buffer
from app.core.di.container import setup_di_container
from app.agent.di_config import setup_agent_di
from app.agent.loop.session_manager import init_session_manager
from app.core.util.agent_config import get_config


class FailureOnlyAccessFormatter(AccessFormatter):
    def formatMessage(self, record: logging.LogRecord) -> str:
        return "" if record.status_code < 400 else super().formatMessage(record)


def setup_uvicorn_logging():
    from uvicorn.config import LOGGING_CONFIG
    import copy
    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config["formatters"]["access"] = {"()": FailureOnlyAccessFormatter, "fmt": '%(h)s - "%(r)s" %(status_code)s %(client)s %(took)sms'}
    log_config["handlers"]["access"]["level"] = "WARNING"
    return log_config


class MainApplication(LogMixin):
    def __init__(self):
        self.container = None
        self.app = None

    def run(self, host: str = None, port: int = None):
        cfg = get_config()
        _host = host or cfg.get("server.host", "127.0.0.1")
        _port = port or cfg.get("server.port", 8000)

        self._setup_logging(cfg)
        self._setup_containers(cfg)
        self._setup_session_manager(cfg)
        self._setup_components()
        self._setup_watcher()
        self._setup_web_app()
        self._print_event_system_info()

        self.logger.info("=" * 50)
        self.logger.info("服务已启动: http://%s:%d", _host, _port)
        self.logger.info("API 文档: http://%s:%d/docs", _host, _port)
        self.logger.info("=" * 50)

        uvicorn.run(self.app, host=_host, port=_port, log_config=setup_uvicorn_logging())

    def _setup_logging(self, cfg):
        log_level = cfg.get("logging.level", "INFO")
        buf_size = max(100, int(cfg.get("logging.max_size", 5000)))
        setup_logger("app", level=log_level)
        install_buffer(max_size=buf_size)
        self.logger.info("[OK] 日志系统初始化完成（level=%s, buffer=%s）", log_level, buf_size)

    def _setup_containers(self, cfg):
        setup_di_container()
        self.logger.info("[OK] Core DI 容器初始化完成")

        mem_dir = cfg.get("memory.memory_dir", "memory") or os.path.join(os.path.dirname(__file__), "memory")
        self.container = setup_agent_di(memory_dir=mem_dir)
        self.logger.info("[OK] Agent DI 容器初始化完成")

    def _setup_session_manager(self, cfg):
        from app.agent.memory.three_layer import ThreeLayerMemory
        from app.core.di.container import get_container

        di = get_container()
        tlm = di.resolve(ThreeLayerMemory) if di.has(ThreeLayerMemory) else None

        init_session_manager(
            timeout=cfg.get("agent.request_timeout", 86400),
            max_sessions=cfg.get("agent.max_sessions", 100),
            three_layer_memory=tlm,
        )
        self.logger.info("[OK] 会话管理器初始化完成")

    def _setup_components(self):
        from app.core.util.components_loader import load_components, get_all_commands

        loaded = load_components(container=self.container, auto_discover=True, auto_register=True)
        cmd_summary = get_all_commands()
        total_cmds = sum(len(cmds) for cmds in cmd_summary.values())

        self.logger.info("[OK] 组件系统就绪: %d 个组件, %d 条命令", len(loaded), total_cmds)
        for cell_name, cmds in cmd_summary.items():
            self.logger.info("  [Component] %s → %s", cell_name, list(cmds.keys()))

    def _setup_watcher(self):
        from app.core.util.component_watcher import start_watching

        watcher = start_watching(interval=3.0)
        status = watcher.status()
        self.logger.info("[OK] 热插拔监控已启动 | watching=%d files", status.get("watched_files", 0))

        if status.get("tool_count", 0) > 0:
            self.logger.info("  [HotPlug] %d 个工具已注册", status["tool_count"])

    def _setup_web_app(self):
        from app.server.web_server import create_app
        self.app = create_app()
        self.logger.info("[OK] FastAPI 应用创建完成")

    def _print_event_system_info(self):
        from app.core.bus.event_bus import event_bus
        from app.agent.events.event_types import AgentEventType

        self.logger.info("[EVENT] 事件总线就绪:")
        for et in AgentEventType:
            count = event_bus.get_subscribers_count(et)
            marker = "[ACTIVE]" if count > 0 else "       "
            self.logger.info("  %s %s (%d subscribers)", marker, et.value, count)


def main():
    MainApplication().run()


if __name__ == "__main__":
    main()
