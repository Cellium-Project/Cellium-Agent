# -*- coding: utf-8 -*-
"""
事件类型定义
"""

from enum import Enum


class EventType(str, Enum):

    NAVIGATION = "navigation"
    
    ALERT = "alert"
    
    JSQUERY = "jsquery"
    
    FADE_OUT = "fade_out"
    WINDOW_RESIZE = "window_resize"
    WINDOW_MOVE = "window_move"
    
    BUTTON_CLICK = "button_click"

    CALC_RESULT = "calc_result"
    
    SYSTEM_COMMAND = "system_command"
    
    def __str__(self):
        return self.value
