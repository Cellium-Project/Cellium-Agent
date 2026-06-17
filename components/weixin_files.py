# -*- coding: utf-8 -*-
"""
WeixinFiles - 微信文件传输工具
用于在微信和本地之间传输文件
"""

import os
from pathlib import Path
from typing import Dict, Any
from app.core.interface.base_cell import BaseCell


class WeixinFiles(BaseCell):
    """
    微信文件传输工具

    功能：
    - 从微信消息下载文件到 workspace
    - 发送本地文件到微信
    - 发送本地图片到微信
    - 列出从微信下载的文件
    """

    cell_name = "weixin_files"

    def __init__(self):
        super().__init__()
        # 下载目录：workspace/downloads/weixin
        self._download_dir = Path("workspace") / "downloads" / "weixin"
        self._download_dir.mkdir(parents=True, exist_ok=True)

    def _get_adapter(self):
        """自动从 ChannelManager 获取 WeixinAdapter"""
        try:
            from app.channels import ChannelManager
            channel_mgr = ChannelManager.get_instance()
            adapter = channel_mgr.get_adapter("weixin")
            if not adapter:
                print(f"[WeixinFiles] 微信 adapter 未找到. 已注册: {list(channel_mgr._adapters.keys())}")
            return adapter
        except Exception as e:
            print(f"[WeixinFiles] 获取 adapter 失败: {e}")
            return None

    def execute_with_context(self, arguments: Dict[str, Any], session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行工具调用（带平台上下文）"""
        command = arguments.get("command", "")

        self._last_platform_context = platform_context or {}

        if command == "download":
            return self._cmd_download(
                encrypt_query_param=arguments.get("encrypt_query_param", ""),
                aes_key=arguments.get("aes_key", ""),
                filename=arguments.get("filename"),
                folder=arguments.get("folder", ""),
                full_url=arguments.get("full_url"),
            )
        elif command == "send_file":
            return self._cmd_send_file(
                file_path=arguments.get("file_path", ""),
                target_id=arguments.get("target_id"),
            )
        elif command == "send_image":
            return self._cmd_send_image(
                image_path=arguments.get("image_path", ""),
                target_id=arguments.get("target_id"),
            )
        elif command == "send_video":
            return self._cmd_send_video(
                video_path=arguments.get("video_path", ""),
                target_id=arguments.get("target_id"),
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
            print(f"[WeixinFiles] 获取平台上下文失败: {e}")
            return {}

    # ============================================================
    # 文件下载
    # ============================================================

    def _cmd_download(
        self,
        encrypt_query_param: str,
        aes_key: str,
        filename: str = None,
        folder: str = "",
        full_url: str = None,
    ) -> Dict[str, Any]:
        """
        从微信下载文件到 workspace/downloads/weixin

        使用场景：
        - 用户发送文件给你，需要下载处理
        - 获取图片/视频内容进行分析

        Args:
            encrypt_query_param: 加密查询参数（从消息 raw.item_list 中获取）
            aes_key: AES 密钥（从消息 raw.item_list 中获取）
            filename: 保存的文件名（可选）
            folder: 子文件夹（可选）
            full_url: 完整下载 URL（可选）

        Returns:
            {
                "success": True,
                "file_path": "workspace/downloads/weixin/report.pdf",
                "file_size": 1024,
                "filename": "report.pdf"
            }
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "微信服务未启动"}

        if not encrypt_query_param or not aes_key:
            return {"success": False, "error": "缺少下载参数 encrypt_query_param 或 aes_key"}

        try:
            target_dir = self._download_dir / folder if folder else self._download_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            if not filename:
                filename = f"weixin_file_{os.urandom(4).hex()}"

            save_path = str(target_dir / filename)

            client = getattr(adapter, "_client", None)
            if not client:
                return {"success": False, "error": "微信客户端未初始化"}

            coro = client.download_media(
                encrypt_query_param=encrypt_query_param,
                aes_key_b64=aes_key,
                save_path=save_path,
                full_url=full_url,
            )
            adapter.run_async(coro, timeout=60)

            file_size = Path(save_path).stat().st_size
            return {
                "success": True,
                "file_path": save_path,
                "file_size": file_size,
                "filename": filename,
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
    ) -> Dict[str, Any]:
        """
        发送本地文件到微信

        Args:
            file_path: 本地文件的完整路径
            target_id: 用户 ID（可选，默认自动获取当前会话）

        Returns:
            {"success": True, "message": "文件发送成功"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "微信服务未启动"}

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        if target_id is None:
            context = self._get_platform_context()
            if not context:
                return {"success": False, "error": "无法获取当前会话信息"}
            target_id = context.get("user_id") or context.get("target_id")
            if not target_id:
                return {"success": False, "error": "无法获取目标用户 ID"}
            print(f"[WeixinFiles] 自动获取会话: target_id={target_id}")

        try:
            client = getattr(adapter, "_client", None)
            if not client:
                return {"success": False, "error": "微信客户端未初始化"}

            ctx_token = adapter._context_tokens.get(target_id)

            coro = client.send_file(target_id, file_path, context_token=ctx_token)
            adapter.run_async(coro, timeout=60)

            return {"success": True, "message": "文件发送成功"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_send_image(
        self,
        image_path: str,
        target_id: str = None,
    ) -> Dict[str, Any]:
        """
        发送本地图片到微信

        Args:
            image_path: 本地图片的完整路径
            target_id: 用户 ID（可选，默认自动获取当前会话）

        Returns:
            {"success": True, "message": "图片发送成功"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "微信服务未启动"}

        if target_id is None:
            context = self._get_platform_context()
            if not context:
                return {"success": False, "error": "无法获取当前会话信息"}
            target_id = context.get("user_id") or context.get("target_id")
            if not target_id:
                return {"success": False, "error": "无法获取目标用户 ID"}
            print(f"[WeixinFiles] 自动获取会话: target_id={target_id}")

        try:
            client = getattr(adapter, "_client", None)
            if not client:
                return {"success": False, "error": "微信客户端未初始化"}

            ctx_token = adapter._context_tokens.get(target_id)

            coro = client.send_image(target_id, image_path, context_token=ctx_token)
            adapter.run_async(coro, timeout=30)

            return {"success": True, "message": "图片发送成功"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_send_video(
        self,
        video_path: str,
        target_id: str = None,
    ) -> Dict[str, Any]:
        """
        发送本地视频到微信

        Args:
            video_path: 本地视频的完整路径
            target_id: 用户 ID（可选，默认自动获取当前会话）

        Returns:
            {"success": True, "message": "视频发送成功"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "微信服务未启动"}

        if target_id is None:
            context = self._get_platform_context()
            if not context:
                return {"success": False, "error": "无法获取当前会话信息"}
            target_id = context.get("user_id") or context.get("target_id")
            if not target_id:
                return {"success": False, "error": "无法获取目标用户 ID"}
            print(f"[WeixinFiles] 自动获取会话: target_id={target_id}")

        try:
            client = getattr(adapter, "_client", None)
            if not client:
                return {"success": False, "error": "微信客户端未初始化"}

            ctx_token = adapter._context_tokens.get(target_id)

            coro = client.send_video(target_id, video_path, context_token=ctx_token)
            adapter.run_async(coro, timeout=60)

            return {"success": True, "message": "视频发送成功"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ============================================================
    # 文件管理
    # ============================================================

    def _cmd_list(self, folder: str = "") -> Dict[str, Any]:
        """
        列出从微信下载的文件

        Args:
            folder: 子文件夹（可选）

        Returns:
            {
                "files": [
                    {"name": "report.pdf", "path": "workspace/downloads/weixin/report.pdf", "size": 1024}
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