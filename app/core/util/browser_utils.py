# -*- coding: utf-8 -*-
"""
浏览器路径自动检测工具

支持 Windows/macOS/Linux，自动查找 Edge、Chrome、Chromium 等浏览器路径
"""

import os
import platform
import shutil
from typing import Optional, List, Dict, Any

from app.core.util.browser_runtime import get_runtime_browser_path

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

CHROMIUM_PATHS_MACOS = [
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
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

BROWSER_NAMES_BY_PLATFORM = {
    "Windows": [
        ("edge", "msedge.exe"),
        ("chrome", "chrome.exe"),
        ("chromium", "chromium.exe"),
    ],
    "Darwin": [
        ("edge", "Microsoft Edge"),
        ("chrome", "Google Chrome"),
        ("chromium", "Chromium"),
    ],
    "Linux": [
        ("edge", "microsoft-edge"),
        ("edge", "microsoft-edge-stable"),
        ("chrome", "google-chrome"),
        ("chrome", "google-chrome-stable"),
        ("chromium", "chromium-browser"),
        ("chromium", "chromium"),
    ],
}


def get_system_browser_paths() -> List[str]:
    """根据操作系统返回可能的浏览器路径列表"""
    system = platform.system()

    if system == "Windows":
        return EDGE_PATHS_WINDOWS + CHROME_PATHS_WINDOWS
    elif system == "Darwin":  # macOS
        return EDGE_PATHS_MACOS + CHROME_PATHS_MACOS + CHROMIUM_PATHS_MACOS
    else:  # Linux
        return EDGE_PATHS_LINUX + CHROME_PATHS_LINUX


def get_browser_candidates() -> List[Dict[str, Any]]:
    """返回按优先级排序的浏览器候选列表（含 bundled runtime、固定路径和 PATH 探测）"""
    system = platform.system()
    candidates: List[Dict[str, Any]] = []
    seen = set()

    def _add_candidate(path: Optional[str], name: str, source: str):
        if not path:
            return
        normalized = os.path.normpath(path)
        key = normalized.lower() if system == "Windows" else normalized
        if key in seen:
            return
        if not os.path.exists(normalized):
            return
        seen.add(key)
        candidates.append({"path": normalized, "name": name, "source": source})

    runtime_path = get_runtime_browser_path()
    _add_candidate(runtime_path, "chromium", "runtime")

    if system == "Windows":
        for path in EDGE_PATHS_WINDOWS:
            _add_candidate(path, "edge", "system")
        for path in CHROME_PATHS_WINDOWS:
            _add_candidate(path, "chrome", "system")
    elif system == "Darwin":
        for path in EDGE_PATHS_MACOS:
            _add_candidate(path, "edge", "system")
        for path in CHROME_PATHS_MACOS:
            _add_candidate(path, "chrome", "system")
        for path in CHROMIUM_PATHS_MACOS:
            _add_candidate(path, "chromium", "system")
    else:
        for path in EDGE_PATHS_LINUX:
            _add_candidate(path, "edge", "system")
        for path in CHROME_PATHS_LINUX:
            _add_candidate(path, "chrome" if "chrome" in path else "chromium", "system")

    for name, executable in BROWSER_NAMES_BY_PLATFORM.get(system, []):
        resolved = shutil.which(executable)
        _add_candidate(resolved, name, "path")

    return candidates


def find_browser_path() -> Optional[str]:
    """
    自动查找系统中可用的浏览器路径

    Returns:
        浏览器可执行文件路径，如果未找到返回 None
    """
    candidates = get_browser_candidates()
    return candidates[0]["path"] if candidates else None


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
