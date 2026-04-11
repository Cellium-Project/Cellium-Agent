# -*- coding: utf-8 -*-
"""
事件基类
统一事件结构，提供类型安全
"""

from dataclasses import dataclass, field
from typing import Any
from datetime import datetime
from .events import EventType


@dataclass
class BaseEvent:
    """事件基类"""
    event_type: EventType
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    data: dict = field(default_factory=dict)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取事件数据
        
        Args:
            key: 数据键
            default: 默认值
            
        Returns:
            数据值
        """
        return self.data.get(key, default)
    
    def __repr__(self):
        return f"<{self.__class__.__name__} type={self.event_type} data={self.data}>"


@dataclass
class NavigationEvent(BaseEvent):
    """导航事件"""
    
    def __init__(self, navigation_type: int, url: str):
        super().__init__(
            event_type=EventType.NAVIGATION,
            data={
                "navigation_type": navigation_type,
                "url": url
            }
        )
    
    @property
    def navigation_type(self) -> int:
        return self.data["navigation_type"]
    
    @property
    def url(self) -> str:
        return self.data["url"]


@dataclass
class AlertEvent(BaseEvent):
    """Alert 事件"""
    
    def __init__(self, message: str):
        super().__init__(
            event_type=EventType.ALERT,
            data={"message": message}
        )
    
    @property
    def message(self) -> str:
        return self.data["message"]


@dataclass
class JsQueryEvent(BaseEvent):
    """JsQuery 事件"""
    
    def __init__(self, webview, query_id: int, custom_msg: int, message: str):
        super().__init__(
            event_type=EventType.JSQUERY,
            data={
                "webview": webview,
                "query_id": query_id,
                "custom_msg": custom_msg,
                "message": message
            }
        )
    
    @property
    def webview(self):
        return self.data["webview"]
    
    @property
    def query_id(self) -> int:
        return self.data["query_id"]
    
    @property
    def custom_msg(self) -> int:
        return self.data["custom_msg"]
    
    @property
    def message(self) -> str:
        return self.data["message"]


@dataclass
class FadeOutEvent(BaseEvent):
    """淡出事件"""
    
    def __init__(self, duration: int = 300):
        super().__init__(
            event_type=EventType.FADE_OUT,
            data={"duration": duration}
        )
    
    @property
    def duration(self) -> int:
        return self.data["duration"]


@dataclass
class ButtonClickEvent(BaseEvent):
    """按钮点击事件"""
    
    def __init__(self, button_id: str, hwnd, event_type: str = "click"):
        super().__init__(
            event_type=EventType.BUTTON_CLICK,
            data={
                "button_id": button_id,
                "hwnd": hwnd,
                "event_type": event_type
            }
        )
    
    @property
    def button_id(self) -> str:
        return self.data["button_id"]
    
    @property
    def hwnd(self):
        return self.data["hwnd"]
    
    @property
    def event_type(self) -> str:
        return self.data["event_type"]


@dataclass
class CalcResultEvent(BaseEvent):
    """计算结果事件"""
    
    def __init__(self, result: str):
        super().__init__(
            event_type=EventType.CALC_RESULT,
            data={"result": result}
        )
    
    @property
    def result(self) -> str:
        return self.data["result"]


@dataclass
class SystemCommandEvent(BaseEvent):
    """系统命令事件"""
    
    def __init__(self, command: str, webview=None, query_id=None, custom_msg=None):
        data = {"command": command}
        if webview is not None:
            data["webview"] = webview
        if query_id is not None:
            data["query_id"] = query_id
        if custom_msg is not None:
            data["custom_msg"] = custom_msg
        
        super().__init__(
            event_type=EventType.SYSTEM_COMMAND,
            data=data
        )
    
    @property
    def command(self) -> str:
        return self.data["command"]
    
    @property
    def webview(self):
        return self.data.get("webview")
    
    @property
    def query_id(self):
        return self.data.get("query_id")
    
    @property
    def custom_msg(self):
        return self.data.get("custom_msg")
