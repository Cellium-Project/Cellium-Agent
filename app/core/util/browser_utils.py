# -*- coding: utf-8 -*-
"""
浏览器路径自动检测工具

支持 Windows/macOS/Linux，自动查找 Edge、Chrome、Chromium 等浏览器路径
"""

import os
import platform
from typing import Optional, List

EDGE_PATHS_WINDOWS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

CHROME_PATHS_WINDOWS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
]

EDGE_PATHS_MACOS = [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Edge.app/Contents/MacOS/Edge",
]

CHROME_PATHS_MACOS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

EDGE_PATHS_LINUX = [
    "/usr/bin/microsoft-edge",
    "/usr/bin/edge",
    "/opt/microsoft/msedge/msedge",
]

CHROME_PATHS_LINUX = [
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


def get_system_browser_paths() -> List[str]:
    """根据操作系统返回可能的浏览器路径列表"""
    system = platform.system()
    
    if system == "Windows":
        return EDGE_PATHS_WINDOWS + CHROME_PATHS_WINDOWS
    elif system == "Darwin":  # macOS
        return EDGE_PATHS_MACOS + CHROME_PATHS_MACOS
    else:  # Linux
        return EDGE_PATHS_LINUX + CHROME_PATHS_LINUX


def find_browser_path() -> Optional[str]:
    """
    自动查找系统中可用的浏览器路径
    
    Returns:
        浏览器可执行文件路径，如果未找到返回 None
    """
    for path in get_system_browser_paths():
        if os.path.exists(path):
            return path
    return None


def get_chromium_options_with_browser() -> dict:
    """
    获取配置好的 ChromiumOptions 和浏览器路径
    
    Returns:
        dict: {
            'options': ChromiumOptions实例,
            'browser_path': str浏览器路径或None
        }
    """
    from DrissionPage import ChromiumOptions
    
    options = ChromiumOptions()
    browser_path = find_browser_path()
    
    if browser_path:
        options.set_browser_path(browser_path)
    
    return {
        'options': options,
        'browser_path': browser_path
    }
