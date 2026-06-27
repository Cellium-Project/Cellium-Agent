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
        if platform == "qq":
            from app.channels.qq import QQChannelConfig
            qq_config = QQChannelConfig()
            await adapter.update_config(
                app_id=qq_config.get_app_id(force_reload=True),
                app_secret=qq_config.get_app_secret(force_reload=True),
            )
        elif platform == "telegram":
            from app.channels.telegram import TelegramChannelConfig
            tg_config = TelegramChannelConfig()
            await adapter.update_config(
                bot_token=tg_config.get_bot_token(force_reload=True),
                whitelist_user_ids=tg_config.get_whitelist_user_ids(force_reload=True),
                whitelist_usernames=tg_config.get_whitelist_usernames(force_reload=True),
            )
        elif platform == "feishu":
            from app.channels.feishu import FeishuChannelConfig
            feishu_config = FeishuChannelConfig()
            await adapter.update_config(
                app_id=feishu_config.get_app_id(force_reload=True),
                app_secret=feishu_config.get_app_secret(force_reload=True),
                whitelist_users=feishu_config.get_whitelist_users(force_reload=True),
            )
        elif platform == "weixin":
            from app.channels.weixin import WeixinChannelConfig
            weixin_config = WeixinChannelConfig()
            await adapter.update_config(
                state_dir=weixin_config.get_state_dir(force_reload=True),
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


@router.get("/weixin/qrcode")
async def get_weixin_qrcode() -> Dict[str, Any]:
    """获取微信登录二维码"""
    from app.channels import ChannelManager

    channel_mgr = ChannelManager.get_instance()
    adapter = channel_mgr.get_adapter("weixin")

    if not adapter:
        raise HTTPException(status_code=404, detail="微信通道未注册")

    client = getattr(adapter, "_client", None)
    if not client:
        raise HTTPException(status_code=500, detail="微信客户端未初始化")

    try:
        result = await client.login_qr_start()
        qrcode_url = result.get("qrcode_img_content", "")
        qrcode = result.get("qrcode", "")

        if not qrcode_url:
            raise HTTPException(status_code=500, detail="获取二维码失败")

        return {
            "status": "ok",
            "qrcode_url": qrcode_url,
            "qrcode": qrcode,
        }
    except Exception as e:
        logger.error(f"[ChannelAPI] 获取微信二维码失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weixin/qrcode/status")
async def get_weixin_qrcode_status(qrcode: str) -> Dict[str, Any]:
    """查询微信bot二维码扫码状态"""
    from app.channels import ChannelManager

    channel_mgr = ChannelManager.get_instance()
    adapter = channel_mgr.get_adapter("weixin")

    if not adapter:
        raise HTTPException(status_code=404, detail="微信通道未注册")

    client = getattr(adapter, "_client", None)
    if not client:
        raise HTTPException(status_code=500, detail="微信客户端未初始化")

    try:
        result = await client.login_qr_poll(qrcode)
        status = result.get("status", "wait")

        # 扫码确认后保存登录态
        if status == "confirmed":
            bot_token = result.get("bot_token", "")
            ilink_bot_id = result.get("ilink_bot_id", "")
            baseurl = result.get("baseurl", "")
            user_id = result.get("ilink_user_id", "")

            if ilink_bot_id and bot_token:
                account_id = ilink_bot_id.replace("@", "-").replace("/", "_")
                client.token = bot_token
                client.base_url = (baseurl or client.base_url).rstrip("/")
                client._account_id = account_id

                if client._account_store:
                    # 单账号模式：清空旧账号，只保留新账号
                    client._account_store.clear_all()
                    client._account_store.register(account_id)
                    client._account_store.save(account_id, token=bot_token, base_url=client.base_url, user_id=user_id)

                logger.info(f"[ChannelAPI] 微信登录成功: {account_id}")

        return {
            "status": "ok",
            "scan_status": status,
            "detail": result,
        }
    except Exception as e:
        logger.error(f"[ChannelAPI] 查询微信扫码状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qq/qrcode")
async def get_qq_qrcode() -> Dict[str, Any]:
    """获取 QQ 登录二维码"""
    from app.channels import ChannelManager

    channel_mgr = ChannelManager.get_instance()
    adapter = channel_mgr.get_adapter("qq")

    if not adapter:
        raise HTTPException(status_code=404, detail="QQ 通道未注册")

    try:
        result = await adapter.login_qr_start()
        return {
            "status": "ok",
            "qrcode_url": result.get("qrcode_url", ""),
            "task_id": result.get("task_id", ""),
            "key": result.get("key", ""),
        }
    except Exception as e:
        logger.error(f"[ChannelAPI] 获取 QQ 二维码失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qq/qrcode/status")
async def get_qq_qrcode_status(task_id: str, key: str) -> Dict[str, Any]:
    """查询 QQ 扫码状态"""
    from app.channels import ChannelManager
    import asyncio

    channel_mgr = ChannelManager.get_instance()
    adapter = channel_mgr.get_adapter("qq")

    if not adapter:
        raise HTTPException(status_code=404, detail="QQ 通道未注册")

    try:
        result = await adapter.login_qr_poll(task_id, key)
        scan_status = result.get("status", "waiting")
        
        if scan_status == "confirmed":
            app_id = result.get("app_id", "")
            app_secret = result.get("app_secret", "")
            if app_id and app_secret:
                adapter._save_credentials_to_config(app_id, app_secret)
                asyncio.create_task(_reload_qq_adapter(adapter, app_id, app_secret))
        
        return {
            "status": "ok",
            "scan_status": scan_status,
            "detail": result,
        }
    except Exception as e:
        logger.error(f"[ChannelAPI] 查询 QQ 扫码状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _reload_qq_adapter(adapter, app_id: str, app_secret: str):
    """后台重连 QQ 适配器"""
    import asyncio
    try:
        await adapter.update_config(app_id=app_id, app_secret=app_secret)
        await adapter.disconnect()
        await asyncio.sleep(1)
        await adapter.connect()
        logger.info("[ChannelAPI] QQ 适配器已重连")
    except Exception as e:
        logger.error(f"[ChannelAPI] QQ 重连失败: {e}")
