# -*- coding: utf-8 -*-
"""
QQFiles - QQ 文件传输工具
用于在 QQ 和本地之间传输文件
"""

import asyncio
import os
from pathlib import Path
from typing import Dict, Any
from app.core.interface.base_cell import BaseCell


class QQFiles(BaseCell):
    """
    QQ 文件传输工具

    功能：
    - 从 QQ 消息下载文件到 workspace
    - 发送本地文件到 QQ
    - 发送本地图片到 QQ
    - 列出从 QQ 下载的文件
    """

    cell_name = "qq_files"

    def __init__(self):
        super().__init__()
        # 下载目录：workspace/downloads/qq
        self._download_dir = Path("workspace") / "downloads" / "qq"
        self._download_dir.mkdir(parents=True, exist_ok=True)

    def _get_adapter(self):
        """自动从 ChannelManager 获取 QQAdapter"""
        try:
            from app.channels import ChannelManager
            channel_mgr = ChannelManager.get_instance()
            adapter = channel_mgr.get_adapter("qq")
            if not adapter:
                print(f"[QQFiles] QQ adapter not found. Registered adapters: {list(channel_mgr._adapters.keys())}")
            return adapter
        except Exception as e:
            print(f"[QQFiles] Failed to get adapter: {e}")
            return None

    def execute_with_context(self, arguments: Dict[str, Any], session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行工具调用（带平台上下文）"""
        command = arguments.get("command", "")
        
        self._last_platform_context = platform_context or {}
        
        if command == "download":
            return self._cmd_download(
                url=arguments.get("url", ""),
                filename=arguments.get("filename"),
                folder=arguments.get("folder", "")
            )
        elif command == "send_file":
            return self._cmd_send_file(
                file_path=arguments.get("file_path", ""),
                target_id=arguments.get("target_id"),
                is_group=arguments.get("is_group")
            )
        elif command == "send_image":
            return self._cmd_send_image(
                image_path=arguments.get("image_path", ""),
                target_id=arguments.get("target_id"),
                is_group=arguments.get("is_group")
            )
        elif command == "list":
            return self._cmd_list(folder=arguments.get("folder", ""))
        else:
            return {"success": False, "error": f"未知命令: {command}"}

    def _get_platform_context(self) -> Dict[str, Any]:
        """获取当前会话的平台上下文"""
        if hasattr(self, "_last_platform_context"):
            return self._last_platform_context
        
        try:
            from app.agent.loop.session_manager import get_session_manager
            session_mgr = get_session_manager()
            
            sessions = session_mgr.list_sessions(active_only=True)
            if not sessions:
                return {}
            
            latest_session = sessions[0]
            session_info = session_mgr.get(latest_session["session_id"])
            
            if session_info and hasattr(session_info, "platform_context"):
                return session_info.platform_context
            return {}
        except Exception as e:
            print(f"[QQFiles] Failed to get platform context: {e}")
            return {}

    # ============================================================
    # 文件下载
    # ============================================================

    def _cmd_download(
        self,
        url: str,
        filename: str = None,
        folder: str = ""
    ) -> Dict[str, Any]:
        """
        从 QQ 下载文件到 workspace/downloads/qq

        使用场景：
        - 用户发送文件给你，需要下载处理
        - 获取文件内容进行分析

        Args:
            url: 文件的下载链接（从消息中获取）
            filename: 保存的文件名（可选，默认从 URL 提取）
            folder: 子文件夹（可选，保存到 workspace/downloads/qq/子文件夹）

        Returns:
            {
                "success": True,
                "file_path": "workspace/downloads/qq/report.pdf",
                "file_size": 1024,
                "filename": "report.pdf"
            }
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "QQ 服务未启动"}

        try:
            # folder 是相对于 self._download_dir 的子目录
            import concurrent.futures

            def _run_in_new_loop(coro):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            coro = adapter.download_file(url, filename, folder)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_new_loop, coro)
                result = future.result(timeout=30)

            if "error" in result:
                return {"success": False, "error": result["error"]}
            return {
                "success": True,
                "file_path": result["file_path"],
                "file_size": result["file_size"],
                "filename": result["filename"]
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ============================================================
    # 文件发送
    # ============================================================

    def _cmd_send_file(
        self,
        file_path: str,
        target_id: str = None,
        is_group: bool = None
    ) -> Dict[str, Any]:
        """
        发送本地文件到 QQ

        使用场景：
        - 生成报告后发送给用户
        - 转发文件给 QQ 用户或群

        Args:
            file_path: 本地文件的完整路径
            target_id: 用户 OpenID 或群 OpenID（可选，默认自动获取当前会话）
            is_group: 是否是群聊（可选，默认自动检测）

        Returns:
            {"success": True, "message": "文件发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "QQ 服务未启动"}

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        if target_id is None or is_group is None:
            context = self._get_platform_context()
            if not context:
                return {"success": False, "error": "无法获取当前会话信息，请确保是通过 QQ 平台接收的消息"}
            
            if target_id is None:
                target_id = context.get("target_id")
                if not target_id:
                    return {"success": False, "error": "无法获取目标用户 ID"}
            
            if is_group is None:
                is_group = context.get("message_type") == "group"
            
            print(f"[QQFiles] 自动获取会话信息: target_id={target_id}, is_group={is_group}")

        try:
            import concurrent.futures

            def _run_in_new_loop(coro):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            send_coro = adapter.send_file_message(target_id, file_path, is_group)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                send_future = executor.submit(_run_in_new_loop, send_coro)
                success = send_future.result(timeout=60)

            if success:
                return {"success": True, "message": "文件发送成功"}
            else:
                return {"success": False, "error": "发送文件消息失败"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_send_image(
        self,
        image_path: str,
        target_id: str = None,
        is_group: bool = None
    ) -> Dict[str, Any]:
        """
        发送本地图片到 QQ

        使用场景：
        - 生成图表后发送
        - 发送截图或照片到 QQ

        Args:
            image_path: 本地图片的完整路径
            target_id: 用户 OpenID 或群 OpenID（可选，默认自动获取当前会话）
            is_group: 是否是群聊（可选，默认自动检测）

        Returns:
            {"success": True, "message": "图片发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "QQ 服务未启动"}

        if target_id is None or is_group is None:
            context = self._get_platform_context()
            if not context:
                return {"success": False, "error": "无法获取当前会话信息，请确保是通过 QQ 平台接收的消息"}
            
            if target_id is None:
                target_id = context.get("target_id")
                if not target_id:
                    return {"success": False, "error": "无法获取目标用户 ID"}
            
            if is_group is None:
                is_group = context.get("message_type") == "group"
            
            print(f"[QQFiles] 自动获取会话信息: target_id={target_id}, is_group={is_group}")

        try:
            import concurrent.futures

            def _run_in_new_loop(coro):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_new_loop, adapter.send_image_message(target_id, image_path, is_group))
                success = future.result(timeout=30)
            if success:
                return {"success": True, "message": "图片发送成功"}
            else:
                return {"success": False, "error": "图片发送失败"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ============================================================
    # 文件管理
    # ============================================================

    def _cmd_list(self, folder: str = "") -> Dict[str, Any]:
        """
        列出从 QQ 下载的文件

        使用场景：
        - 查看之前从 QQ 下载的文件
        - 确认文件是否存在

        Args:
            folder: 子文件夹（可选，默认列出所有）

        Returns:
            {
                "files": [
                    {"name": "report.pdf", "path": "workspace/downloads/qq/report.pdf", "size": 1024}
                ]
            }
        """
        try:
            target_dir = self._download_dir / folder if folder else self._download_dir
            if not target_dir.exists():
                return {"files": []}

            files = []
            for file_path in target_dir.rglob("*"):
                if file_path.is_file():
                    files.append({
                        "name": file_path.name,
                        "path": str(file_path),
                        "size": file_path.stat().st_size
                    })

            return {"files": files}
        except Exception as e:
            return {"error": str(e)}

    # 注意：读取和删除本地文件请使用默认的 file 工具
    # - file.read 读取文件内容
    # - file.delete 删除文件
