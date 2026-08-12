# -*- coding: utf-8 -*-
"""
Cellium Agent - 主入口

注意：本文件的顶层 imports 必须保持极简（只允许 os/sys/multiprocessing 等
stdlib 轻量模块）。所有重模块 imports 必须放在 main() 函数体内部，或放在 `if __name__ == "__main__":` 块内部。
"""
import os
import sys
import multiprocessing

multiprocessing.freeze_support()

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

libs_dir = os.path.join(project_root, "libs")
if os.path.exists(libs_dir) and libs_dir not in sys.path:
    sys.path.insert(0, libs_dir)

def main():
    """WebUI 入口（python main.py / cellium-web）"""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(prog="cellium-web", description="Cellium Agent WebUI 服务")
    parser.add_argument("--host", default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="端口")
    args = parser.parse_args()

    from app.core.bootstrap import bootstrap_agent, setup_uvicorn_logging
    result = bootstrap_agent(host=args.host, port=args.port)
    uvicorn.run(result.fastapi_app, host=result.host, port=result.port, log_config=setup_uvicorn_logging())

def main_tui():
    """TUI 入口（cellium）"""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(prog="cellium", description="Cellium Agent 全屏终端界面")
    parser.add_argument("--tui", action="store_true", help="进入 TUI")
    parser.add_argument("--host", default=None, help="WebUI 监听地址")
    parser.add_argument("--port", type=int, default=None, help="WebUI 端口")
    parser.add_argument("--session", default=None, help="会话 ID")
    parser.add_argument("--no-webui", action="store_true", help="不启动 WebUI 服务")
    args = parser.parse_args()

    from app.tui.runner import run_tui
    asyncio.run(run_tui(
        host=args.host,
        port=args.port,
        session_id=args.session,
        enable_webapp=not args.no_webui,
    ))

if __name__ == "__main__":
    if "--tui" in sys.argv:
        main_tui()
    else:
        main()
