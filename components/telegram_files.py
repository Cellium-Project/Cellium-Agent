# -*- coding: utf-8 -*-
"""
TelegramFiles - Telegram 文件传输工具
用于在 Telegram 和本地之间传输文件
"""

import asyncio
import os
from pathlib import Path
from typing import Dict, Any
from app.core.interface.base_cell import BaseCell


class TelegramFiles(BaseCell):
    """
    Telegram 文件传输工具

    功能：
    - 从 Telegram 消息下载文件到 workspace
    - 发送本地文件到 Telegram
    - 发送本地图片到 Telegram
    - 列出从 Telegram 下载的文件
    """

    cell_name = "telegram_files"

    def __init__(self):
        super().__init__()
        # 下载目录：workspace/downloads/telegram
        self._download_dir = Path("workspace") / "downloads" / "telegram"
        self._download_dir.mkdir(parents=True, exist_ok=True)

    def _get_adapter(self):
        """自动从 ChannelManager 获取 TelegramAdapter"""
        try:
            from app.channels import ChannelManager
            channel_mgr = ChannelManager.get_instance()
            adapter = channel_mgr.get_adapter("telegram")
            if not adapter:
                print(f"[TelegramFiles] Telegram adapter not found. Registered adapters: {list(channel_mgr._adapters.keys())}")
            return adapter
        except Exception as e:
            print(f"[TelegramFiles] Failed to get adapter: {e}")
            return None

    # ============================================================
    # 文件下载
    # ============================================================

    def _cmd_download(
        self,
        file_id: str,
        filename: str = None
    ) -> Dict[str, Any]:
        """
        从 Telegram 下载文件到 workspace/downloads/telegram

        使用场景：
        - 用户发送文件给你，需要下载处理
        - 获取文件内容进行分析

        Args:
            file_id: Telegram 文件 ID（从消息中获取）
            filename: 保存的文件名（可选，默认使用原始文件名）

        Returns:
            {
                "success": True,
                "file_path": "workspace/downloads/telegram/report.pdf",
                "file_size": 1024,
                "filename": "report.pdf"
            }
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "Telegram 服务未启动"}

        try:
            import concurrent.futures

            def _run_in_new_loop(coro):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            # 下载文件（_download_file 内部会获取文件信息）
            # 如果未指定文件名，使用 file_id 作为临时名称
            if not filename:
                filename = f"file_{file_id}"

            coro = adapter._download_file(file_id, filename)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_new_loop, coro)
                file_path = future.result(timeout=60)

            if not file_path:
                return {"success": False, "error": "下载文件失败"}
            
            # 获取文件大小
            file_size = Path(file_path).stat().st_size if Path(file_path).exists() else 0
            
            return {
                "success": True,
                "file_path": file_path,
                "file_size": file_size,
                "filename": filename
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ============================================================
    # 文件发送
    # ============================================================

    def _cmd_send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str = ""
    ) -> Dict[str, Any]:
        """
        发送本地文件到 Telegram

        使用场景：
        - 生成报告后发送给用户
        - 转发文件给 Telegram 用户或群

        Args:
            chat_id: 聊天 ID（从消息中获取）
            file_path: 本地文件的完整路径
            caption: 文件说明文字（可选）

        Returns:
            {"success": True, "message": "文件发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "Telegram 服务未启动"}

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        try:
            import concurrent.futures

            def _run_in_new_loop(coro):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            # 判断是图片还是普通文件
            mime_type = self._get_mime_type(file_path)
            if mime_type and mime_type.startswith("image/"):
                send_coro = adapter.send_photo(chat_id, str(file_path), caption)
            else:
                send_coro = adapter.send_document(chat_id, str(file_path), caption)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                send_future = executor.submit(_run_in_new_loop, send_coro)
                success = send_future.result(timeout=60)

            if success:
                return {"success": True, "message": "文件发送成功"}
            else:
                return {"success": False, "error": "发送文件失败"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_send_image(
        self,
        chat_id: str,
        image_path: str,
        caption: str = ""
    ) -> Dict[str, Any]:
        """
        发送本地图片到 Telegram

        使用场景：
        - 生成图表后发送
        - 发送截图或照片到 Telegram

        Args:
            chat_id: 聊天 ID（从消息中获取）
            image_path: 本地图片的完整路径
            caption: 图片说明文字（可选）

        Returns:
            {"success": True, "message": "图片发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "Telegram 服务未启动"}

        file_path_obj = Path(image_path)
        if not file_path_obj.exists():
            return {"success": False, "error": f"图片不存在: {image_path}"}

        try:
            import concurrent.futures

            def _run_in_new_loop(coro):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            send_coro = adapter.send_photo(chat_id, str(image_path), caption)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                send_future = executor.submit(_run_in_new_loop, send_coro)
                success = send_future.result(timeout=60)

            if success:
                return {"success": True, "message": "图片发送成功"}
            else:
                return {"success": False, "error": "图片发送失败"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _get_mime_type(self, file_path: str) -> str:
        """获取文件的 MIME 类型"""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        return mime_type or "application/octet-stream"

    # ============================================================
    # 文件管理
    # ============================================================

    def _cmd_list(self, folder: str = "") -> Dict[str, Any]:
        """
        列出从 Telegram 下载的文件

        使用场景：
        - 查看之前从 Telegram 下载的文件
        - 确认文件是否存在

        Args:
            folder: 子文件夹（可选，默认列出所有）

        Returns:
            {
                "files": [
                    {"name": "report.pdf", "path": "workspace/downloads/telegram/report.pdf", "size": 1024}
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
