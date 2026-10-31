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
    - 发送本地语音到 QQ（silk/wav/mp3/flac）
    - 列出从 QQ 下载的文件
    """

    cell_name = "qq_files"
    required_channel = "qq"

    def __init__(self):
        super().__init__()
        # 下载目录：workspace/downloads/qq
        from app.core.util.runtime_paths import resolve_dir_writable
        self._download_dir = Path(resolve_dir_writable("workspace")) / "downloads" / "qq"
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
        
        # 未传 context 时保留上一次的上下文，避免被空值覆盖
        if platform_context is not None:
            self._last_platform_context = platform_context
        
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
                is_group=arguments.get("is_group"),
                msg_id=arguments.get("msg_id")
            )
        elif command == "send_image":
            return self._cmd_send_image(
                image_path=arguments.get("image_path", ""),
                target_id=arguments.get("target_id"),
                is_group=arguments.get("is_group"),
                msg_id=arguments.get("msg_id")
            )
        elif command == "send_voice":
            return self._cmd_send_voice(
                voice_path=arguments.get("voice_path", ""),
                target_id=arguments.get("target_id"),
                is_group=arguments.get("is_group"),
                msg_id=arguments.get("msg_id")
            )
        elif command == "list":
            return self._cmd_list(folder=arguments.get("folder", ""))
        else:
            return {"success": False, "error": f"未知命令: {command}"}

    def _get_platform_context(self) -> Dict[str, Any]:
        """获取当前会话的平台上下文"""
        if getattr(self, "_last_platform_context", None):
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

    def _resolve_target_params(self, target_id, is_group, msg_id):
        """解析发送目标与被动回复 msg_id

        Returns:
            (target_id, is_group, msg_id, error)
        """
        if target_id is not None and is_group is not None and msg_id is not None:
            return target_id, is_group, msg_id, None

        context = self._get_platform_context()
        if not context:
            return target_id, is_group, msg_id, "无法获取当前会话信息，请确保是通过 QQ 平台接收的消息"

        if target_id is None:
            target_id = context.get("target_id")
            if not target_id:
                return target_id, is_group, msg_id, "无法获取目标 ID（群 OpenID 或用户 OpenID）"

        if is_group is None:
            is_group = context.get("message_type") == "group"

        if msg_id is None:
            msg_id = context.get("msg_id")

        print(f"[QQFiles] 会话信息: target_id={target_id}, is_group={is_group}, msg_id={msg_id}")
        return target_id, is_group, msg_id, None

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
        is_group: bool = None,
        msg_id: str = None
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
            msg_id: 被动回复的消息 ID（可选，默认自动获取当前会话）

        Returns:
            {"success": True, "message": "文件发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "QQ 服务未启动"}

        file_path_obj = Path(file_path)
        if not str(file_path).strip():
            return {"success": False, "error": "缺少 file_path 参数"}
        if not file_path_obj.exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}
        if not file_path_obj.is_file():
            return {"success": False, "error": f"不是普通文件: {file_path}"}

        target_id, is_group, msg_id, error = self._resolve_target_params(
            target_id, is_group, msg_id
        )
        if error:
            return {"success": False, "error": error}

        return self._send_media(adapter, target_id, file_path, 4, is_group, msg_id, "文件")

    def _cmd_send_image(
        self,
        image_path: str,
        target_id: str = None,
        is_group: bool = None,
        msg_id: str = None
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
            msg_id: 被动回复的消息 ID（可选，默认自动获取当前会话）

        Returns:
            {"success": True, "message": "图片发送成功"}
            或
            {"success": False, "error": "错误信息"}
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "QQ 服务未启动"}

        if not str(image_path).strip():
            return {"success": False, "error": "缺少 image_path 参数"}
        image_path_obj = Path(image_path)
        if not image_path_obj.exists():
            return {"success": False, "error": f"图片不存在: {image_path}"}
        if not image_path_obj.is_file():
            return {"success": False, "error": f"不是普通文件: {image_path}"}

        target_id, is_group, msg_id, error = self._resolve_target_params(
            target_id, is_group, msg_id
        )
        if error:
            return {"success": False, "error": error}

        return self._send_media(adapter, target_id, image_path, 1, is_group, msg_id, "图片")

    def _cmd_send_voice(
        self,
        voice_path: str,
        target_id: str = None,
        is_group: bool = None,
        msg_id: str = None
    ) -> Dict[str, Any]:
        """
        发送本地语音到 QQ（支持 silk/wav/mp3/flac）

        使用场景：
        - 用 TTS 生成语音后发送
        - 发送录音给用户或群

        Args:
            voice_path: 本地语音文件的完整路径
            target_id: 用户 OpenID 或群 OpenID（可选，默认自动获取当前会话）
            is_group: 是否是群聊（可选，默认自动检测）
            msg_id: 被动回复的消息 ID（可选，默认自动获取当前会话）
        """
        adapter = self._get_adapter()
        if not adapter:
            return {"success": False, "error": "QQ 服务未启动"}

        if not str(voice_path).strip():
            return {"success": False, "error": "缺少 voice_path 参数"}
        voice_path_obj = Path(voice_path)
        if not voice_path_obj.exists():
            return {"success": False, "error": f"语音文件不存在: {voice_path}"}
        if not voice_path_obj.is_file():
            return {"success": False, "error": f"不是普通文件: {voice_path}"}

        target_id, is_group, msg_id, error = self._resolve_target_params(
            target_id, is_group, msg_id
        )
        if error:
            return {"success": False, "error": error}

        return self._send_media(adapter, target_id, voice_path, 3, is_group, msg_id, "语音")

    def _send_media(
        self,
        adapter,
        target_id: str,
        file_path: str,
        file_type: int,
        is_group: bool,
        msg_id: str,
        type_name: str
    ) -> Dict[str, Any]:
        """在独立事件循环中执行异步发送，并把平台错误原样带回"""
        import concurrent.futures

        def _run_in_new_loop(coro):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        try:
            coro = adapter.send_media_message(
                target_id, file_path, file_type,
                is_group=is_group, msg_id=msg_id
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_new_loop, coro)
                result = future.result(timeout=600)
        except concurrent.futures.TimeoutError:
            return {"success": False, "error": f"{type_name}发送超时（上传可能仍在后台进行）"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        if not isinstance(result, dict):
            return {"success": False, "error": f"{type_name}发送返回异常: {result}"}

        if result.get("success"):
            return {"success": True, "message": f"{type_name}发送成功"}

        error_msg = result.get("error") or f"{type_name}发送失败"
        if not msg_id:
            error_msg += "（缺少 msg_id，非被动回复的富媒体消息在群聊中受限）"
        return {"success": False, "error": error_msg}

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
