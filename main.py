# -*- coding: utf-8 -*-
"""
Cellium Agent - 主入口
"""

import os
import sys
import asyncio
import uvicorn
import logging
from uvicorn.logging import AccessFormatter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.util.logger import setup_logger, LogMixin, install_buffer
from app.core.di.container import setup_di_container
from app.agent.di_config import setup_agent_di
from app.agent.loop.session_manager import init_session_manager
from app.agent.loop import AgentLoopManager
from app.agent.memory.three_layer import ThreeLayerMemory
from app.agent.shell.cellium_shell import CelliumShell
from app.agent.llm.engine import BaseLLMEngine
from app.channels import ChannelManager
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
        _port = port or cfg.get("server.port", 18000)
        _port = self._ensure_available_port(_host, _port)

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

        import threading

        def open_browser():
            import webbrowser
            try:
                webbrowser.open(f"http://{_host}:{_port}")
            except Exception:
                pass

        if sys.platform == "win32" or sys.platform == "darwin" or os.environ.get("DISPLAY"):
            threading.Thread(target=open_browser, daemon=True).start()

        uvicorn.run(self.app, host=_host, port=_port, log_config=setup_uvicorn_logging())

    def _ensure_available_port(self, host: str, preferred_port: int) -> int:
        """确保端口可用，如果被占用则自动切换"""
        import socket

        port = preferred_port
        max_attempts = 10

        for attempt in range(max_attempts):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                sock.close()
                if port != preferred_port:
                    self.logger.warning(f"端口 {preferred_port} 被占用，已自动切换到端口 {port}")
                return port
            except OSError:
                port = preferred_port + attempt + 1
                self.logger.debug(f"端口 {preferred_port + attempt} 被占用，尝试端口 {port}")

        import random
        random_port = random.randint(30000, 40000)
        self.logger.warning(f"无法找到可用端口，使用随机端口 {random_port}")
        return random_port

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

        agent_loop_mgr = AgentLoopManager.get_instance()

        from app.agent.tools.shell_tool import ShellTool
        from app.agent.tools.memory_tool import MemoryTool
        from app.agent.tools.file_tool import FileTool
        _mem = self.container.resolve(ThreeLayerMemory)
        _shell = self.container.resolve(CelliumShell)
        _mem_tool = MemoryTool(three_layer_memory=_mem)
        _file_tool = FileTool()
        _tool = ShellTool(shell=_shell)

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
            llm_engine=self.container.resolve(BaseLLMEngine),
            shell=_shell,
            three_layer_memory=_mem,
            tools={
                "shell": _tool,
                "memory": _mem_tool,
                "file": _file_tool,
            },
            global_config=agent_cfg,
        )
        channel_mgr = ChannelManager.get_instance()
        channel_mgr.set_agent_loop_manager(agent_loop_mgr)
        self.logger.info("[OK] AgentLoopManager + ChannelManager 集成完成")

        self._setup_channels()
        self.logger.info("[OK] 外部平台通道初始化完成")

    def _setup_channels(self):
        from app.channels import ChannelManager, QQAdapter, TelegramAdapter
        from app.channels.qq_channel_config import QQChannelConfig
        from app.channels.telegram_channel_config import TelegramChannelConfig

        channel_mgr = ChannelManager.get_instance()

        qq_config = QQChannelConfig()
        if qq_config.should_auto_start():
            if channel_mgr.get_adapter("qq"):
                self.logger.info("[Channel] QQ 适配器已存在，跳过注册")
            else:
                qq_adapter = QQAdapter(
                    app_id=qq_config.get_app_id(),
                    app_secret=qq_config.get_app_secret(),
                )
                channel_mgr.register_adapter(qq_adapter)
                app_id = qq_config.get_app_id()
                self.logger.info(f"[Channel] QQ 适配器已注册 (app_id: {app_id[:8] if app_id else '***'}...)")
        else:
            self.logger.warning("[Channel] QQ 通道未启用或凭证缺失，跳过加载")

        # 初始化 Telegram 通道
        tg_config = TelegramChannelConfig()
        if tg_config.should_auto_start():
            if channel_mgr.get_adapter("telegram"):
                self.logger.info("[Channel] Telegram 适配器已存在，跳过注册")
            else:
                tg_adapter = TelegramAdapter(
                    bot_token=tg_config.get_bot_token(),
                    whitelist_user_ids=tg_config.get_whitelist_user_ids(),
                    whitelist_usernames=tg_config.get_whitelist_usernames(),
                )
                channel_mgr.register_adapter(tg_adapter)
                self.logger.info("[Channel] Telegram 适配器已注册")
        else:
            self.logger.warning("[Channel] Telegram 通道未启用或凭证缺失，跳过加载")

        if not channel_mgr._running:
            self.logger.info("[Channel] 适配器已注册，将在服务器启动后自动连接")

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
