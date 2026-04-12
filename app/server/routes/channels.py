# -*- coding: utf-8 -*-
"""
通道管理路由 - 提供通道热重载等 API
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/channels", tags=["channels"])

_client_logger = logging.getLogger("app.client")


class ClientLogEntry(BaseModel):
    level: str = "info"
    message: str
    data: Optional[Any] = None


@router.post("/client-log")
async def client_log(entry: ClientLogEntry):
    """接收前端 console.log 的日志"""
    if entry.level == "error":
        _client_logger.error(f"[Client] {entry.message}", extra={"data": entry.data})
    elif entry.level == "warning":
        _client_logger.warning(f"[Client] {entry.message}", extra={"data": entry.data})
    else:
        _client_logger.info(f"[Client] {entry.message}", extra={"data": entry.data})
    return {"status": "ok"}


class ChannelReloadRequest(BaseModel):
    platform: str = "qq"
    config: Optional[Dict[str, Any]] = None


@router.post("/reload")
async def reload_channel(platform: str = "qq") -> Dict[str, Any]:
    """
    热重载指定平台的通道连接

    - 如果通道已存在：断开并重新连接
    - 如果通道不存在：创建新连接
    """
    from app.channels import ChannelManager
    import asyncio

    channel_mgr = ChannelManager.get_instance()
    adapter = channel_mgr.get_adapter(platform)

    if not adapter:
        raise HTTPException(status_code=404, detail=f"通道 {platform} 未注册")

    try:
        from app.channels.qq_channel_config import QQChannelConfig
        qq_config = QQChannelConfig()
        await adapter.update_config(
            app_id=qq_config.get_app_id(force_reload=True),
            app_secret=qq_config.get_app_secret(force_reload=True),
        )
        await adapter.disconnect()
        asyncio.create_task(adapter.connect())
        logger.info(f"[ChannelAPI] 通道 {platform} 正在后台重连")
        return {"status": "ok", "platform": platform, "message": "正在重连中..."}
    except Exception as e:
        logger.error(f"[ChannelAPI] 热重载失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_channel_status() -> Dict[str, Any]:
    """获取所有通道的连接状态"""
    from app.channels import ChannelManager

    channel_mgr = ChannelManager.get_instance()
    platforms = channel_mgr.list_platforms()

    status = {}
    for platform in platforms:
        adapter = channel_mgr.get_adapter(platform)
        status[platform] = {
            "connected": getattr(adapter, "_running", False),
            "platform": platform,
        }

    return {
        "status": "ok",
        "platforms": status,
        "running": channel_mgr.is_running,
    }


@router.post("/stop")
async def stop_channels(platform: Optional[str] = None) -> Dict[str, Any]:
    """停止通道连接"""
    from app.channels import ChannelManager

    channel_mgr = ChannelManager.get_instance()

    if platform:
        adapter = channel_mgr.get_adapter(platform)
        if not adapter:
            raise HTTPException(status_code=404, detail=f"通道 {platform} 未注册")
        await adapter.disconnect()
        return {"status": "ok", "platform": platform, "message": "已停止"}
    else:
        await channel_mgr.stop_all()
        return {"status": "ok", "message": "所有通道已停止"}


@router.post("/start")
async def start_channels(platform: Optional[str] = None) -> Dict[str, Any]:
    """启动通道连接"""
    from app.channels import ChannelManager

    channel_mgr = ChannelManager.get_instance()

    if platform:
        adapter = channel_mgr.get_adapter(platform)
        if not adapter:
            raise HTTPException(status_code=404, detail=f"通道 {platform} 未注册")
        await adapter.connect()
        return {"status": "ok", "platform": platform, "message": "已启动"}
    else:
        await channel_mgr.start_all(with_queue=False)
        return {"status": "ok", "message": "所有通道已启动"}
