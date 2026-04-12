# -*- coding: utf-8 -*-
"""
ChannelManager 测试脚本
验证多平台消息架构
"""

import asyncio
from app.channels import ChannelManager, QQAdapter
from app.channels.base import UnifiedMessage


async def test_channel_manager():
    print("[Test] Creating ChannelManager...")
    manager = ChannelManager.get_instance()

    print("[Test] Creating QQAdapter...")
    adapter = await create_qq_adapter_safe()

    def on_message(msg: UnifiedMessage):
        print(f"\n[Test] Received message: {msg.content}")
        print(f"  Platform: {msg.platform}")
        print(f"  User: {msg.user_id}")
        print(f"  Type: {msg.message_type}")
        print(f"  Session: {msg.session_id}")

        response = f"我收到了: {msg.content}"
        asyncio.create_task(manager.send_message(
            msg.platform, msg.user_id, response, msg.message_type, msg_id=msg.msg_id
        ))

    manager.set_global_handler(on_message)
    manager.register_adapter(adapter)

    print("[Test] Starting ChannelManager...")
    await manager.start_all()

    print("[Test] Waiting for messages... (Press Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n[Test] Stopping...")
        await manager.stop_all()


async def create_qq_adapter_safe():
    import os
    app_id = os.environ.get("QQ_BOT_APP_ID")
    app_secret = os.environ.get("QQ_BOT_APP_SECRET")
    if not app_id or not app_secret:
        raise ValueError("Please set QQ_BOT_APP_ID and QQ_BOT_APP_SECRET")
    return QQAdapter(app_id=app_id, app_secret=app_secret)


if __name__ == "__main__":
    asyncio.run(test_channel_manager())
