# -*- coding: utf-8 -*-
"""
FeishuFiles - 飞书文件传输工具
用于在飞书和本地之间传输文件
"""

import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from app.core.interface.base_cell import BaseCell


class FeishuFiles(BaseCell):
    """
    飞书文件传输工具

    功能：
    - 从飞书消息下载文件到 workspace
    - 发送本地文件到飞书
    - 发送本地图片到飞书
    - 列出从飞书下载的文件
    """

    cell_name = "feishu_files"

    def __init__(self):
        super().__init__()
        self._download_dir = Path("workspace") / "downloads" / "feishu"
        self._download_dir.mkdir(parents=True, exist_ok=True)

    def _get_adapter(self):
        """自动从 ChannelManager 获取 FeishuAdapter"""
        try:
            from app.channels import ChannelManager
            channel_mgr = ChannelManager.get_instance()
            registered = list(channel_mgr._adapters.keys())
            adapter = channel_mgr.get_adapter("feishu")
            if not adapter:
                error_msg = f"飞书适配器未注册。已注册的平台: {registered}"
                print(f"[FeishuFiles] {error_msg}")
                return None, error_msg
            return adapter, None
        except Exception as e:
            error_msg = f"获取适配器失败: {e}"
            print(f"[FeishuFiles] {error_msg}")
            return None, error_msg

    def execute_with_context(self, arguments: Dict[str, Any], session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行工具调用"""
        command = arguments.get("command", "")

        self._last_platform_context = platform_context or {}
        self._last_session_id = session_id

        if command == "download":
            return self._cmd_download(
                file_key=arguments.get("file_key", ""),
                filename=arguments.get("filename"),
                folder=arguments.get("folder", ""),
                message_id=arguments.get("message_id")
            )
        elif command == "download_pending":
            return self._cmd_download_pending(
                folder=arguments.get("folder", "")
            )
        elif command == "send_file":
            return self._cmd_send_file(
                file_path=arguments.get("file_path", ""),
                target_id=arguments.get("target_id")
            )
        elif command == "send_image":
            return self._cmd_send_image(
                image_path=arguments.get("image_path", ""),
                target_id=arguments.get("target_id")
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
            print(f"[FeishuFiles] Failed to get platform context: {e}")
            return {}

    def _run_async(self, coro, timeout=120):
        """在新事件循环中运行异步协程"""
        def _run_in_new_loop(c):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(c)
            finally:
                loop.close()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_in_new_loop, coro)
            return future.result(timeout=timeout)

    def _cmd_download(
        self,
        file_key: str,
        filename: str = None,
        folder: str = "",
        message_id: str = None
    ) -> Dict[str, Any]:
        """
        从飞书下载文件到 workspace/downloads/feishu

        使用场景：
        - 用户发送文件给你，需要下载处理
        - 获取文件内容进行分析

        Args:
            file_key: 文件的 file_key（从消息中获取）
            filename: 保存的文件名（可选）
            folder: 子文件夹（可选）
            message_id: 消息ID（用户发送的文件需要此参数）

        Returns:
            {
                "success": True,
                "file_path": "workspace/downloads/feishu/report.pdf",
                "file_size": 1024,
                "filename": "report.pdf"
            }
            或
            {"success": False, "error": "错误信息"}
        """
        if not file_key:
            return {"success": False, "error": "file_key 不能为空"}

        adapter, error = self._get_adapter()
        if not adapter:
            return {"success": False, "error": error}

        if not message_id:
            context = self._get_platform_context()
            message_id = context.get("msg_id")

        try:
            sub_dir = f"downloads/feishu/{folder}" if folder else "downloads/feishu"
            
            coro = adapter.download_file(
                file_key=file_key,
                filename=filename,
                sub_dir=sub_dir,
                message_id=message_id
            )
            result = self._run_async(coro, timeout=120)
            
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

    def _cmd_download_pending(self, folder: str = "") -> Dict[str, Any]:
        """
        下载用户最近发送的文件（从 pending_files 自动获取信息）

        使用场景：
        - 用户发送文件后，直接下载处理
        - 无需手动输入 file_key

        Args:
            folder: 子文件夹（可选）

        Returns:
            {
                "success": True,
                "file_path": "workspace/downloads/feishu/report.pdf",
                "file_size": 1024,
                "filename": "report.pdf"
            }
            或
            {"success": False, "error": "错误信息"}
        """
        # 获取当前会话的 pending_files
        pending_file = self._get_latest_pending_file()
        if not pending_file:
            return {"success": False, "error": "没有待下载的文件。请先发送文件后再尝试下载。"}

        file_key = pending_file.get("file_key")
        image_key = pending_file.get("image_key")
        filename = pending_file.get("filename")
        message_id = pending_file.get("msg_id")

        if not file_key and not image_key:
            return {"success": False, "error": f"文件缺少下载所需的 key: {pending_file}"}

        adapter, error = self._get_adapter()
        if not adapter:
            return {"success": False, "error": error}

        try:
            sub_dir = f"downloads/feishu/{folder}" if folder else "downloads/feishu"

            key_to_use = file_key or image_key

            coro = adapter.download_file(
                file_key=key_to_use,
                filename=filename,
                sub_dir=sub_dir,
                message_id=message_id
            )
            result = self._run_async(coro, timeout=120)

            if "error" in result:
                return {"success": False, "error": result["error"]}

            self._remove_pending_file(pending_file)

            return {
                "success": True,
                "file_path": result["file_path"],
                "file_size": result["file_size"],
                "filename": result["filename"],
                "original_filename": pending_file.get("filename"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _get_latest_pending_file(self) -> Optional[Dict[str, Any]]:
        """获取当前会话最新的待下载文件"""
        try:
            from app.agent.loop.session_manager import get_session_manager
            session_mgr = get_session_manager()

            session_id = getattr(self, "_last_session_id", None)
            if not session_id:
                sessions = session_mgr.list_sessions(active_only=True)
                if not sessions:
                    return None
                session_id = sessions[0]["session_id"]

            session_info = session_mgr.get(session_id)
            if not session_info:
                return None

            pending_files = getattr(session_info, "pending_files", [])
            if not pending_files:
                return None

            return pending_files[-1] if pending_files else None
        except Exception as e:
            print(f"[FeishuFiles] Failed to get pending files: {e}")
            return None

    def _remove_pending_file(self, file_to_remove: Dict[str, Any]) -> bool:
        """从 pending_files 中移除已下载的文件"""
        try:
            from app.agent.loop.session_manager import get_session_manager
            session_mgr = get_session_manager()

            session_id = getattr(self, "_last_session_id", None)
            if not session_id:
                sessions = session_mgr.list_sessions(active_only=True)
                if not sessions:
                    return False
                session_id = sessions[0]["session_id"]

            session_info = session_mgr.get(session_id)
            if not session_info:
                return False

            pending_files = getattr(session_info, "pending_files", [])
            if not pending_files:
                return False

            removed = False
            new_pending = []
            for f in pending_files:
                if isinstance(f, dict):
                    if (f.get("msg_id") == file_to_remove.get("msg_id") or
                        f.get("file_key") == file_to_remove.get("file_key") or
                        f.get("image_key") == file_to_remove.get("image_key")):
                        removed = True
                        continue
                new_pending.append(f)

            if removed:
                session_info.pending_files = new_pending
                print(f"[FeishuFiles] Removed file from pending_files: {file_to_remove.get('filename')}")
                return True
            return False
        except Exception as e:
            print(f"[FeishuFiles] Failed to remove pending file: {e}")
            return False

    def _cmd_send_file(
        self,
        file_path: str,
        target_id: str = None
    ) -> Dict[str, Any]:
        """
        发送本地文件到飞书

        使用场景：
        - 生成报告后发送给用户
        - 转发文件给飞书用户或群

        Args:
            file_path: 本地文件的完整路径
            target_id: 用户 Open ID 或群 Chat ID（可选，默认自动获取当前会话）

        Returns:
            {"success": True, "message": "文件发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter, error = self._get_adapter()
        if not adapter:
            return {"success": False, "error": error}

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        if target_id is None:
            context = self._get_platform_context()
            if not context:
                return {"success": False, "error": "无法获取当前会话信息，请确保是通过飞书平台接收的消息"}
            
            target_id = context.get("target_id")
            if not target_id:
                return {"success": False, "error": "无法获取目标用户 ID"}
            
            print(f"[FeishuFiles] 自动获取会话信息: target_id={target_id}")

        try:
            is_group = target_id.startswith("oc_")
            
            coro = adapter.send_file_message(
                target_id=target_id,
                file_path=file_path,
                is_group=is_group
            )
            success = self._run_async(coro, timeout=120)
            
            if success:
                return {"success": True, "message": "文件发送成功"}
            else:
                return {"success": False, "error": "文件发送失败"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_send_image(
        self,
        image_path: str,
        target_id: str = None
    ) -> Dict[str, Any]:
        """
        发送本地图片到飞书

        使用场景：
        - 生成图表后发送
        - 发送截图或照片到飞书

        Args:
            image_path: 本地图片的完整路径
            target_id: 用户 Open ID 或群 Chat ID（可选，默认自动获取当前会话）

        Returns:
            {"success": True, "message": "图片发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter, error = self._get_adapter()
        if not adapter:
            return {"success": False, "error": error}

        image_path_obj = Path(image_path)
        if not image_path_obj.exists():
            return {"success": False, "error": f"图片不存在: {image_path}"}

        if target_id is None:
            context = self._get_platform_context()
            if not context:
                return {"success": False, "error": "无法获取当前会话信息，请确保是通过飞书平台接收的消息"}
            
            target_id = context.get("target_id")
            if not target_id:
                return {"success": False, "error": "无法获取目标用户 ID"}
            
            print(f"[FeishuFiles] 自动获取会话信息: target_id={target_id}")

        try:
            is_group = target_id.startswith("oc_")
            
            coro = adapter.send_image_message(
                target_id=target_id,
                image_path=image_path,
                is_group=is_group
            )
            success = self._run_async(coro, timeout=120)
            
            if success:
                return {"success": True, "message": "图片发送成功"}
            else:
                return {"success": False, "error": "图片发送失败"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_list(self, folder: str = "") -> Dict[str, Any]:
        """
        列出从飞书下载的文件

        使用场景：
        - 查看之前从飞书下载的文件
        - 确认文件是否存在

        Args:
            folder: 子文件夹（可选，默认列出所有）

        Returns:
            {
                "files": [
                    {"name": "report.pdf", "path": "workspace/downloads/feishu/report.pdf", "size": 1024}
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
