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
from app.server.routes.skills import router as skills_router
from app.server.routes.gene import router as gene_router
from app.server.routes.scheduler import router as scheduler_router


@asynccontextmanager
async def lifespan_context(app: FastAPI):
    from app.channels import ChannelManager
    channel_mgr = ChannelManager.get_instance()
    if channel_mgr.list_platforms() and not channel_mgr.is_running:
        await channel_mgr.start_all(with_queue=False)
        import logging
        platforms = ", ".join(channel_mgr.list_platforms())
        logging.getLogger(__name__).info(f"[Channel] 通道已在启动时自动连接: {platforms}")
    
    from app.core.scheduler import get_scheduler_manager, start_executor
    from app.agent.loop import AgentLoopManager
    
    scheduler_manager = get_scheduler_manager()
    scheduler_manager.start()
    await scheduler_manager.start_loop()
    
    loop_manager = AgentLoopManager.get_instance()
    start_executor(loop_manager)
    
    import asyncio
    import sys
    import os
    
    async def open_browser_delayed():
        await asyncio.sleep(0.1)
        try:
            import webbrowser
            from app.core.util.agent_config import get_config
            cfg = get_config()
            host = cfg.get("server.host", "127.0.0.1")
            port = cfg.get("server.port", 18000)
            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass
    
    if sys.platform == "win32" or sys.platform == "darwin" or os.environ.get("DISPLAY"):
        asyncio.create_task(open_browser_delayed())
    
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

    cors_origins = cfg.get("server.cors_origins", ["*"])
    if isinstance(cors_origins, str):
        cors_origins = [o.strip() for o in cors_origins.split(",")]
    cors_methods = cfg.get("server.cors_methods", ["*"])
    cors_headers = cfg.get("server.cors_headers", ["*"])

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=cors_methods,
        allow_headers=cors_headers,
    )

    app.include_router(chat_router)
    app.include_router(config_router)
    app.include_router(memory_router)
    app.include_router(components_router)
    app.include_router(logs_router)
    app.include_router(channels_router)
    app.include_router(session_events_router)
    app.include_router(skills_router)
    app.include_router(gene_router)
    app.include_router(scheduler_router)

    html_dir = cfg.get("server.static_dir") or os.path.join(os.path.dirname(__file__), "..", "..", "html")
    if os.path.exists(html_dir):
        app.mount("/assets", StaticFiles(directory=os.path.join(html_dir, "assets")), name="assets")
        app.mount("/font", StaticFiles(directory=os.path.join(html_dir, "font")), name="font")
        
        from fastapi.responses import FileResponse
        
        @app.get("/")
        async def serve_index():
            return FileResponse(os.path.join(html_dir, "index.html"))
        
        @app.get("/logo.png")
        async def serve_logo():
            return FileResponse(os.path.join(html_dir, "logo.png"))

    @app.middleware("http")
    async def _suppress_favicon(request, call_next):
        if request.url.path == "/favicon.ico":
            return Response(status_code=204)  
        return await call_next(request)

    return app
