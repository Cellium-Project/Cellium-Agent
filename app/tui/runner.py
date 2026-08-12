# -*- coding: utf-8 -*-
"""Cellium TUI 启动器"""
import asyncio
import contextlib
import io
import logging
import sys
import threading

def _silence_console_logging():
    """静音控制台日志，避免污染 TUI 终端（日志仍进入内存缓冲区供 WebUI 查看）"""
    try:
        from app.core.util.logger import set_console_logging
        set_console_logging(False)
    except Exception:
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler):
                root.removeHandler(h)
        for name in list(logging.root.manager.loggerDict):
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                if isinstance(h, logging.StreamHandler):
                    lg.removeHandler(h)

def _run_uvicorn(fastapi_app, host, port):
    import asyncio
    import io
    import logging
    import uvicorn

    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.handlers = [handler]
    uvicorn_logger.propagate = False
    for sub in ("uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        lg = logging.getLogger(sub)
        lg.handlers = [handler]
        lg.propagate = False

    async def _serve():
        config = uvicorn.Config(
            fastapi_app,
            host=host,
            port=port,
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        await server.serve()

    try:
        asyncio.run(_serve())
    except Exception:
        pass

def _start_web_server(app, result):
    if getattr(result, "fastapi_app", None) is None:
        return
    try:
        t = threading.Thread(
            target=_run_uvicorn,
            args=(result.fastapi_app, result.host, result.port),
            daemon=True,
            name="webui-server",
        )
        t.start()
        app._server_thread = t
    except Exception:
        pass

def _bootstrap_in_thread(app, host, port, enable_webapp):
    # 先静音控制台日志，避免 bootstrap 期间日志污染 TUI 终端
    _silence_console_logging()
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            from app.core.bootstrap import bootstrap_agent
            result = bootstrap_agent(host=host, port=port, enable_webapp=enable_webapp, auto_open_browser=False)
        _silence_console_logging()
        _start_web_server(app, result)
        try:
            app.call_from_thread(_apply_bootstrap, result)
        except Exception:
            try:
                app.call_from_thread(_apply_bootstrap, result)
            except Exception:
                app.bootstrap = result
                app._bootstrap_ready.set()
    except Exception as e:
        app._bootstrap_error = str(e)
        try:
            loop = asyncio.get_event_loop()
            if loop and loop.is_running() and not loop.is_closed():
                asyncio.run_coroutine_threadsafe(_apply_bootstrap_error(app, e), loop).result(timeout=15)
            else:
                # loop 已关闭/未运行：直接置位，避免 "Event loop is closed"
                app._bootstrap_ready.set()
        except Exception:
            try:
                app._bootstrap_ready.set()
            except Exception:
                pass

async def _apply_bootstrap(app, result):
    app.bootstrap = result
    app._bootstrap_ready.set()
    app._refresh_status()

async def _apply_bootstrap_error(app, exc):
    app._bootstrap_ready.set()
    app._refresh_status()

async def run_tui(host=None, port=None, session_id=None, enable_webapp=True):
    from app.tui.app import CelliumTUI

    # 启动前即静音控制台日志，确保 TUI 渲染期间终端不被日志污染
    _silence_console_logging()

    # 后台线程（bootstrap / uvicorn）的 traceback 不应污染 TUI 终端，
    # 将 stderr 重定向到缓冲区，TUI 退出后恢复
    _stderr_buf = io.StringIO()
    _old_stderr = sys.stderr
    try:
        sys.stderr = _stderr_buf
        return await _run_tui_inner(host, port, session_id, enable_webapp)
    finally:
        sys.stderr = _old_stderr


async def _run_tui_inner(host=None, port=None, session_id=None, enable_webapp=True):
    from app.tui.app import CelliumTUI

    app = CelliumTUI(bootstrap=None, session_id=session_id)
    threading.Thread(
        target=_bootstrap_in_thread,
        args=(app, host, port, enable_webapp),
        daemon=True,
        name="tui-bootstrap",
    ).start()
    try:
        await app.run_async()
    finally:
        server_thread = getattr(app, "_server_thread", None)
        if server_thread:
            try:
                server_thread.join(timeout=2)
            except Exception:
                pass
