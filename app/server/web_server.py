# -*- coding: utf-8 -*-
"""
FastAPI 应用工厂
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.server.routes.chat import router as chat_router
from app.server.routes.config import router as config_router
from app.server.routes.memory import router as memory_router
from app.server.routes.components import router as components_router
from app.server.routes.logs import router as logs_router
from app.server.routes.channels import router as channels_router
from app.server.routes.session_events import router as session_events_router


@asynccontextmanager
async def lifespan_context(app: FastAPI):
    from app.channels import ChannelManager
    channel_mgr = ChannelManager.get_instance()
    if channel_mgr.get_adapter("qq") and not channel_mgr.is_running:
        await channel_mgr.start_all(with_queue=False)
        import logging
        logging.getLogger(__name__).info("[Channel] QQ 通道已在启动时自动连接")
    yield


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""

    from app.core.util.agent_config import get_config
    cfg = get_config()

    app = FastAPI(
        title="Cellium Agent",
        description="基于微内核架构（EventBus + DI + BaseTool）的跨平台通用 Agent",
        version="2.0.0",
        lifespan=lifespan_context,
    )

    # ★ 从 server.yaml 读取 CORS 配置
    cors_origins = cfg.get("server.cors_origins", ["*"])
    if isinstance(cors_origins, str):
        cors_origins = [o.strip() for o in cors_origins.split(",")]
    cors_methods = cfg.get("server.cors_methods", ["*"])
    cors_headers = cfg.get("server.cors_headers", ["*"])

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=cors_methods,
        allow_headers=cors_headers,
    )

    # 注册路由
    app.include_router(chat_router)
    app.include_router(config_router)
    app.include_router(memory_router)
    app.include_router(components_router)
    app.include_router(logs_router)
    app.include_router(channels_router)
    app.include_router(session_events_router)


    # ★ 静态文件目录从 server.yaml 读取
    html_dir = cfg.get("server.static_dir") or os.path.join(os.path.dirname(__file__), "..", "..", "html")
    if os.path.exists(html_dir):
        app.mount("/", StaticFiles(directory=html_dir, html=True), name="static")

    # ★ 静默拦截 favicon 请求（浏览器自动请求，项目不使用）
    @app.middleware("http")
    async def _suppress_favicon(request, call_next):
        if request.url.path == "/favicon.ico":
            return Response(status_code=204)  # No Content
        return await call_next(request)

    return app
