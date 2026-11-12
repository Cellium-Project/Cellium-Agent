# -*- coding: utf-8 -*-
"""
浏览器路径自动检测工具

支持 Windows/macOS/Linux，自动查找 Edge、Chrome、Chromium 等浏览器路径
"""

import os
import logging
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Optional, List, Dict, Any, Iterator

from app.core.util.browser_runtime import get_runtime_browser_path

logger = logging.getLogger(__name__)

_probe_cache: Dict[str, tuple] = {}
_probe_lock = threading.Lock()
_PROBE_OK_TTL = 600
_PROBE_FAIL_TTL = 60

ENV_BROWSER_PATH = "CELLIUM_BROWSER_PATH"


def invalidate_browser_cache() -> None:
    """安装/更换浏览器后调用，让检测立即重新探测"""
    with _probe_lock:
        _probe_cache.clear()


def _probe_browser(path: str) -> bool:
    """探测浏览器是否可正常启动"""
    now = time.time()
    with _probe_lock:
        cached = _probe_cache.get(path)
    if cached is not None:
        ok, ts = cached
        ttl = _PROBE_OK_TTL if ok else _PROBE_FAIL_TTL
        if now - ts < ttl:
            return ok

    if platform.system() == "Windows":
        # Windows 下 chrome/edge 不支持 --version，只能起一个 headless 实例验证；
        # 用独立 user-data-dir，避免和正在运行的浏览器抢单例锁导致误判
        with tempfile.TemporaryDirectory(prefix="cellium-probe-") as profile:
            cmd = [
                path, "--headless", "--disable-gpu", "--no-first-run",
                f"--user-data-dir={profile}", "--dump-dom", "about:blank",
            ]
            timeout = 8
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                ok = result.returncode == 0
            except Exception:
                ok = False
    else:
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                timeout=6,
            )
            ok = result.returncode == 0
        except Exception:
            ok = False

    with _probe_lock:
        _probe_cache[path] = (ok, now)
    if not ok:
        logger.warning("[BrowserUtils] 浏览器探测失败（无法正常启动）: %s", path)
    return ok

EDGE_PATHS_WINDOWS = [
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
]

CHROME_PATHS_WINDOWS = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
]

# 测试版/开发版与便携版常见位置
EDGE_BETA_PATHS_WINDOWS = [
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge Beta\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge Dev\Application\msedge.exe"),
]

CHROME_BETA_PATHS_WINDOWS = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome Beta\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome SxS\Application\chrome.exe"),
]

EDGE_PATHS_MACOS = [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Edge.app/Contents/MacOS/Edge",
    os.path.expanduser("~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
]

CHROME_PATHS_MACOS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]

CHROMIUM_PATHS_MACOS = [
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

EDGE_PATHS_LINUX = [
    "/usr/bin/microsoft-edge",
    "/usr/bin/microsoft-edge-stable",
    "/usr/bin/microsoft-edge-beta",
    "/opt/microsoft/msedge/msedge",
    "/var/lib/flatpak/exports/bin/com.microsoft.Edge",
]

CHROME_PATHS_LINUX = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome-beta",
    "/snap/bin/chromium",
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


def iter_browser_candidates() -> Iterator[Dict[str, Any]]:
    """按优先级惰性产出可用浏览器候选，找到第一个即可短路，避免重复启动探测"""
    system = platform.system()
    seen = set()

    def _iter_one(path: Optional[str], name: str, source: str):
        if not path:
            return
        normalized = os.path.normpath(path)
        key = normalized.lower() if system == "Windows" else normalized
        if key in seen or not os.path.exists(normalized):
            return
        if not _probe_browser(normalized):
            return
        seen.add(key)
        yield {"path": normalized, "name": name, "source": source}

    env_path = os.environ.get(ENV_BROWSER_PATH)
    yield from _iter_one(env_path, "custom", "env")

    yield from _iter_one(get_runtime_browser_path(), "chromium", "runtime")

    if system == "Windows":
        for path in EDGE_PATHS_WINDOWS:
            yield from _iter_one(path, "edge", "system")
        for path in CHROME_PATHS_WINDOWS:
            yield from _iter_one(path, "chrome", "system")
        for path in EDGE_BETA_PATHS_WINDOWS:
            yield from _iter_one(path, "edge", "system")
        for path in CHROME_BETA_PATHS_WINDOWS:
            yield from _iter_one(path, "chrome", "system")
    elif system == "Darwin":
        for path in EDGE_PATHS_MACOS:
            yield from _iter_one(path, "edge", "system")
        for path in CHROME_PATHS_MACOS:
            yield from _iter_one(path, "chrome", "system")
        for path in CHROMIUM_PATHS_MACOS:
            yield from _iter_one(path, "chromium", "system")
    else:
        for path in EDGE_PATHS_LINUX:
            yield from _iter_one(path, "edge", "system")
        for path in CHROME_PATHS_LINUX:
            yield from _iter_one(path, "chrome" if "chrome" in path else "chromium", "system")

    for name, executable in BROWSER_NAMES_BY_PLATFORM.get(system, []):
        yield from _iter_one(shutil.which(executable), name, "path")


def get_browser_candidates() -> List[Dict[str, Any]]:
    """返回全部可用浏览器候选（按优先级排序）"""
    return list(iter_browser_candidates())


def find_browser_path() -> Optional[str]:
    """
    自动查找系统中可用的浏览器路径（找到第一个可用的就返回）

    Returns:
        浏览器可执行文件路径，如果未找到返回 None
    """
    for candidate in iter_browser_candidates():
        return candidate["path"]
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
